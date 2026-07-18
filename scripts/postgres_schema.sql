CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('passenger', 'driver', 'admin')),
    contact_info TEXT,
    government_id TEXT,
    passenger_photo TEXT,
    vehicle_details TEXT,
    driver_photo TEXT,
    vehicle_registration TEXT,
    vehicle_type TEXT,
    vehicle_color TEXT,
    is_verified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trips (
    id BIGSERIAL PRIMARY KEY,
    driver_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    route TEXT NOT NULL,
    travel_date TEXT NOT NULL,
    travel_time TEXT NOT NULL,
    available_seats INTEGER NOT NULL,
    price_per_seat DOUBLE PRECISION NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'cancelled', 'completed')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    passenger_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    seats INTEGER NOT NULL,
    pickup_location TEXT NOT NULL DEFAULT '',
    payment_method TEXT NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'pending',
    booking_status TEXT NOT NULL DEFAULT 'confirmed',
    payment_reference TEXT NOT NULL,
    receipt_number TEXT NOT NULL,
    tracking_token TEXT NOT NULL DEFAULT '',
    rating_token TEXT NOT NULL DEFAULT '',
    rating_score INTEGER,
    rating_comment TEXT,
    rated_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    booking_id BIGINT NOT NULL UNIQUE REFERENCES bookings(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    payment_type TEXT,
    bank_name TEXT,
    bank_account_name TEXT,
    bank_account_last4 TEXT,
    cardholder_name TEXT,
    card_brand TEXT,
    card_last4 TEXT,
    card_expiry_month TEXT,
    card_expiry_year TEXT,
    amount DOUBLE PRECISION NOT NULL,
    currency TEXT NOT NULL DEFAULT 'ZAR',
    commission_rate DOUBLE PRECISION,
    platform_commission_amount DOUBLE PRECISION,
    driver_net_amount DOUBLE PRECISION,
    payout_status TEXT NOT NULL DEFAULT 'pending',
    payout_reference TEXT,
    payout_requested_at TEXT,
    payout_paid_at TEXT,
    status TEXT NOT NULL,
    receipt_number TEXT NOT NULL,
    reference TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    read_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS disputes (
    id BIGSERIAL PRIMARY KEY,
    booking_id BIGINT NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    raised_by BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'resolved')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sent_emails (
    id BIGSERIAL PRIMARY KEY,
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    sent_via TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    booking_id BIGINT NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    sender_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receiver_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id BIGSERIAL PRIMARY KEY,
    admin_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id BIGINT,
    details TEXT,
    created_at TEXT NOT NULL
);
