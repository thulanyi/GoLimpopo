import importlib


def _set_session(client, user_id, token="test-csrf-token"):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["_csrf_token"] = token
    return token


def _seed_users(app_module):
    driver_password = app_module.generate_password_hash("Driver123!")
    passenger_password = app_module.generate_password_hash("Passenger123!")
    admin_password = app_module.generate_password_hash("Admin123!")

    driver_id = app_module.execute(
        """
        INSERT INTO users (name, email, password_hash, role, contact_info, is_verified, created_at)
        VALUES (?, ?, ?, 'driver', ?, 1, ?)
        """,
        ("Driver One", "driver@test.local", driver_password, "0710000001", app_module.now_iso()),
    ).lastrowid

    passenger_id = app_module.execute(
        """
        INSERT INTO users (name, email, password_hash, role, contact_info, is_verified, created_at)
        VALUES (?, ?, ?, 'passenger', ?, 1, ?)
        """,
        ("Passenger One", "passenger@test.local", passenger_password, "0710000002", app_module.now_iso()),
    ).lastrowid

    admin_id = app_module.execute(
        """
        INSERT INTO users (name, email, password_hash, role, contact_info, is_verified, created_at)
        VALUES (?, ?, ?, 'admin', ?, 1, ?)
        """,
        ("Admin One", "admin@test.local", admin_password, "System", app_module.now_iso()),
    ).lastrowid

    return driver_id, passenger_id, admin_id


def _seed_paid_booking(app_module, driver_id, passenger_id, *, payout_status="pending", created_at=None):
    created_at = created_at or app_module.now_iso()
    trip_id = app_module.execute(
        """
        INSERT INTO trips (driver_id, route, travel_date, travel_time, available_seats, price_per_seat, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (driver_id, "Pretoria to Giyani", "2026-07-17", "08:00", 3, 500.0, created_at),
    ).lastrowid

    booking_id = app_module.execute(
        """
        INSERT INTO bookings (
            trip_id, passenger_id, seats, pickup_location, payment_method, payment_status,
            booking_status, payment_reference, receipt_number, tracking_token, rating_token, created_at
        )
        VALUES (?, ?, 1, ?, 'Card', 'paid', 'confirmed', ?, ?, ?, ?, ?)
        """,
        (
            trip_id,
            passenger_id,
            "Hatfield",
            "PAY-TEST-001",
            "RCPT-TEST-001",
            "tracking-token-1",
            "rating-token-1",
            app_module.now_iso(),
        ),
    ).lastrowid

    commission_rate = app_module.get_commission_percent()
    platform_commission, driver_net = app_module.calculate_split(500.0, commission_rate)

    payment_id = app_module.execute(
        """
        INSERT INTO payments (
            booking_id, provider, amount, currency, commission_rate,
            platform_commission_amount, driver_net_amount, payout_status,
            status, receipt_number, reference, created_at
        )
        VALUES (?, 'Stripe', 500.0, 'ZAR', ?, ?, ?, ?, 'paid', ?, ?, ?)
        """,
        (
            booking_id,
            commission_rate,
            platform_commission,
            driver_net,
            payout_status,
            "RCPT-TEST-001",
            "PAY-TEST-001",
            created_at,
        ),
    ).lastrowid

    return trip_id, booking_id, payment_id


def test_calculate_split_rounding(monkeypatch, tmp_path):
    db_path = tmp_path / "test_split.sqlite"
    monkeypatch.setenv("MAGAYISA_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MAGAYISA_ADMIN_EMAIL", "root@test.local")
    monkeypatch.setenv("MAGAYISA_ADMIN_PASSWORD", "Admin123!")

    import app as app_module

    app_module = importlib.reload(app_module)

    platform_commission, driver_net = app_module.calculate_split(123.45, 12.5)
    assert platform_commission == 15.43
    assert driver_net == 108.02


def test_cancel_trip_refunds_and_reverses_commission(monkeypatch, tmp_path):
    db_path = tmp_path / "test_cancel.sqlite"
    monkeypatch.setenv("MAGAYISA_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MAGAYISA_ADMIN_EMAIL", "root@test.local")
    monkeypatch.setenv("MAGAYISA_ADMIN_PASSWORD", "Admin123!")

    import app as app_module

    app_module = importlib.reload(app_module)
    app_module.app.config["TESTING"] = True

    with app_module.app.app_context():
        driver_id, passenger_id, _ = _seed_users(app_module)
        trip_id, booking_id, payment_id = _seed_paid_booking(app_module, driver_id, passenger_id)

    client = app_module.app.test_client()
    csrf = _set_session(client, driver_id)
    response = client.post(f"/driver/trips/{trip_id}/cancel", data={"_csrf_token": csrf})
    assert response.status_code == 302

    with app_module.app.app_context():
        payment = app_module.query_one("SELECT * FROM payments WHERE id = ?", (payment_id,))
        booking = app_module.query_one("SELECT * FROM bookings WHERE id = ?", (booking_id,))
        assert payment["status"] == "refunded"
        assert float(payment["platform_commission_amount"] or 0) == 0.0
        assert float(payment["driver_net_amount"] or 0) == 0.0
        assert payment["payout_status"] == "cancelled"
        assert booking["booking_status"] == "cancelled"
        assert booking["payment_status"] == "refunded"


def test_admin_payout_approval_logs_audit_and_reconciliation_export(monkeypatch, tmp_path):
    db_path = tmp_path / "test_payout.sqlite"
    monkeypatch.setenv("MAGAYISA_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MAGAYISA_ADMIN_EMAIL", "root@test.local")
    monkeypatch.setenv("MAGAYISA_ADMIN_PASSWORD", "Admin123!")

    import app as app_module

    app_module = importlib.reload(app_module)
    app_module.app.config["TESTING"] = True

    with app_module.app.app_context():
        driver_id, passenger_id, admin_id = _seed_users(app_module)
        _trip_id, _booking_id, payment_id = _seed_paid_booking(
            app_module,
            driver_id,
            passenger_id,
            payout_status="ready",
            created_at="2026-07-17T09:00:00",
        )

    client = app_module.app.test_client()
    csrf = _set_session(client, admin_id)
    approve_response = client.post(f"/admin/payouts/{payment_id}/approve", data={"_csrf_token": csrf})
    assert approve_response.status_code == 302

    recon_response = client.get("/admin/reconciliation/export?date=2026-07-17")
    assert recon_response.status_code == 200
    csv_text = recon_response.data.decode("utf-8")
    assert "reconciliation_date,2026-07-17" in csv_text
    assert "summary,paid_total,500.00" in csv_text

    with app_module.app.app_context():
        payment = app_module.query_one("SELECT * FROM payments WHERE id = ?", (payment_id,))
        payout_logs = app_module.query_all(
            "SELECT * FROM admin_audit_logs WHERE action = 'payout_marked_paid'"
        )
        recon_logs = app_module.query_all(
            "SELECT * FROM admin_audit_logs WHERE action = 'daily_reconciliation_export'"
        )
        assert payment["payout_status"] == "paid"
        assert payout_logs
        assert recon_logs


def test_beta_traffic_cap_blocks_new_visitors_after_limit(monkeypatch, tmp_path):
    db_path = tmp_path / "test_beta_cap.sqlite"
    monkeypatch.setenv("MAGAYISA_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MAGAYISA_ADMIN_EMAIL", "root@test.local")
    monkeypatch.setenv("MAGAYISA_ADMIN_PASSWORD", "Admin123!")
    monkeypatch.setenv("MAGAYISA_BETA_MODE", "1")
    monkeypatch.setenv("MAGAYISA_BETA_MAX_DAILY_ACTIVE_USERS", "1")
    monkeypatch.setenv("MAGAYISA_BETA_ACTIVITY_WINDOW_SECONDS", "86400")

    import app as app_module

    app_module = importlib.reload(app_module)
    app_module.app.config["TESTING"] = True

    first_client = app_module.app.test_client()
    first_client.environ_base["REMOTE_ADDR"] = "10.1.1.1"
    first_response = first_client.get("/")
    assert first_response.status_code == 200

    second_client = app_module.app.test_client()
    second_client.environ_base["REMOTE_ADDR"] = "10.1.1.2"
    second_response = second_client.get("/")
    assert second_response.status_code == 503


def test_trip_booking_defaults_to_payfast_pending_and_redirects_to_checkout(monkeypatch, tmp_path):
    db_path = tmp_path / "test_payfast_default.sqlite"
    monkeypatch.setenv("MAGAYISA_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MAGAYISA_ADMIN_EMAIL", "root@test.local")
    monkeypatch.setenv("MAGAYISA_ADMIN_PASSWORD", "Admin123!")
    monkeypatch.delenv("PAYFAST_MERCHANT_ID", raising=False)
    monkeypatch.delenv("PAYFAST_MERCHANT_KEY", raising=False)
    monkeypatch.setenv("PAYFAST_TEST_MODE_FALLBACK", "1")

    import app as app_module

    app_module = importlib.reload(app_module)
    app_module.app.config["TESTING"] = True

    with app_module.app.app_context():
        driver_id, passenger_id, _admin_id = _seed_users(app_module)
        trip_id = app_module.execute(
            """
            INSERT INTO trips (driver_id, route, travel_date, travel_time, available_seats, price_per_seat, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (driver_id, "Pretoria to Giyani", "2026-07-17", "08:00", 5, 250.0, app_module.now_iso()),
        ).lastrowid

    client = app_module.app.test_client()
    csrf = _set_session(client, passenger_id)
    response = client.post(
        f"/trips/{trip_id}",
        data={
            "_csrf_token": csrf,
            "seats": "1",
            "provider": "PayFast",
            "pickup_location": "Hatfield",
        },
    )
    assert response.status_code == 302
    assert "/bookings/" in response.location
    assert "/payfast/start" in response.location

    with app_module.app.app_context():
        booking = app_module.query_one("SELECT * FROM bookings ORDER BY id DESC LIMIT 1")
        payment = app_module.get_payment_for_booking(booking["id"])
        assert booking["payment_method"] == "PayFast"
        assert booking["payment_status"] == "pending"
        assert payment["provider"] == "PayFast"
        assert payment["status"] == "pending"


def test_payfast_itn_marks_payment_paid(monkeypatch, tmp_path):
    db_path = tmp_path / "test_payfast_itn.sqlite"
    monkeypatch.setenv("MAGAYISA_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MAGAYISA_ADMIN_EMAIL", "root@test.local")
    monkeypatch.setenv("MAGAYISA_ADMIN_PASSWORD", "Admin123!")
    monkeypatch.setenv("PAYFAST_MERCHANT_ID", "10000100")
    monkeypatch.setenv("PAYFAST_MERCHANT_KEY", "46f0cd694581a")
    monkeypatch.setenv("PAYFAST_PASSPHRASE", "passphrase")

    import app as app_module

    app_module = importlib.reload(app_module)
    app_module.app.config["TESTING"] = True

    with app_module.app.app_context():
        driver_id, passenger_id, _admin_id = _seed_users(app_module)
        _trip_id, booking_id, _payment_id = _seed_paid_booking(app_module, driver_id, passenger_id, payout_status="pending")
        app_module.execute("UPDATE payments SET status = 'pending', payment_type = NULL WHERE booking_id = ?", (booking_id,))
        app_module.execute("UPDATE bookings SET payment_status = 'pending', payment_method = 'PayFast' WHERE id = ?", (booking_id,))

    with app_module.app.app_context():
        booking = app_module.get_booking(booking_id)
        payment = app_module.get_payment_for_booking(booking_id)
        payload = app_module.build_payfast_payload(booking, payment)
        payload["payment_status"] = "COMPLETE"
        payload["amount_gross"] = f"{float(payment['amount']):.2f}"
        payload["pf_payment_id"] = "PF123456789"
        payload["signature"] = app_module.payfast_signature_for_payload(payload)

    client = app_module.app.test_client()
    response = client.post("/payfast/itn", data=payload)
    assert response.status_code == 200

    with app_module.app.app_context():
        payment = app_module.get_payment_for_booking(booking_id)
        booking = app_module.query_one("SELECT * FROM bookings WHERE id = ?", (booking_id,))
        assert payment["status"] == "paid"
        assert payment["payment_type"] == "payfast"
        assert booking["payment_status"] == "paid"
