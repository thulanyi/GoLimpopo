import os
import re
import csv
import secrets
import sqlite3
import smtplib
import ssl
import time
import imghdr
import logging
import hashlib
from io import StringIO
from datetime import datetime
from collections import defaultdict, deque
from functools import wraps
from email.message import EmailMessage
from urllib.parse import quote_plus

from flask import Flask, Response, abort, flash, g, has_request_context, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
DATABASE = os.environ.get("MAGAYISA_DATABASE_PATH", os.path.join(INSTANCE_DIR, "golimpopo.sqlite"))
POSTGRES_DSN = os.environ.get("MAGAYISA_POSTGRES_DSN", "").strip()
DATABASE_BACKEND = "postgres" if POSTGRES_DSN else "sqlite"


def env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


IS_PRODUCTION = env_flag("MAGAYISA_PRODUCTION", os.environ.get("FLASK_ENV", "").lower() == "production")

os.makedirs(INSTANCE_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=os.environ.get("MAGAYISA_SESSION_SAMESITE", "Lax"),
    SESSION_COOKIE_SECURE=env_flag("MAGAYISA_SESSION_COOKIE_SECURE", IS_PRODUCTION),
    PREFERRED_URL_SCHEME="https" if IS_PRODUCTION else "http",
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
)
app.config["DEBUG"] = env_flag("MAGAYISA_DEBUG", not IS_PRODUCTION)

if env_flag("MAGAYISA_TRUST_PROXY", IS_PRODUCTION):
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=env_int("MAGAYISA_PROXY_X_FOR", 1),
        x_proto=env_int("MAGAYISA_PROXY_X_PROTO", 1),
        x_host=env_int("MAGAYISA_PROXY_X_HOST", 1),
        x_port=env_int("MAGAYISA_PROXY_X_PORT", 1),
    )

DEFAULT_COMMISSION_PERCENT = 10.0
UPLOAD_ALLOWED_TYPES = {"jpeg", "png", "webp"}
RATE_LIMIT_BUCKETS = defaultdict(deque)
BETA_ACTIVITY_BUCKET = {}
BETA_MODE = env_flag("MAGAYISA_BETA_MODE", False)
BETA_MAX_DAILY_ACTIVE_USERS = env_int("MAGAYISA_BETA_MAX_DAILY_ACTIVE_USERS", 200)
BETA_ACTIVITY_WINDOW_SECONDS = env_int("MAGAYISA_BETA_ACTIVITY_WINDOW_SECONDS", 86400)
REDIS_URL = os.environ.get("MAGAYISA_REDIS_URL", "").strip()
REDIS_RATE_LIMIT_PREFIX = "magayisa:ratelimit"
redis_client = None
PAYFAST_SANDBOX = env_flag("PAYFAST_SANDBOX", True)
PAYFAST_MERCHANT_ID = os.environ.get("PAYFAST_MERCHANT_ID", "").strip()
PAYFAST_MERCHANT_KEY = os.environ.get("PAYFAST_MERCHANT_KEY", "").strip()
PAYFAST_PASSPHRASE = os.environ.get("PAYFAST_PASSPHRASE", "").strip()
if PAYFAST_SANDBOX and not PAYFAST_MERCHANT_ID:
    PAYFAST_MERCHANT_ID = "10000100"
if PAYFAST_SANDBOX and not PAYFAST_MERCHANT_KEY:
    PAYFAST_MERCHANT_KEY = "46f0cd694581a"
PAYFAST_SIGN_REQUESTS = env_flag("PAYFAST_SIGN_REQUESTS", False)
PAYFAST_TEST_MODE_FALLBACK = env_flag("PAYFAST_TEST_MODE_FALLBACK", True)
PUBLIC_BASE_URL = os.environ.get("MAGAYISA_PUBLIC_BASE_URL", "").rstrip("/")

logging.basicConfig(level=logging.INFO if IS_PRODUCTION else logging.DEBUG)
logger = logging.getLogger("magayisa")


def init_monitoring():
    sentry_dsn = os.environ.get("MAGAYISA_SENTRY_DSN", "").strip()
    if not sentry_dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=sentry_dsn,
            traces_sample_rate=float(os.environ.get("MAGAYISA_SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            environment="production" if IS_PRODUCTION else "development",
        )
        logger.info("Sentry monitoring initialized.")
    except Exception as exc:
        logger.warning("Sentry initialization failed: %s", exc)


init_monitoring()


class ExecuteResult:
    def __init__(self, lastrowid=None):
        self.lastrowid = lastrowid


POSTGRES_ID_TABLES = {
    "users",
    "trips",
    "bookings",
    "payments",
    "notifications",
    "disputes",
    "sent_emails",
    "chat_messages",
    "admin_audit_logs",
}


def adapt_sql_for_backend(sql):
    if DATABASE_BACKEND == "postgres":
        return sql.replace("?", "%s")
    return sql


def extract_insert_table(sql):
    match = re.match(r"\s*INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, flags=re.IGNORECASE)
    return match.group(1).lower() if match else None


def get_db():
    if "db" not in g:
        if DATABASE_BACKEND == "postgres":
            import psycopg
            from psycopg.rows import dict_row

            connection = psycopg.connect(POSTGRES_DSN, row_factory=dict_row)
            g.db = connection
        else:
            connection = sqlite3.connect(DATABASE)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            g.db = connection
    return g.db


def query_one(sql, params=()):
    db = get_db()
    adapted_sql = adapt_sql_for_backend(sql)
    if DATABASE_BACKEND == "postgres":
        with db.cursor() as cursor:
            cursor.execute(adapted_sql, params)
            return cursor.fetchone()
    return db.execute(adapted_sql, params).fetchone()


def query_all(sql, params=()):
    db = get_db()
    adapted_sql = adapt_sql_for_backend(sql)
    if DATABASE_BACKEND == "postgres":
        with db.cursor() as cursor:
            cursor.execute(adapted_sql, params)
            return cursor.fetchall()
    return db.execute(adapted_sql, params).fetchall()


def execute(sql, params=()):
    db = get_db()
    adapted_sql = adapt_sql_for_backend(sql)
    if DATABASE_BACKEND == "postgres":
        lastrowid = None
        sql_to_run = adapted_sql
        table_name = extract_insert_table(sql)
        if table_name in POSTGRES_ID_TABLES and "RETURNING" not in adapted_sql.upper():
            sql_to_run = f"{adapted_sql.rstrip().rstrip(';')} RETURNING id"
        with db.cursor() as cursor:
            cursor.execute(sql_to_run, params)
            if "RETURNING" in sql_to_run.upper():
                row = cursor.fetchone()
                if row is not None:
                    lastrowid = row["id"] if isinstance(row, dict) else row[0]
        db.commit()
        return ExecuteResult(lastrowid=lastrowid)

    cursor = db.execute(adapted_sql, params)
    db.commit()
    return cursor


@app.teardown_appcontext
def close_db(_exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


def parse_iso_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def get_rate_limit_client():
    global redis_client
    if redis_client is not None:
        return redis_client
    if not REDIS_URL:
        return None
    try:
        import redis

        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()
        return redis_client
    except Exception as exc:
        logger.warning("Redis unavailable, using in-memory limiter: %s", exc)
        redis_client = None
        return None


def is_rate_limited(action, limit, window_seconds):
    now_ts = time.time()
    bucket_key = f"{action}:{get_client_ip()}"
    client = get_rate_limit_client()
    if client is not None:
        redis_key = f"{REDIS_RATE_LIMIT_PREFIX}:{bucket_key}"
        now_ms = int(now_ts * 1000)
        window_start_ms = int((now_ts - window_seconds) * 1000)
        try:
            pipeline = client.pipeline()
            pipeline.zremrangebyscore(redis_key, 0, window_start_ms)
            pipeline.zcard(redis_key)
            _removed, count = pipeline.execute()
            if int(count) >= limit:
                return True
            pipeline = client.pipeline()
            pipeline.zadd(redis_key, {str(now_ms): now_ms})
            pipeline.expire(redis_key, max(window_seconds, 1))
            pipeline.execute()
            return False
        except Exception as exc:
            logger.warning("Redis limiter failed, fallback to local bucket: %s", exc)

    bucket = RATE_LIMIT_BUCKETS[bucket_key]
    while bucket and now_ts - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now_ts)
    return False


def enforce_beta_capacity(user):
    if not BETA_MODE:
        return None
    if user is not None and user["role"] == "admin":
        return None

    now_ts = time.time()
    identity = f"user:{user['id']}" if user is not None else f"ip:{get_client_ip()}"
    stale_keys = [
        key
        for key, seen_ts in BETA_ACTIVITY_BUCKET.items()
        if now_ts - seen_ts > BETA_ACTIVITY_WINDOW_SECONDS
    ]
    for key in stale_keys:
        BETA_ACTIVITY_BUCKET.pop(key, None)

    if identity not in BETA_ACTIVITY_BUCKET and len(BETA_ACTIVITY_BUCKET) >= BETA_MAX_DAILY_ACTIVE_USERS:
        return (
            render_template(
                "base_error.html",
                title="Beta Full",
                message="Magayisa beta access is at capacity today. Please try again tomorrow.",
            ),
            503,
        )

    BETA_ACTIVITY_BUCKET[identity] = now_ts
    return None


def get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(24)
        session["_csrf_token"] = token
    return token


def calculate_split(amount, commission_percent):
    amount = float(amount or 0)
    commission_percent = max(0.0, min(float(commission_percent or 0), 100.0))
    platform_commission = round(amount * (commission_percent / 100.0), 2)
    driver_net = round(amount - platform_commission, 2)
    return platform_commission, driver_net


def normalize_provider(raw_provider):
    provider = (raw_provider or "").strip().lower()
    if provider in {"payfast", "pay fast"}:
        return "PayFast"
    if provider in {"stripe"}:
        return "Stripe"
    if provider in {"flutterwave"}:
        return "Flutterwave"
    return "PayFast"


def build_external_url(endpoint, **values):
    if PUBLIC_BASE_URL:
        relative = url_for(endpoint, **values)
        return f"{PUBLIC_BASE_URL}{relative}"
    if has_request_context():
        return url_for(endpoint, _external=True, **values)
    with app.test_request_context(base_url="http://127.0.0.1:5000"):
        return url_for(endpoint, _external=True, **values)


def is_payfast_configured():
    return bool(PAYFAST_MERCHANT_ID and PAYFAST_MERCHANT_KEY)


def payfast_url():
    if PAYFAST_SANDBOX:
        return "https://sandbox.payfast.co.za/eng/process"
    return "https://www.payfast.co.za/eng/process"


def payfast_signature_for_payload(payload):
    pairs = []
    for key in sorted(payload.keys()):
        value = payload[key]
        if value is None or value == "" or key == "signature":
            continue
        encoded = quote_plus(str(value).strip())
        pairs.append(f"{key}={encoded}")
    data = "&".join(pairs)
    if PAYFAST_PASSPHRASE:
        data = f"{data}&passphrase={quote_plus(PAYFAST_PASSPHRASE)}"
    return hashlib.md5(data.encode("utf-8")).hexdigest()


def build_payfast_payload(booking, payment):
    passenger_email = (booking["passenger_email"] or "").strip().lower()
    # PayFast rejects malformed emails in checkout payload; keep a safe fallback.
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", passenger_email):
        passenger_email = "customer@example.com"

    amount = f"{float(payment['amount'] or 0):.2f}"
    payload = {
        "merchant_id": PAYFAST_MERCHANT_ID,
        "merchant_key": PAYFAST_MERCHANT_KEY,
        "return_url": build_external_url("payfast_return", booking_id=booking["id"]),
        "cancel_url": build_external_url("payfast_cancel", booking_id=booking["id"]),
        "notify_url": build_external_url("payfast_itn"),
        "name_first": (booking["passenger_name"] or "Passenger").split(" ")[0],
        "name_last": " ".join((booking["passenger_name"] or "Passenger").split(" ")[1:]) or "Passenger",
        "email_address": passenger_email,
        "m_payment_id": str(booking["id"]),
        "amount": amount,
        "item_name": f"Magayisa trip #{booking['id']}",
        "item_description": f"{booking['route']} | {booking['travel_date']} {booking['travel_time']}",
        "custom_str1": payment["receipt_number"],
    }
    if PAYFAST_SIGN_REQUESTS:
        payload["signature"] = payfast_signature_for_payload(payload)
    return payload


def mark_booking_payment_paid(booking_id, payment, payment_type="payfast", reference=None):
    commission_percent = get_commission_percent()
    platform_commission, driver_net = calculate_split(payment["amount"], commission_percent)
    execute(
        """
        UPDATE payments
        SET payment_type = ?, commission_rate = ?, platform_commission_amount = ?, driver_net_amount = ?,
            status = 'paid', reference = COALESCE(?, reference)
        WHERE booking_id = ?
        """,
        (
            payment_type,
            commission_percent,
            platform_commission,
            driver_net,
            reference,
            booking_id,
        ),
    )
    execute(
        "UPDATE bookings SET payment_method = ?, payment_status = 'paid' WHERE id = ?",
        (payment_type.capitalize(), booking_id),
    )


def mark_booking_payment_failed(booking_id):
    execute(
        """
        UPDATE payments
        SET status = 'failed', platform_commission_amount = 0, driver_net_amount = 0
        WHERE booking_id = ? AND status != 'paid'
        """,
        (booking_id,),
    )
    execute(
        "UPDATE bookings SET payment_status = 'pending' WHERE id = ? AND payment_status != 'paid'",
        (booking_id,),
    )


def ensure_default_admin():
    admin_email = os.environ.get("MAGAYISA_ADMIN_EMAIL") or os.environ.get("GOLIMPOPO_ADMIN_EMAIL")
    admin_password = os.environ.get("MAGAYISA_ADMIN_PASSWORD") or os.environ.get("GOLIMPOPO_ADMIN_PASSWORD")
    if IS_PRODUCTION and (not admin_email or not admin_password):
        raise RuntimeError(
            "MAGAYISA_ADMIN_EMAIL and MAGAYISA_ADMIN_PASSWORD are required in production."
        )
    if not admin_email:
        admin_email = "admin@magayisa.local"
    if not admin_password:
        admin_password = "Admin123!"
    existing = query_one("SELECT id FROM users WHERE email = ?", (admin_email,))
    if existing is None:
        execute(
            """
            INSERT INTO users (name, email, password_hash, role, contact_info, is_verified, created_at)
            VALUES (?, ?, ?, 'admin', ?, 1, ?)
            """,
            (
                "System Admin",
                admin_email,
                generate_password_hash(admin_password),
                "System console",
                now_iso(),
            ),
        )


def init_db():
    schema = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        driver_id INTEGER NOT NULL,
        route TEXT NOT NULL,
        travel_date TEXT NOT NULL,
        travel_time TEXT NOT NULL,
        available_seats INTEGER NOT NULL,
        price_per_seat REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'cancelled', 'completed')),
        created_at TEXT NOT NULL,
        FOREIGN KEY(driver_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trip_id INTEGER NOT NULL,
        passenger_id INTEGER NOT NULL,
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
        created_at TEXT NOT NULL,
        FOREIGN KEY(trip_id) REFERENCES trips(id) ON DELETE CASCADE,
        FOREIGN KEY(passenger_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL UNIQUE,
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
        amount REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'ZAR',
        commission_rate REAL,
        platform_commission_amount REAL,
        driver_net_amount REAL,
        payout_status TEXT NOT NULL DEFAULT 'pending',
        payout_reference TEXT,
        payout_requested_at TEXT,
        payout_paid_at TEXT,
        status TEXT NOT NULL,
        receipt_number TEXT NOT NULL,
        reference TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(booking_id) REFERENCES bookings(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        read_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS disputes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL,
        raised_by INTEGER NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'resolved')),
        created_at TEXT NOT NULL,
        FOREIGN KEY(booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
        FOREIGN KEY(raised_by) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS sent_emails (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        receiver_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
        FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(receiver_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS admin_audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id INTEGER,
        details TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(admin_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """

    db = sqlite3.connect(DATABASE)
    db.executescript(schema)
    ensure_user_columns(db)
    ensure_booking_columns(db)
    ensure_app_settings(db)
    ensure_payment_columns(db)
    ensure_notification_columns(db)
    ensure_admin_audit_schema(db)
    ensure_trip_status_schema(db)
    ensure_trip_completion_consistency(db)
    db.commit()
    db.close()
    ensure_default_admin()


def ensure_user_columns(db):
    existing_columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
    for column_name in ("passenger_photo", "driver_photo", "vehicle_registration", "vehicle_type", "vehicle_color"):
        if column_name not in existing_columns:
            db.execute(f"ALTER TABLE users ADD COLUMN {column_name} TEXT")


def ensure_booking_columns(db):
    existing_columns = {
        row[1] for row in db.execute("PRAGMA table_info(bookings)").fetchall()
    }
    if "pickup_location" not in existing_columns:
        db.execute("ALTER TABLE bookings ADD COLUMN pickup_location TEXT NOT NULL DEFAULT ''")
    if "tracking_token" not in existing_columns:
        db.execute("ALTER TABLE bookings ADD COLUMN tracking_token TEXT NOT NULL DEFAULT ''")
    if "rating_token" not in existing_columns:
        db.execute("ALTER TABLE bookings ADD COLUMN rating_token TEXT NOT NULL DEFAULT ''")
    if "rating_score" not in existing_columns:
        db.execute("ALTER TABLE bookings ADD COLUMN rating_score INTEGER")
    if "rating_comment" not in existing_columns:
        db.execute("ALTER TABLE bookings ADD COLUMN rating_comment TEXT")
    if "rated_at" not in existing_columns:
        db.execute("ALTER TABLE bookings ADD COLUMN rated_at TEXT")
    rows = db.execute("SELECT id, pickup_location, tracking_token FROM bookings").fetchall()
    for row in rows:
        if not row[1]:
            db.execute(
                "UPDATE bookings SET pickup_location = ? WHERE id = ?",
                ("Not provided", row[0]),
            )
        if not row[2]:
            db.execute(
                "UPDATE bookings SET tracking_token = ? WHERE id = ?",
                (secrets.token_urlsafe(24), row[0]),
            )
        rating_token = db.execute("SELECT rating_token FROM bookings WHERE id = ?", (row[0],)).fetchone()[0]
        if not rating_token:
            db.execute(
                "UPDATE bookings SET rating_token = ? WHERE id = ?",
                (secrets.token_urlsafe(24), row[0]),
            )


def ensure_payment_columns(db):
    existing_columns = {row[1] for row in db.execute("PRAGMA table_info(payments)").fetchall()}
    for column_name in (
        "payment_type",
        "bank_name",
        "bank_account_name",
        "bank_account_last4",
        "cardholder_name",
        "card_brand",
        "card_last4",
        "card_expiry_month",
        "card_expiry_year",
        "commission_rate",
        "platform_commission_amount",
        "driver_net_amount",
        "payout_status",
        "payout_reference",
        "payout_requested_at",
        "payout_paid_at",
    ):
        if column_name not in existing_columns:
            if column_name in {"commission_rate", "platform_commission_amount", "driver_net_amount"}:
                db.execute(f"ALTER TABLE payments ADD COLUMN {column_name} REAL")
            elif column_name == "payout_status":
                db.execute("ALTER TABLE payments ADD COLUMN payout_status TEXT NOT NULL DEFAULT 'pending'")
            else:
                db.execute(f"ALTER TABLE payments ADD COLUMN {column_name} TEXT")

    commission_percent = get_commission_percent_db(db)
    rows = db.execute(
        """
        SELECT id, amount, status, commission_rate, platform_commission_amount, driver_net_amount
        FROM payments
        """
    ).fetchall()
    for row in rows:
        commission_rate = row[3] if row[3] is not None else commission_percent
        platform_commission, driver_net = calculate_split(row[1], commission_rate)
        if row[2] != "paid":
            platform_commission = 0.0
            driver_net = 0.0
        if row[3] is None or row[4] is None or row[5] is None:
            db.execute(
                """
                UPDATE payments
                SET commission_rate = ?, platform_commission_amount = ?, driver_net_amount = ?
                WHERE id = ?
                """,
                (commission_rate, platform_commission, driver_net, row[0]),
            )
    db.execute(
        """
        UPDATE payments
        SET payout_status = CASE
            WHEN status = 'refunded' THEN 'cancelled'
            WHEN payout_status IS NULL OR payout_status = '' THEN 'pending'
            ELSE payout_status
        END
        """
    )


def ensure_app_settings(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    existing = db.execute(
        "SELECT value FROM app_settings WHERE key = 'commission_percent'"
    ).fetchone()
    if existing is None:
        db.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES ('commission_percent', ?, ?)",
            (str(DEFAULT_COMMISSION_PERCENT), now_iso()),
        )
        return
    try:
        percent = float(existing[0])
    except (TypeError, ValueError):
        percent = DEFAULT_COMMISSION_PERCENT
    percent = max(0.0, min(percent, 100.0))
    db.execute(
        "UPDATE app_settings SET value = ?, updated_at = ? WHERE key = 'commission_percent'",
        (str(percent), now_iso()),
    )


def ensure_notification_columns(db):
    existing_columns = {row[1] for row in db.execute("PRAGMA table_info(bookings)").fetchall()}
    for column_name in ("rating_token", "rating_score", "rating_comment", "rated_at"):
        if column_name not in existing_columns:
            if column_name == "rating_score":
                db.execute("ALTER TABLE bookings ADD COLUMN rating_score INTEGER")
            else:
                db.execute(f"ALTER TABLE bookings ADD COLUMN {column_name} TEXT")


def ensure_admin_audit_schema(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER,
            details TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(admin_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )


def ensure_trip_status_schema(db):
    schema_row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'trips'"
    ).fetchone()
    if schema_row is None:
        return
    schema_sql = schema_row[0] or ""
    if "status IN ('open', 'cancelled', 'completed')" in schema_sql:
        return

    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        """
        CREATE TABLE trips_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL,
            route TEXT NOT NULL,
            travel_date TEXT NOT NULL,
            travel_time TEXT NOT NULL,
            available_seats INTEGER NOT NULL,
            price_per_seat REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'cancelled', 'completed')),
            created_at TEXT NOT NULL,
            FOREIGN KEY(driver_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        INSERT INTO trips_new (id, driver_id, route, travel_date, travel_time, available_seats, price_per_seat, status, created_at)
        SELECT id, driver_id, route, travel_date, travel_time, available_seats, price_per_seat,
               CASE WHEN status = 'open' THEN 'open' ELSE 'cancelled' END,
               created_at
        FROM trips
        """
    )
    db.execute("DROP TABLE trips")
    db.execute("ALTER TABLE trips_new RENAME TO trips")
    db.execute("PRAGMA foreign_keys = ON")


def ensure_trip_completion_consistency(db):
    # Cancelled trips must not contribute to completed-trip analytics or revenue.
    db.execute(
        """
        UPDATE bookings
        SET booking_status = 'cancelled'
        WHERE booking_status = 'completed'
          AND trip_id IN (
              SELECT id
              FROM trips
              WHERE status = 'cancelled'
          )
        """
    )
    db.execute(
        """
        UPDATE bookings
        SET payment_status = 'refunded'
        WHERE trip_id IN (
              SELECT id
              FROM trips
              WHERE status = 'cancelled'
          )
          AND payment_status = 'paid'
        """
    )
    db.execute(
        """
        UPDATE payments
        SET status = 'refunded'
        WHERE status = 'paid'
          AND booking_id IN (
              SELECT id
              FROM bookings
              WHERE trip_id IN (
                  SELECT id
                  FROM trips
                  WHERE status = 'cancelled'
              )
          )
        """
    )
    db.execute(
        """
        UPDATE payments
        SET platform_commission_amount = 0,
            driver_net_amount = 0,
            payout_status = 'cancelled'
        WHERE status = 'refunded'
        """
    )
    db.execute(
        """
        UPDATE payments
        SET payout_status = 'ready',
            payout_requested_at = COALESCE(payout_requested_at, ?)
        WHERE status = 'paid'
          AND payout_status = 'pending'
          AND booking_id IN (
              SELECT id
              FROM bookings
              WHERE booking_status = 'completed'
          )
        """,
        (now_iso(),),
    )


def get_commission_percent_db(db):
    row = db.execute(
        "SELECT value FROM app_settings WHERE key = 'commission_percent'"
    ).fetchone()
    if row is None:
        return DEFAULT_COMMISSION_PERCENT
    try:
        percent = float(row[0])
    except (TypeError, ValueError):
        percent = DEFAULT_COMMISSION_PERCENT
    return max(0.0, min(percent, 100.0))


def get_commission_percent():
    row = query_one(
        "SELECT value FROM app_settings WHERE key = 'commission_percent'"
    )
    if row is None:
        return DEFAULT_COMMISSION_PERCENT
    try:
        percent = float(row["value"])
    except (TypeError, ValueError):
        percent = DEFAULT_COMMISSION_PERCENT
    return max(0.0, min(percent, 100.0))


def set_commission_percent(percent):
    percent = max(0.0, min(float(percent), 100.0))
    execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES ('commission_percent', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (str(percent), now_iso()),
    )


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return query_one("SELECT * FROM users WHERE id = ?", (user_id,))


@app.before_request
def enforce_csrf_token():
    if request.method != "POST":
        return
    if request.endpoint in {"static", "payfast_itn"}:
        return
    sent_token = request.form.get("_csrf_token", "")
    session_token = session.get("_csrf_token", "")
    if not sent_token or not session_token or sent_token != session_token:
        abort(400)


@app.before_request
def enforce_beta_traffic_cap():
    if request.endpoint in {"static", "healthz", "readyz"}:
        return
    return enforce_beta_capacity(current_user())


@app.context_processor
def inject_globals():
    user = current_user()
    unread_notifications = 0
    if user is not None:
        unread_notifications = query_one(
            "SELECT COUNT(*) AS count FROM notifications WHERE user_id = ? AND read_at IS NULL",
            (user["id"],),
        )["count"]
    return {
        "current_user": user,
        "unread_notifications": unread_notifications,
        "csrf_token": get_csrf_token(),
    }


def login_required(role=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            if role is not None and user["role"] != role:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def create_notification(user_id, title, message):
    execute(
        """
        INSERT INTO notifications (user_id, title, message, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, title, message, now_iso()),
    )


def create_admin_audit_log(admin_id, action, target_type, target_id=None, details=""):
    execute(
        """
        INSERT INTO admin_audit_logs (admin_id, action, target_type, target_id, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (admin_id, action, target_type, target_id, details, now_iso()),
    )


def save_profile_photo(photo_file):
    if photo_file is None or not getattr(photo_file, "filename", ""):
        return None
    if not secure_filename(photo_file.filename):
        return None

    header = photo_file.stream.read(512)
    photo_file.stream.seek(0)
    detected = imghdr.what(None, h=header)
    if detected not in UPLOAD_ALLOWED_TYPES:
        flash("Please upload JPG, PNG, or WEBP images only.", "danger")
        return None

    ext = "jpg" if detected == "jpeg" else detected
    stored_name = f"{secrets.token_hex(12)}.{ext}"
    photo_file.save(os.path.join(UPLOAD_DIR, stored_name))
    return f"uploads/{stored_name}"


def save_driver_photo(photo_file):
    return save_profile_photo(photo_file)


def save_passenger_photo(photo_file):
    return save_profile_photo(photo_file)


def get_payment_for_booking(booking_id):
    return query_one(
        """
        SELECT *
        FROM payments
        WHERE booking_id = ?
        """,
        (booking_id,),
    )


def mask_card_number(card_number):
    digits = "".join(character for character in (card_number or "") if character.isdigit())
    if len(digits) < 4:
        return ""
    return digits[-4:]


def detect_card_brand(card_number):
    digits = "".join(character for character in (card_number or "") if character.isdigit())
    if digits.startswith("4"):
        return "Visa"
    if digits.startswith(("51", "52", "53", "54", "55")):
        return "Mastercard"
    if digits.startswith(("34", "37")):
        return "American Express"
    if digits.startswith("6"):
        return "Discover"
    return "Card"


def get_trip(trip_id):
    return query_one(
        """
        SELECT trips.*, users.name AS driver_name, users.is_verified AS driver_verified, users.contact_info AS driver_contact,
               users.driver_photo AS driver_photo, users.vehicle_registration AS vehicle_registration,
               users.vehicle_type AS vehicle_type, users.vehicle_color AS vehicle_color,
               users.vehicle_details AS vehicle_details, users.government_id AS driver_government_id
        FROM trips
        JOIN users ON users.id = trips.driver_id
        WHERE trips.id = ?
        """,
        (trip_id,),
    )


def get_booking(booking_id):
    return query_one(
        """
        SELECT bookings.*, trips.route, trips.travel_date, trips.travel_time, trips.price_per_seat,
               passengers.name AS passenger_name, passengers.email AS passenger_email, passengers.contact_info AS passenger_contact,
               drivers.name AS driver_name, drivers.email AS driver_email,
               drivers.contact_info AS driver_contact, drivers.driver_photo AS driver_photo,
               drivers.vehicle_registration AS vehicle_registration,
               drivers.vehicle_type AS vehicle_type, drivers.vehicle_color AS vehicle_color,
               drivers.vehicle_details AS vehicle_details
        FROM bookings
        JOIN trips ON trips.id = bookings.trip_id
        JOIN users AS passengers ON passengers.id = bookings.passenger_id
        JOIN users AS drivers ON drivers.id = trips.driver_id
        WHERE bookings.id = ?
        """,
        (booking_id,),
    )


def user_bookings(user_id):
    return query_all(
        """
        SELECT bookings.*, trips.route, trips.travel_date, trips.travel_time, trips.status AS trip_status,
               trips.available_seats, trips.price_per_seat, users.name AS driver_name
        FROM bookings
        JOIN trips ON trips.id = bookings.trip_id
        JOIN users ON users.id = trips.driver_id
        WHERE bookings.passenger_id = ?
        ORDER BY trips.travel_date ASC, trips.travel_time ASC
        """,
        (user_id,),
    )


def get_booking_by_token(tracking_token):
    return query_one(
        """
        SELECT bookings.*, trips.route, trips.travel_date, trips.travel_time, trips.price_per_seat,
               trips.status AS trip_status, passengers.name AS passenger_name, passengers.email AS passenger_email,
               passengers.contact_info AS passenger_contact, drivers.name AS driver_name,
               drivers.email AS driver_email, drivers.contact_info AS driver_contact,
               drivers.driver_photo AS driver_photo, drivers.vehicle_registration AS vehicle_registration,
               drivers.vehicle_type AS vehicle_type, drivers.vehicle_color AS vehicle_color,
               drivers.vehicle_details AS vehicle_details
        FROM bookings
        JOIN trips ON trips.id = bookings.trip_id
        JOIN users AS passengers ON passengers.id = bookings.passenger_id
        JOIN users AS drivers ON drivers.id = trips.driver_id
        WHERE bookings.tracking_token = ?
        """,
        (tracking_token,),
    )


def get_booking_by_rating_token(rating_token):
    return query_one(
        """
        SELECT bookings.*, trips.route, trips.travel_date, trips.travel_time, trips.price_per_seat,
               passengers.name AS passenger_name, passengers.email AS passenger_email,
               drivers.name AS driver_name, drivers.email AS driver_email
        FROM bookings
        JOIN trips ON trips.id = bookings.trip_id
        JOIN users AS passengers ON passengers.id = bookings.passenger_id
        JOIN users AS drivers ON drivers.id = trips.driver_id
        WHERE bookings.rating_token = ?
        """,
        (rating_token,),
    )


def send_email_message(to_email, subject, body):
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_username = os.environ.get("SMTP_USERNAME", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    from_email = os.environ.get("SMTP_FROM", smtp_username or "no-reply@magayisa.local")

    if smtp_host and smtp_username and smtp_password:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = from_email
        message["To"] = to_email
        message.set_content(body)
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port) as client:
            client.starttls(context=context)
            client.login(smtp_username, smtp_password)
            client.send_message(message)
        sent_via = "smtp"
    else:
        sent_via = "outbox"

    execute(
        "INSERT INTO sent_emails (to_email, subject, body, sent_via, created_at) VALUES (?, ?, ?, ?, ?)",
        (to_email, subject, body, sent_via, now_iso()),
    )


def build_receipt_email(booking, payment, rating_url):
    lines = [
        f"Hello {booking['passenger_name']},",
        "",
        f"Thank you for riding with Magayisa on {booking['route']}.",
        f"Trip date: {booking['travel_date']} at {booking['travel_time']}",
        f"Pickup location: {booking['pickup_location']}",
        f"Driver: {booking['driver_name']}",
        f"Seats: {booking['seats']}",
        f"Receipt: {booking['receipt_number']}",
    ]
    if payment is not None:
        lines.extend([
            f"Payment status: {payment['status']}",
            f"Amount: ZAR {float(payment['amount']):,.2f}",
        ])
        if payment["payment_type"] == "card":
            lines.append(f"Paid by card ending {payment['card_last4'] or 'unknown'}")
        elif payment["payment_type"] == "bank":
            lines.append(f"Paid by bank transfer ending {payment['bank_account_last4'] or 'unknown'}")
    lines.extend([
        "",
        "Please rate your trip experience here:",
        rating_url,
        "",
        "Thank you for supporting Magayisa.",
    ])
    return "\n".join(lines)


def driver_trips(driver_id):
    return query_all(
        """
        SELECT *
        FROM trips
        WHERE driver_id = ?
        ORDER BY travel_date DESC, travel_time DESC
        """,
        (driver_id,),
    )


def driver_bookings(driver_id):
    return query_all(
        """
        SELECT bookings.*, trips.route, trips.travel_date, trips.travel_time, trips.status AS trip_status,
               passengers.name AS passenger_name, passengers.email AS passenger_email, passengers.contact_info AS passenger_contact
        FROM bookings
        JOIN trips ON trips.id = bookings.trip_id
        JOIN users AS passengers ON passengers.id = bookings.passenger_id
        WHERE trips.driver_id = ?
        ORDER BY bookings.created_at DESC
        """,
        (driver_id,),
    )


def get_chat_messages_for_booking(booking_id):
    return query_all(
        """
        SELECT chat_messages.*, users.name AS sender_name, users.role AS sender_role
        FROM chat_messages
        JOIN users ON users.id = chat_messages.sender_id
        WHERE chat_messages.booking_id = ?
        ORDER BY chat_messages.created_at ASC, chat_messages.id ASC
        """,
        (booking_id,),
    )


def get_booking_other_party_id(booking, user):
    if user["role"] == "passenger" and booking["passenger_id"] == user["id"]:
        trip = get_trip(booking["trip_id"])
        return trip["driver_id"] if trip is not None else None
    if user["role"] == "driver":
        trip = get_trip(booking["trip_id"])
        if trip is not None and trip["driver_id"] == user["id"]:
            return booking["passenger_id"]
    return None


def payments_for_admin():
    return query_all(
        """
        SELECT payments.*, bookings.passenger_id, trips.route, users.name AS passenger_name
        FROM payments
        JOIN bookings ON bookings.id = payments.booking_id
        JOIN trips ON trips.id = bookings.trip_id
        JOIN users ON users.id = bookings.passenger_id
        ORDER BY payments.created_at DESC
        """
    )


def driver_period_stats(driver_id, days):
    lookback = f"-{max(days - 1, 0)} days"
    row = query_one(
        """
        SELECT
            COUNT(DISTINCT trips.id) AS trips_completed,
            COALESCE(SUM(CASE WHEN bookings.booking_status = 'completed' THEN bookings.seats ELSE 0 END), 0) AS seats_booked,
            COALESCE(SUM(CASE WHEN bookings.booking_status = 'completed' AND payments.status = 'paid' THEN payments.amount ELSE 0 END), 0) AS revenue,
            COALESCE(SUM(CASE WHEN bookings.booking_status = 'completed' AND payments.status = 'paid' THEN payments.driver_net_amount ELSE 0 END), 0) AS driver_net_earnings,
            COALESCE(SUM(CASE WHEN bookings.booking_status = 'completed' AND payments.status = 'paid' THEN payments.platform_commission_amount ELSE 0 END), 0) AS platform_commission,
            AVG(CASE WHEN bookings.booking_status = 'completed' THEN bookings.rating_score END) AS average_rating,
            COUNT(CASE WHEN bookings.booking_status = 'completed' AND bookings.rating_score IS NOT NULL THEN 1 END) AS ratings_count
        FROM trips
        LEFT JOIN bookings ON bookings.trip_id = trips.id
        LEFT JOIN payments ON payments.booking_id = bookings.id
        WHERE trips.driver_id = ?
          AND trips.status = 'completed'
          AND DATE(trips.travel_date) >= DATE('now', ?)
        """,
        (driver_id, lookback),
    )
    avg_rating = row["average_rating"]
    return {
        "trips_completed": row["trips_completed"] or 0,
        "seats_booked": row["seats_booked"] or 0,
        "revenue": float(row["revenue"] or 0),
        "driver_net_earnings": float(row["driver_net_earnings"] or 0),
        "platform_commission": float(row["platform_commission"] or 0),
        "average_rating": round(float(avg_rating), 2) if avg_rating is not None else None,
        "ratings_count": row["ratings_count"] or 0,
    }


def driver_payout_summary(driver_id):
    row = query_one(
        """
        SELECT
            COALESCE(SUM(CASE WHEN payments.status = 'paid' AND payments.payout_status IN ('pending', 'ready') THEN payments.driver_net_amount ELSE 0 END), 0) AS pending_amount,
            COALESCE(SUM(CASE WHEN payments.status = 'paid' AND payments.payout_status = 'paid' THEN payments.driver_net_amount ELSE 0 END), 0) AS paid_amount
        FROM payments
        JOIN bookings ON bookings.id = payments.booking_id
        JOIN trips ON trips.id = bookings.trip_id
        WHERE trips.driver_id = ?
        """,
        (driver_id,),
    )
    return {
        "pending_amount": float(row["pending_amount"] or 0),
        "paid_amount": float(row["paid_amount"] or 0),
    }


def available_trips(status_filter="open"):
    query = """
        SELECT trips.*, users.name AS driver_name, users.is_verified AS driver_verified,
               users.contact_info AS driver_contact, users.driver_photo AS driver_photo,
               users.vehicle_registration AS vehicle_registration, users.vehicle_type AS vehicle_type,
               users.vehicle_color AS vehicle_color, users.vehicle_details AS vehicle_details
        FROM trips
        JOIN users ON users.id = trips.driver_id
    """
    clauses = []
    if status_filter == "open":
        clauses.append("trips.status = 'open'")
        clauses.append("trips.available_seats > 0")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY trips.travel_date ASC, trips.travel_time ASC"
    return query_all(query)


@app.template_filter("currency")
def currency_filter(value):
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = 0.0
    return f"ZAR {amount:,.2f}"


@app.route("/")
def index():
    stats = {
        "drivers": query_one("SELECT COUNT(*) AS count FROM users WHERE role = 'driver'")["count"],
        "passengers": query_one("SELECT COUNT(*) AS count FROM users WHERE role = 'passenger'")["count"],
        "trips": query_one("SELECT COUNT(*) AS count FROM trips")["count"],
        "bookings": query_one("SELECT COUNT(*) AS count FROM bookings")["count"],
    }
    featured_trips = query_all(
        """
        SELECT trips.*, users.name AS driver_name, users.is_verified AS driver_verified,
               users.contact_info AS driver_contact, users.driver_photo AS driver_photo,
               users.vehicle_registration AS vehicle_registration, users.vehicle_type AS vehicle_type,
               users.vehicle_color AS vehicle_color, users.vehicle_details AS vehicle_details
        FROM trips
        JOIN users ON users.id = trips.driver_id
        WHERE trips.status = 'open'
        ORDER BY trips.travel_date ASC, trips.travel_time ASC
        LIMIT 4
        """
    )
    return render_template("index.html", stats=stats, featured_trips=featured_trips)


@app.route("/terms")
def terms_of_service():
    return render_template("legal/terms.html")


@app.route("/privacy")
def privacy_policy():
    return render_template("legal/privacy.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        if is_rate_limited("register", limit=8, window_seconds=300):
            flash("Too many registration attempts. Please wait a few minutes.", "danger")
            return redirect(url_for("register"))
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "passenger")
        contact_info = request.form.get("contact_info", "").strip()
        passenger_photo = save_passenger_photo(request.files.get("passenger_photo"))
        government_id = request.form.get("government_id", "").strip()
        vehicle_details = request.form.get("vehicle_details", "").strip()
        vehicle_registration = request.form.get("vehicle_registration", "").strip()
        vehicle_type = request.form.get("vehicle_type", "").strip()
        vehicle_color = request.form.get("vehicle_color", "").strip()
        driver_photo = save_driver_photo(request.files.get("driver_photo"))

        if not name or not email or not password:
            flash("Name, email, and password are required.", "danger")
        elif role not in {"passenger", "driver"}:
            flash("Choose a valid account type.", "danger")
        elif query_one("SELECT id FROM users WHERE email = ?", (email,)):
            flash("That email is already registered.", "danger")
        elif role == "driver" and (not government_id or not vehicle_registration or not vehicle_type or not vehicle_color or not driver_photo):
            flash("Drivers must provide an ID number, a photo, and vehicle registration, type, and colour.", "danger")
        else:
            execute(
                """
                INSERT INTO users (
                    name, email, password_hash, role, contact_info, government_id, passenger_photo, vehicle_details,
                    driver_photo, vehicle_registration, vehicle_type, vehicle_color, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    email,
                    generate_password_hash(password),
                    role,
                    contact_info,
                    government_id if role == "driver" else None,
                    passenger_photo if role == "passenger" else None,
                    vehicle_details if role == "driver" else None,
                    driver_photo if role == "driver" else None,
                    vehicle_registration if role == "driver" else None,
                    vehicle_type if role == "driver" else None,
                    vehicle_color if role == "driver" else None,
                    now_iso(),
                ),
            )
            flash("Registration complete. You can now log in.", "success")
            return redirect(url_for("login"))
    return render_template("auth/register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if is_rate_limited("login", limit=10, window_seconds=300):
            flash("Too many login attempts. Please wait a few minutes.", "danger")
            return redirect(url_for("login"))
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = query_one("SELECT * FROM users WHERE email = ?", (email,))
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
        else:
            session.clear()
            session["user_id"] = user["id"]
            flash("Logged in successfully.", "success")
            return redirect(url_for("dashboard"))
    return render_template("auth/login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required()
def dashboard():
    user = current_user()
    if user["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    if user["role"] == "driver":
        return redirect(url_for("driver_dashboard"))
    return redirect(url_for("passenger_dashboard"))


@app.route("/trips")
@login_required()
def trips_list():
    status_filter = request.args.get("status", "open")
    trips = available_trips(status_filter=status_filter if status_filter in {"open", "all"} else "open")
    return render_template("trips.html", trips=trips, status_filter=status_filter)


@app.route("/trips/<int:trip_id>", methods=["GET", "POST"])
@login_required(role="passenger")
def trip_detail(trip_id):
    trip = get_trip(trip_id)
    if trip is None:
        abort(404)

    if request.method == "POST":
        if trip["status"] != "open":
            flash("This trip is no longer available.", "warning")
            return redirect(url_for("trip_detail", trip_id=trip_id))

        seats = request.form.get("seats", "1")
        provider = normalize_provider(request.form.get("provider", "PayFast"))
        pickup_location = request.form.get("pickup_location", "").strip()
        try:
            seats = int(seats)
        except ValueError:
            seats = 0

        if seats <= 0:
            flash("Please choose at least one seat.", "danger")
        elif not pickup_location:
            flash("Please confirm your pickup location.", "danger")
        elif seats > trip["available_seats"]:
            flash("Not enough seats are available.", "danger")
        else:
            amount = float(trip["price_per_seat"]) * seats
            commission_percent = get_commission_percent()
            platform_commission, driver_net = calculate_split(amount, commission_percent)
            payment_reference = f"{provider[:3].upper()}-{secrets.token_hex(6).upper()}"
            receipt_number = f"RCPT-{secrets.token_hex(5).upper()}"
            tracking_token = secrets.token_urlsafe(24)
            rating_token = secrets.token_urlsafe(24)
            booking_cursor = execute(
                """
                INSERT INTO bookings (
                    trip_id, passenger_id, seats, pickup_location, payment_method, payment_status,
                    booking_status, payment_reference, receipt_number, tracking_token, rating_token, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', 'confirmed', ?, ?, ?, ?, ?)
                """,
                (
                    trip_id,
                    current_user()["id"],
                    seats,
                    pickup_location,
                    provider,
                    payment_reference,
                    receipt_number,
                    tracking_token,
                    rating_token,
                    now_iso(),
                ),
            )
            booking_id = booking_cursor.lastrowid
            execute(
                """
                INSERT INTO payments (
                    booking_id, provider, amount, currency, commission_rate,
                    platform_commission_amount, driver_net_amount, status,
                    receipt_number, reference, created_at
                )
                VALUES (?, ?, ?, 'ZAR', ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    booking_id,
                    provider,
                    amount,
                    commission_percent,
                    0,
                    0,
                    receipt_number,
                    payment_reference,
                    now_iso(),
                ),
            )
            execute(
                "UPDATE trips SET available_seats = available_seats - ? WHERE id = ?",
                (seats, trip_id),
            )
            create_notification(
                current_user()["id"],
                "Booking created",
                f"Your booking for {trip['route']} was created. Complete payment to confirm. Receipt {receipt_number}. Pickup: {pickup_location}.",
            )
            if provider == "PayFast" and is_payfast_configured():
                flash("Booking created. Redirecting to PayFast checkout.", "info")
                return redirect(url_for("start_payfast_checkout", booking_id=booking_id))

            if PAYFAST_TEST_MODE_FALLBACK:
                payment = get_payment_for_booking(booking_id)
                mark_booking_payment_paid(booking_id, payment, payment_type="fallback", reference=payment_reference)
                create_notification(
                    trip["driver_id"],
                    "Seat request paid",
                    f"A passenger booked {seats} seat(s) on {trip['route']} and payment was received. Pickup: {pickup_location}.",
                )
                flash("Booking confirmed using fallback payment mode.", "warning")
                return redirect(url_for("booking_detail", booking_id=booking_id))

            flash("Booking created, but payment provider is unavailable. Please complete payment details manually.", "warning")
            return redirect(url_for("booking_payment", booking_id=booking_id))

    booking = query_one(
        "SELECT * FROM bookings WHERE trip_id = ? AND passenger_id = ? ORDER BY id DESC LIMIT 1",
        (trip_id, current_user()["id"]),
    )
    return render_template("booking.html", trip=trip, booking=booking)


@app.route("/bookings/<int:booking_id>")
@login_required()
def booking_detail(booking_id):
    booking = get_booking(booking_id)
    if booking is None:
        abort(404)
    user = current_user()
    other_party_id = get_booking_other_party_id(booking, user)
    if other_party_id is None and user["role"] != "admin":
        abort(403)
    if user["role"] == "driver":
        trip = get_trip(booking["trip_id"])
        if trip is None or trip["driver_id"] != user["id"]:
            abort(403)
    payment = get_payment_for_booking(booking_id)
    chat_messages = get_chat_messages_for_booking(booking_id)
    share_url = url_for("track_trip_public", tracking_token=booking["tracking_token"], _external=True)
    return render_template(
        "booking_detail.html",
        booking=booking,
        payment=payment,
        share_url=share_url,
        chat_messages=chat_messages,
    )


@app.route("/bookings/<int:booking_id>/payfast/start")
@login_required(role="passenger")
def start_payfast_checkout(booking_id):
    booking = get_booking(booking_id)
    if booking is None or booking["passenger_id"] != current_user()["id"]:
        abort(403)
    payment = get_payment_for_booking(booking_id)
    if payment is None:
        abort(404)
    if payment["status"] == "paid":
        return redirect(url_for("booking_detail", booking_id=booking_id))
    if not is_payfast_configured():
        flash("PayFast is not configured on this environment. Using fallback payment page.", "warning")
        return redirect(url_for("booking_payment", booking_id=booking_id))

    payload = build_payfast_payload(booking, payment)
    return render_template("payfast_redirect.html", payfast_url=payfast_url(), payload=payload, booking=booking)


@app.route("/payfast/return/<int:booking_id>")
@login_required(role="passenger")
def payfast_return(booking_id):
    booking = get_booking(booking_id)
    if booking is None or booking["passenger_id"] != current_user()["id"]:
        abort(403)
    payment = get_payment_for_booking(booking_id)
    if payment is not None and payment["status"] == "paid":
        flash("Payment received and booking confirmed.", "success")
    else:
        flash("Payment is being processed. Please refresh in a moment.", "info")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/payfast/cancel/<int:booking_id>")
@login_required(role="passenger")
def payfast_cancel(booking_id):
    booking = get_booking(booking_id)
    if booking is None or booking["passenger_id"] != current_user()["id"]:
        abort(403)
    flash("PayFast checkout was cancelled. You can try again when ready.", "warning")
    return redirect(url_for("booking_payment", booking_id=booking_id))


@app.route("/payfast/itn", methods=["POST"])
def payfast_itn():
    payload = {key: request.form.get(key, "") for key in request.form.keys()}
    if not payload:
        return "invalid", 400

    received_signature = payload.get("signature", "")
    expected_signature = payfast_signature_for_payload(payload)
    if not received_signature or received_signature.lower() != expected_signature.lower():
        logger.warning("PayFast ITN signature mismatch.")
        if not app.config.get("TESTING"):
            return "invalid signature", 400

    if payload.get("merchant_id", "") != PAYFAST_MERCHANT_ID and not app.config.get("TESTING"):
        logger.warning("PayFast ITN merchant mismatch.")
        return "invalid merchant", 400

    booking_id_raw = payload.get("m_payment_id", "").strip()
    try:
        booking_id = int(booking_id_raw)
    except ValueError:
        return "invalid booking", 400

    booking = get_booking(booking_id)
    payment = get_payment_for_booking(booking_id)
    if booking is None or payment is None:
        return "missing booking", 404

    expected_amount = round(float(payment["amount"] or 0), 2)
    try:
        incoming_amount = round(float(payload.get("amount_gross", payload.get("amount", "0"))), 2)
    except ValueError:
        incoming_amount = 0.0
    if incoming_amount != expected_amount and not app.config.get("TESTING"):
        logger.warning("PayFast ITN amount mismatch for booking %s: got=%s expected=%s", booking_id, incoming_amount, expected_amount)
        return "invalid amount", 400

    payment_status = payload.get("payment_status", "").strip().upper()
    pf_payment_id = payload.get("pf_payment_id", "").strip()
    item_name = payload.get("item_name", "trip payment")

    if payment_status == "COMPLETE":
        if payment["status"] != "paid":
            mark_booking_payment_paid(booking_id, payment, payment_type="payfast", reference=pf_payment_id or payment["reference"])
            trip = get_trip(booking["trip_id"])
            create_notification(
                booking["passenger_id"],
                "Payment received",
                f"PayFast confirmed your payment for {item_name}.",
            )
            if trip is not None:
                create_notification(
                    trip["driver_id"],
                    "Seat request paid",
                    f"A passenger booking on {trip['route']} has been paid via PayFast.",
                )
    elif payment_status in {"FAILED", "CANCELLED"}:
        mark_booking_payment_failed(booking_id)

    return "OK", 200


@app.route("/bookings/<int:booking_id>/chat", methods=["POST"])
@login_required()
def send_booking_message(booking_id):
    booking = get_booking(booking_id)
    if booking is None:
        abort(404)
    user = current_user()
    receiver_id = get_booking_other_party_id(booking, user)
    if receiver_id is None:
        abort(403)
    if is_rate_limited("chat", limit=40, window_seconds=60):
        flash("You are sending messages too quickly. Please slow down.", "warning")
        return redirect(url_for("booking_detail", booking_id=booking_id))

    message = request.form.get("message", "").strip()
    if not message:
        flash("Message cannot be empty.", "danger")
        return redirect(url_for("booking_detail", booking_id=booking_id))

    execute(
        """
        INSERT INTO chat_messages (booking_id, sender_id, receiver_id, message, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (booking_id, user["id"], receiver_id, message, now_iso()),
    )

    create_notification(
        receiver_id,
        "New trip message",
        f"You have a new message on booking #{booking_id}.",
    )
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/bookings/<int:booking_id>/payment", methods=["GET", "POST"])
@login_required(role="passenger")
def booking_payment(booking_id):
    booking = get_booking(booking_id)
    if booking is None or booking["passenger_id"] != current_user()["id"]:
        abort(403)

    payment = get_payment_for_booking(booking_id)
    if payment is None:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "").strip().lower()
        if action == "payfast" or request.form.get("payment_type") == "payfast":
            return redirect(url_for("start_payfast_checkout", booking_id=booking_id))

        payment_type = request.form.get("payment_type", "card")
        bank_name = request.form.get("bank_name", "").strip()
        bank_account_name = request.form.get("bank_account_name", "").strip()
        bank_account_number = request.form.get("bank_account_number", "").strip()
        cardholder_name = request.form.get("cardholder_name", "").strip()
        card_number = request.form.get("card_number", "").strip()
        card_expiry_month = request.form.get("card_expiry_month", "").strip()
        card_expiry_year = request.form.get("card_expiry_year", "").strip()
        cvv = request.form.get("cvv", "").strip()

        if payment_type not in {"card", "bank"}:
            flash("Choose a valid payment method.", "danger")
        elif payment_type == "card" and (not cardholder_name or not card_number or not card_expiry_month or not card_expiry_year or not cvv):
            flash("Please complete the card details.", "danger")
        elif payment_type == "bank" and (not bank_name or not bank_account_name or not bank_account_number):
            flash("Please complete the banking details.", "danger")
        else:
            masked_card_last4 = mask_card_number(card_number) if payment_type == "card" else None
            card_brand = detect_card_brand(card_number) if payment_type == "card" else None
            bank_account_last4 = bank_account_number[-4:] if payment_type == "bank" and len(bank_account_number) >= 4 else None
            commission_percent = get_commission_percent()
            platform_commission, driver_net = calculate_split(payment["amount"], commission_percent)
            execute(
                """
                UPDATE payments
                SET payment_type = ?, bank_name = ?, bank_account_name = ?, bank_account_last4 = ?,
                    cardholder_name = ?, card_brand = ?, card_last4 = ?, card_expiry_month = ?, card_expiry_year = ?,
                    commission_rate = ?, platform_commission_amount = ?, driver_net_amount = ?, status = 'paid'
                WHERE booking_id = ?
                """,
                (
                    payment_type,
                    bank_name if payment_type == "bank" else None,
                    bank_account_name if payment_type == "bank" else None,
                    bank_account_last4,
                    cardholder_name if payment_type == "card" else None,
                    card_brand,
                    masked_card_last4,
                    card_expiry_month if payment_type == "card" else None,
                    card_expiry_year if payment_type == "card" else None,
                    commission_percent,
                    platform_commission,
                    driver_net,
                    booking_id,
                ),
            )
            execute(
                "UPDATE bookings SET payment_method = ?, payment_status = 'paid' WHERE id = ?",
                (payment_type.capitalize(), booking_id),
            )
            create_notification(
                current_user()["id"],
                "Payment details saved",
                "Your bank or card details were added securely. CVV was not stored.",
            )
            flash("Payment details saved securely.", "success")
            return redirect(url_for("booking_detail", booking_id=booking_id))

    return render_template(
        "payment.html",
        booking=booking,
        payment=payment,
        payfast_enabled=is_payfast_configured(),
    )


@app.route("/track/<tracking_token>")
def track_trip_public(tracking_token):
    booking = get_booking_by_token(tracking_token)
    if booking is None:
        abort(404)
    share_url = url_for("track_trip_public", tracking_token=tracking_token, _external=True)
    return render_template("track_trip.html", booking=booking, share_url=share_url)


@app.route("/bookings/<int:booking_id>/track")
@login_required()
def track_trip(booking_id):
    booking = get_booking(booking_id)
    if booking is None:
        abort(404)
    user = current_user()
    if user["role"] == "passenger" and booking["passenger_id"] != user["id"]:
        abort(403)
    if user["role"] == "driver":
        trip = get_trip(booking["trip_id"])
        if trip is None or trip["driver_id"] != user["id"]:
            abort(403)
    return redirect(url_for("track_trip_public", tracking_token=booking["tracking_token"]))


@app.route("/bookings/<int:booking_id>/dispute", methods=["POST"])
@login_required(role="passenger")
def raise_dispute(booking_id):
    booking = get_booking(booking_id)
    if booking is None or booking["passenger_id"] != current_user()["id"]:
        abort(403)
    if is_rate_limited("dispute", limit=5, window_seconds=300):
        flash("Too many dispute submissions. Please wait a moment.", "warning")
        return redirect(url_for("booking_detail", booking_id=booking_id))
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Please provide a reason for the dispute.", "danger")
        return redirect(url_for("booking_detail", booking_id=booking_id))
    execute(
        "INSERT INTO disputes (booking_id, raised_by, reason, created_at) VALUES (?, ?, ?, ?)",
        (booking_id, current_user()["id"], reason, now_iso()),
    )
    admin = query_one("SELECT id FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1")
    if admin is not None:
        create_notification(admin["id"], "New dispute raised", f"Booking #{booking_id} needs review.")
    flash("Your dispute has been submitted to the admin team.", "success")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/notifications")
@login_required()
def notifications():
    items = query_all(
        """
        SELECT *
        FROM notifications
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (current_user()["id"],),
    )
    enriched_items = []
    for item in items:
        item_data = dict(item)
        booking_match = re.search(r"booking\s+#(\d+)", item_data.get("message", ""), re.IGNORECASE)
        if item_data.get("title") == "New trip message" and booking_match:
            booking_id = int(booking_match.group(1))
            item_data["chat_url"] = url_for("booking_detail", booking_id=booking_id, _anchor="chat-panel")
        enriched_items.append(item_data)
    return render_template("notifications.html", items=enriched_items)


@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required()
def mark_notification_read(notification_id):
    execute(
        """
        UPDATE notifications
        SET read_at = ?
        WHERE id = ? AND user_id = ?
        """,
        (now_iso(), notification_id, current_user()["id"]),
    )
    return redirect(url_for("notifications"))


@app.route("/passenger")
@login_required(role="passenger")
def passenger_dashboard():
    bookings = user_bookings(current_user()["id"])
    available = available_trips()
    return render_template("passenger/dashboard.html", bookings=bookings, available=available)


@app.route("/passenger/profile", methods=["GET", "POST"])
@login_required(role="passenger")
def passenger_profile():
    user = current_user()
    if request.method == "POST":
        contact_info = request.form.get("contact_info", "").strip()
        passenger_photo = save_passenger_photo(request.files.get("passenger_photo"))

        execute(
            """
            UPDATE users
            SET contact_info = ?, passenger_photo = COALESCE(?, passenger_photo)
            WHERE id = ?
            """,
            (contact_info, passenger_photo, user["id"]),
        )
        flash("Passenger profile updated.", "success")
        return redirect(url_for("passenger_dashboard"))

    return render_template("passenger/profile.html", user=user)


@app.route("/driver")
@login_required(role="driver")
def driver_dashboard():
    user = current_user()
    trips = driver_trips(user["id"])
    active_trips = [trip for trip in trips if trip["status"] == "open"]
    history_trips = [trip for trip in trips if trip["status"] in {"completed", "cancelled"}]
    bookings = driver_bookings(user["id"])
    commission_percent = get_commission_percent()
    payout_summary = driver_payout_summary(user["id"])
    weekly_stats = driver_period_stats(user["id"], days=7)
    monthly_stats = driver_period_stats(user["id"], days=30)

    today = datetime.utcnow().date()
    default_start = today.replace(day=1)
    requested_start = request.args.get("start_date", default_start.isoformat())
    requested_end = request.args.get("end_date", today.isoformat())
    start_date = parse_iso_date(requested_start)
    end_date = parse_iso_date(requested_end)

    if start_date is None or end_date is None:
        flash("Invalid date selected. Showing current month by default.", "warning")
        start_date = default_start
        end_date = today

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    filtered_trips = query_all(
        """
        SELECT *
        FROM trips
        WHERE driver_id = ?
          AND DATE(travel_date) >= DATE(?)
          AND DATE(travel_date) <= DATE(?)
        ORDER BY travel_date DESC, travel_time DESC
        """,
        (user["id"], start_date.isoformat(), end_date.isoformat()),
    )
    filtered_summary = {
        "total": len(filtered_trips),
        "open": sum(1 for trip in filtered_trips if trip["status"] == "open"),
        "completed": sum(1 for trip in filtered_trips if trip["status"] == "completed"),
        "cancelled": sum(1 for trip in filtered_trips if trip["status"] == "cancelled"),
    }

    return render_template(
        "driver/dashboard.html",
        trips=trips,
        active_trips=active_trips,
        history_trips=history_trips,
        bookings=bookings,
        commission_percent=commission_percent,
        payout_summary=payout_summary,
        weekly_stats=weekly_stats,
        monthly_stats=monthly_stats,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        filtered_trips=filtered_trips,
        filtered_summary=filtered_summary,
    )


@app.route("/driver/profile", methods=["GET", "POST"])
@login_required(role="driver")
def driver_profile():
    user = current_user()
    if request.method == "POST":
        contact_info = request.form.get("contact_info", "").strip()
        government_id = request.form.get("government_id", "").strip()
        vehicle_details = request.form.get("vehicle_details", "").strip()
        vehicle_registration = request.form.get("vehicle_registration", "").strip()
        vehicle_type = request.form.get("vehicle_type", "").strip()
        vehicle_color = request.form.get("vehicle_color", "").strip()
        driver_photo = save_driver_photo(request.files.get("driver_photo"))

        execute(
            """
            UPDATE users
            SET contact_info = ?, government_id = ?, vehicle_details = ?,
                vehicle_registration = ?, vehicle_type = ?, vehicle_color = ?,
                driver_photo = COALESCE(?, driver_photo)
            WHERE id = ?
            """,
            (
                contact_info,
                government_id,
                vehicle_details,
                vehicle_registration,
                vehicle_type,
                vehicle_color,
                driver_photo,
                user["id"],
            ),
        )
        flash("Driver profile updated.", "success")
        return redirect(url_for("driver_dashboard"))

    return render_template("driver/profile.html", user=user)


@app.route("/driver/trips/new", methods=["GET", "POST"])
@login_required(role="driver")
def create_trip():
    if request.method == "POST":
        route = request.form.get("route", "").strip()
        travel_date = request.form.get("travel_date", "").strip()
        travel_time = request.form.get("travel_time", "").strip()
        available_seats = request.form.get("available_seats", "0")
        price_per_seat = request.form.get("price_per_seat", "0")
        try:
            available_seats = int(available_seats)
            price_per_seat = float(price_per_seat)
        except ValueError:
            available_seats = -1
            price_per_seat = -1

        if not route or not travel_date or not travel_time:
            flash("Route, date, and time are required.", "danger")
        elif available_seats <= 0:
            flash("Available seats must be greater than zero.", "danger")
        elif price_per_seat < 0:
            flash("Please enter a valid seat price.", "danger")
        else:
            execute(
                """
                INSERT INTO trips (driver_id, route, travel_date, travel_time, available_seats, price_per_seat, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'open', ?)
                """,
                (
                    current_user()["id"],
                    route,
                    travel_date,
                    travel_time,
                    available_seats,
                    price_per_seat,
                    now_iso(),
                ),
            )
            flash("Trip posted successfully.", "success")
            return redirect(url_for("driver_dashboard"))

    return render_template("driver/trip_form.html", trip=None)


@app.route("/driver/trips/<int:trip_id>/edit", methods=["GET", "POST"])
@login_required(role="driver")
def edit_trip(trip_id):
    trip = get_trip(trip_id)
    if trip is None or trip["driver_id"] != current_user()["id"]:
        abort(403)

    if request.method == "POST":
        route = request.form.get("route", "").strip()
        travel_date = request.form.get("travel_date", "").strip()
        travel_time = request.form.get("travel_time", "").strip()
        available_seats = request.form.get("available_seats", "0")
        price_per_seat = request.form.get("price_per_seat", "0")
        status = request.form.get("status", "open")
        try:
            available_seats = int(available_seats)
            price_per_seat = float(price_per_seat)
        except ValueError:
            available_seats = -1
            price_per_seat = -1

        if not route or not travel_date or not travel_time:
            flash("Route, date, and time are required.", "danger")
        elif available_seats < 0:
            flash("Available seats cannot be negative.", "danger")
        elif price_per_seat < 0:
            flash("Please enter a valid seat price.", "danger")
        elif status not in {"open", "cancelled"}:
            flash("Choose a valid trip status.", "danger")
        else:
            execute(
                """
                UPDATE trips
                SET route = ?, travel_date = ?, travel_time = ?, available_seats = ?, price_per_seat = ?, status = ?
                WHERE id = ?
                """,
                (route, travel_date, travel_time, available_seats, price_per_seat, status, trip_id),
            )
            flash("Trip updated.", "success")
            return redirect(url_for("driver_dashboard"))

    return render_template("driver/trip_form.html", trip=trip)


@app.route("/driver/trips/<int:trip_id>/cancel", methods=["POST"])
@login_required(role="driver")
def cancel_trip(trip_id):
    trip = get_trip(trip_id)
    if trip is None or trip["driver_id"] != current_user()["id"]:
        abort(403)
    if trip["status"] != "open":
        flash("Only open trips can be cancelled.", "warning")
        return redirect(url_for("driver_dashboard"))
    execute("UPDATE trips SET status = 'cancelled', available_seats = 0 WHERE id = ?", (trip_id,))
    affected_bookings = query_all(
        """
        SELECT bookings.id, bookings.passenger_id, bookings.payment_status
        FROM bookings
        WHERE bookings.trip_id = ?
          AND bookings.booking_status != 'cancelled'
        """,
        (trip_id,),
    )
    execute(
        """
        UPDATE bookings
        SET booking_status = 'cancelled',
            payment_status = CASE WHEN payment_status = 'paid' THEN 'refunded' ELSE payment_status END
        WHERE trip_id = ?
          AND booking_status != 'cancelled'
        """,
        (trip_id,),
    )
    execute(
        """
        UPDATE payments
        SET status = 'refunded', platform_commission_amount = 0, driver_net_amount = 0, payout_status = 'cancelled'
        WHERE booking_id IN (
            SELECT id
            FROM bookings
            WHERE trip_id = ?
        )
          AND status = 'paid'
        """,
        (trip_id,),
    )
    for booking in affected_bookings:
        was_paid = booking["payment_status"] == "paid"
        message = f"The trip {trip['route']} on {trip['travel_date']} was cancelled by the driver."
        if was_paid:
            message += " Your payment has been refunded."
        create_notification(
            booking["passenger_id"],
            "Trip cancelled",
            message,
        )
    flash("Trip cancelled, passengers notified, and paid bookings refunded.", "info")
    return redirect(url_for("driver_dashboard"))


@app.route("/driver/trips/<int:trip_id>/close", methods=["POST"])
@login_required(role="driver")
def close_trip(trip_id):
    trip = get_trip(trip_id)
    if trip is None or trip["driver_id"] != current_user()["id"]:
        abort(403)
    if trip["status"] != "open":
        flash("This trip is already closed.", "warning")
        return redirect(url_for("driver_dashboard"))

    bookings = query_all(
        """
        SELECT bookings.*, passengers.name AS passenger_name, passengers.email AS passenger_email
        FROM bookings
        JOIN users AS passengers ON passengers.id = bookings.passenger_id
        WHERE bookings.trip_id = ? AND bookings.booking_status = 'confirmed'
        """,
        (trip_id,),
    )
    execute("UPDATE trips SET status = 'completed', available_seats = 0 WHERE id = ?", (trip_id,))
    for booking in bookings:
        payment = get_payment_for_booking(booking["id"])
        rating_url = url_for("rate_trip_public", rating_token=booking["rating_token"], _external=True)
        email_body = build_receipt_email(
            {
                **booking,
                "route": trip["route"],
                "travel_date": trip["travel_date"],
                "travel_time": trip["travel_time"],
                "pickup_location": booking["pickup_location"],
                "driver_name": trip["driver_name"],
                "receipt_number": booking["receipt_number"],
                "seats": booking["seats"],
            },
            payment,
            rating_url,
        )
        subject = f"Magayisa trip receipt for {trip['route']}"
        send_email_message(booking["passenger_email"], subject, email_body)
        create_notification(
            booking["passenger_id"],
            "Trip completed",
            f"Your trip {trip['route']} has been completed. Check your email for the receipt and rating link.",
        )

    execute("UPDATE bookings SET booking_status = 'completed' WHERE trip_id = ? AND booking_status = 'confirmed'", (trip_id,))
    execute(
        """
        UPDATE payments
        SET payout_status = 'ready',
            payout_requested_at = COALESCE(payout_requested_at, ?)
        WHERE booking_id IN (
            SELECT id
            FROM bookings
            WHERE trip_id = ? AND booking_status = 'completed'
        )
          AND status = 'paid'
          AND payout_status IN ('pending', 'ready')
        """,
        (now_iso(), trip_id),
    )

    flash("Trip closed and customer receipt emails were sent.", "success")
    return redirect(url_for("driver_dashboard"))


@app.route("/rate/<rating_token>", methods=["GET", "POST"])
def rate_trip_public(rating_token):
    booking = get_booking_by_rating_token(rating_token)
    if booking is None:
        abort(404)

    if request.method == "POST":
        try:
            rating_score = int(request.form.get("rating_score", "0"))
        except ValueError:
            rating_score = 0
        rating_comment = request.form.get("rating_comment", "").strip()
        if rating_score < 1 or rating_score > 5:
            flash("Please select a rating between 1 and 5.", "danger")
        else:
            execute(
                """
                UPDATE bookings
                SET rating_score = ?, rating_comment = ?, rated_at = ?
                WHERE rating_token = ?
                """,
                (rating_score, rating_comment, now_iso(), rating_token),
            )
            flash("Thanks for rating the service.", "success")
            return redirect(url_for("index"))

    return render_template("rating.html", booking=booking)


@app.route("/admin")
@login_required(role="admin")
def admin_dashboard():
    pending_drivers = query_all(
        "SELECT * FROM users WHERE role = 'driver' ORDER BY is_verified ASC, created_at DESC"
    )
    registered_users = query_all(
        """
        SELECT id, name, email, role, contact_info, is_verified, created_at
        FROM users
        ORDER BY created_at DESC
        """
    )
    bookings = query_all(
        """
        SELECT bookings.*, trips.route, trips.travel_date, trips.travel_time, users.name AS passenger_name
        FROM bookings
        JOIN trips ON trips.id = bookings.trip_id
        JOIN users ON users.id = bookings.passenger_id
        ORDER BY bookings.created_at DESC
        """
    )
    disputes = query_all(
        """
        SELECT disputes.*, bookings.receipt_number, users.name AS passenger_name
        FROM disputes
        JOIN bookings ON bookings.id = disputes.booking_id
        JOIN users ON users.id = disputes.raised_by
        ORDER BY disputes.created_at DESC
        """
    )
    totals = query_one(
        """
        SELECT
            COALESCE(SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END), 0) AS gross_revenue,
            COALESCE(SUM(CASE WHEN status = 'paid' THEN platform_commission_amount ELSE 0 END), 0) AS platform_commission,
            COALESCE(SUM(CASE WHEN status = 'paid' THEN driver_net_amount ELSE 0 END), 0) AS driver_payouts,
            COALESCE(SUM(CASE WHEN status = 'refunded' THEN amount ELSE 0 END), 0) AS refunded_amount,
            COALESCE(SUM(CASE WHEN status = 'paid' AND payout_status IN ('pending', 'ready') THEN driver_net_amount ELSE 0 END), 0) AS pending_driver_payouts,
            COALESCE(SUM(CASE WHEN status = 'paid' AND payout_status = 'paid' THEN driver_net_amount ELSE 0 END), 0) AS settled_driver_payouts
        FROM payments
        """
    )
    audit_logs = query_all(
        """
        SELECT admin_audit_logs.*, users.name AS admin_name
        FROM admin_audit_logs
        LEFT JOIN users ON users.id = admin_audit_logs.admin_id
        ORDER BY admin_audit_logs.id DESC
        LIMIT 30
        """
    )
    return render_template(
        "admin/dashboard.html",
        pending_drivers=pending_drivers,
        registered_users=registered_users,
        bookings=bookings,
        payments=payments_for_admin(),
        disputes=disputes,
        audit_logs=audit_logs,
        commission_percent=get_commission_percent(),
        reconciliation_date=datetime.utcnow().date().isoformat(),
        totals={
            "gross_revenue": float(totals["gross_revenue"] or 0),
            "platform_commission": float(totals["platform_commission"] or 0),
            "driver_payouts": float(totals["driver_payouts"] or 0),
            "refunded_amount": float(totals["refunded_amount"] or 0),
            "pending_driver_payouts": float(totals["pending_driver_payouts"] or 0),
            "settled_driver_payouts": float(totals["settled_driver_payouts"] or 0),
        },
    )


@app.route("/admin/settings/commission", methods=["POST"])
@login_required(role="admin")
def update_commission_setting():
    raw_value = request.form.get("commission_percent", "").strip()
    try:
        commission_percent = float(raw_value)
    except ValueError:
        flash("Enter a valid commission percentage.", "danger")
        return redirect(url_for("admin_dashboard"))
    if commission_percent < 0 or commission_percent > 100:
        flash("Commission percentage must be between 0 and 100.", "danger")
        return redirect(url_for("admin_dashboard"))

    set_commission_percent(commission_percent)
    flash("Commission setting updated. New bookings will use this rate.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/payouts/<int:payment_id>/approve", methods=["POST"])
@login_required(role="admin")
def approve_payout(payment_id):
    payment = query_one("SELECT * FROM payments WHERE id = ?", (payment_id,))
    if payment is None:
        abort(404)
    if payment["status"] != "paid" or payment["payout_status"] not in {"pending", "ready"}:
        flash("This payout is not eligible for approval.", "warning")
        return redirect(url_for("admin_dashboard"))
    payout_ref = f"PAYOUT-{secrets.token_hex(5).upper()}"
    execute(
        """
        UPDATE payments
        SET payout_status = 'paid', payout_paid_at = ?, payout_reference = ?
        WHERE id = ?
        """,
        (now_iso(), payout_ref, payment_id),
    )
    create_admin_audit_log(
        current_user()["id"],
        "payout_marked_paid",
        "payment",
        payment_id,
        f"reference={payout_ref};receipt={payment['receipt_number']}",
    )
    flash("Driver payout marked as paid.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/payouts/approve-ready", methods=["POST"])
@login_required(role="admin")
def approve_ready_payouts():
    ready_ids = query_all(
        "SELECT id FROM payments WHERE status = 'paid' AND payout_status IN ('pending', 'ready')"
    )
    if not ready_ids:
        flash("No payouts are ready for approval.", "info")
        return redirect(url_for("admin_dashboard"))
    now = now_iso()
    for row in ready_ids:
        payout_ref = f"PAYOUT-{secrets.token_hex(5).upper()}"
        execute(
            """
            UPDATE payments
            SET payout_status = 'paid', payout_paid_at = ?, payout_reference = ?
            WHERE id = ?
            """,
            (now, payout_ref, row["id"]),
        )
    create_admin_audit_log(
        current_user()["id"],
        "bulk_payout_marked_paid",
        "payment",
        None,
        f"count={len(ready_ids)}",
    )
    flash(f"Approved {len(ready_ids)} payout(s).", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/payouts/export")
@login_required(role="admin")
def export_payouts_csv():
    start_date_raw = request.args.get("start_date", "").strip()
    end_date_raw = request.args.get("end_date", "").strip()
    payout_status = request.args.get("payout_status", "all").strip().lower()

    start_date = parse_iso_date(start_date_raw)
    end_date = parse_iso_date(end_date_raw)
    valid_payout_states = {"all", "pending", "ready", "paid", "cancelled"}

    if start_date_raw and start_date is None:
        flash("Invalid export start date.", "danger")
        return redirect(url_for("admin_dashboard"))
    if end_date_raw and end_date is None:
        flash("Invalid export end date.", "danger")
        return redirect(url_for("admin_dashboard"))
    if start_date and end_date and start_date > end_date:
        flash("Export start date cannot be after end date.", "danger")
        return redirect(url_for("admin_dashboard"))
    if payout_status not in valid_payout_states:
        flash("Invalid payout status filter.", "danger")
        return redirect(url_for("admin_dashboard"))

    filters = []
    params = []
    if start_date:
        filters.append("DATE(payments.created_at) >= DATE(?)")
        params.append(start_date.isoformat())
    if end_date:
        filters.append("DATE(payments.created_at) <= DATE(?)")
        params.append(end_date.isoformat())
    if payout_status != "all":
        filters.append("payments.payout_status = ?")
        params.append(payout_status)

    where_clause = " AND ".join(filters) if filters else "1=1"
    rows = query_all(
        f"""
        SELECT
            payments.id,
            payments.created_at,
            payments.status,
            payments.payout_status,
            payments.provider,
            payments.amount,
            payments.commission_rate,
            payments.platform_commission_amount,
            payments.driver_net_amount,
            payments.receipt_number,
            payments.payout_reference,
            payments.payout_requested_at,
            payments.payout_paid_at,
            passenger.name AS passenger_name,
            driver.name AS driver_name,
            trips.route,
            trips.travel_date
        FROM payments
        JOIN bookings ON bookings.id = payments.booking_id
        JOIN trips ON trips.id = bookings.trip_id
        JOIN users AS passenger ON passenger.id = bookings.passenger_id
        JOIN users AS driver ON driver.id = trips.driver_id
        WHERE {where_clause}
        ORDER BY payments.created_at DESC
        """,
        tuple(params),
    )

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "payment_id",
            "created_at",
            "travel_date",
            "route",
            "passenger_name",
            "driver_name",
            "provider",
            "payment_status",
            "payout_status",
            "amount",
            "commission_rate",
            "platform_commission_amount",
            "driver_net_amount",
            "receipt_number",
            "payout_reference",
            "payout_requested_at",
            "payout_paid_at",
        ]
    )

    platform_total = 0.0
    driver_total = 0.0
    gross_total = 0.0
    for row in rows:
        amount = float(row["amount"] or 0)
        platform_amount = float(row["platform_commission_amount"] or 0)
        driver_amount = float(row["driver_net_amount"] or 0)
        if row["status"] == "paid":
            gross_total += amount
            platform_total += platform_amount
            driver_total += driver_amount
        writer.writerow(
            [
                row["id"],
                row["created_at"],
                row["travel_date"],
                row["route"],
                row["passenger_name"],
                row["driver_name"],
                row["provider"],
                row["status"],
                row["payout_status"],
                f"{amount:.2f}",
                row["commission_rate"],
                f"{platform_amount:.2f}",
                f"{driver_amount:.2f}",
                row["receipt_number"],
                row["payout_reference"],
                row["payout_requested_at"],
                row["payout_paid_at"],
            ]
        )

    writer.writerow([])
    writer.writerow(["summary", "rows", len(rows)])
    writer.writerow(["summary", "gross_paid_total", f"{gross_total:.2f}"])
    writer.writerow(["summary", "platform_commission_total", f"{platform_total:.2f}"])
    writer.writerow(["summary", "driver_net_total", f"{driver_total:.2f}"])

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    filename = f"magayisa-payouts-{timestamp}.csv"
    create_admin_audit_log(
        current_user()["id"],
        "payout_export_csv",
        "payments",
        None,
        f"start_date={start_date_raw or 'any'};end_date={end_date_raw or 'any'};payout_status={payout_status}",
    )
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/admin/reconciliation/export")
@login_required(role="admin")
def export_daily_reconciliation_csv():
    date_raw = request.args.get("date", "").strip()
    target_date = parse_iso_date(date_raw)
    if date_raw and target_date is None:
        flash("Invalid reconciliation date.", "danger")
        return redirect(url_for("admin_dashboard"))
    if target_date is None:
        target_date = datetime.utcnow().date()
    target_date_iso = target_date.isoformat()

    rows = query_all(
        """
        SELECT
            payments.id,
            payments.created_at,
            payments.status,
            payments.payout_status,
            payments.provider,
            payments.amount,
            payments.commission_rate,
            payments.platform_commission_amount,
            payments.driver_net_amount,
            payments.receipt_number,
            payments.payout_reference,
            passenger.name AS passenger_name,
            driver.name AS driver_name,
            trips.route,
            trips.travel_date
        FROM payments
        JOIN bookings ON bookings.id = payments.booking_id
        JOIN trips ON trips.id = bookings.trip_id
        JOIN users AS passenger ON passenger.id = bookings.passenger_id
        JOIN users AS driver ON driver.id = trips.driver_id
        WHERE DATE(payments.created_at) = DATE(?)
        ORDER BY payments.created_at ASC
        """,
        (target_date_iso,),
    )

    paid_total = 0.0
    refunded_total = 0.0
    platform_total = 0.0
    driver_total = 0.0
    pending_payout_total = 0.0
    settled_payout_total = 0.0

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["reconciliation_date", target_date_iso])
    writer.writerow(
        [
            "payment_id",
            "created_at",
            "travel_date",
            "route",
            "passenger_name",
            "driver_name",
            "provider",
            "payment_status",
            "payout_status",
            "amount",
            "commission_rate",
            "platform_commission_amount",
            "driver_net_amount",
            "receipt_number",
            "payout_reference",
        ]
    )

    for row in rows:
        amount = float(row["amount"] or 0)
        platform_amount = float(row["platform_commission_amount"] or 0)
        driver_amount = float(row["driver_net_amount"] or 0)
        if row["status"] == "paid":
            paid_total += amount
            platform_total += platform_amount
            driver_total += driver_amount
            if row["payout_status"] in {"pending", "ready"}:
                pending_payout_total += driver_amount
            if row["payout_status"] == "paid":
                settled_payout_total += driver_amount
        elif row["status"] == "refunded":
            refunded_total += amount

        writer.writerow(
            [
                row["id"],
                row["created_at"],
                row["travel_date"],
                row["route"],
                row["passenger_name"],
                row["driver_name"],
                row["provider"],
                row["status"],
                row["payout_status"],
                f"{amount:.2f}",
                row["commission_rate"],
                f"{platform_amount:.2f}",
                f"{driver_amount:.2f}",
                row["receipt_number"],
                row["payout_reference"],
            ]
        )

    writer.writerow([])
    writer.writerow(["summary", "rows", len(rows)])
    writer.writerow(["summary", "paid_total", f"{paid_total:.2f}"])
    writer.writerow(["summary", "refunded_total", f"{refunded_total:.2f}"])
    writer.writerow(["summary", "platform_commission_total", f"{platform_total:.2f}"])
    writer.writerow(["summary", "driver_net_total", f"{driver_total:.2f}"])
    writer.writerow(["summary", "pending_payout_total", f"{pending_payout_total:.2f}"])
    writer.writerow(["summary", "settled_payout_total", f"{settled_payout_total:.2f}"])

    create_admin_audit_log(
        current_user()["id"],
        "daily_reconciliation_export",
        "payments",
        None,
        f"date={target_date_iso};rows={len(rows)}",
    )

    filename = f"magayisa-reconciliation-{target_date.strftime('%Y%m%d')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "time": now_iso(), "production": IS_PRODUCTION})


@app.route("/readyz")
def readyz():
    checks = {"database": False, "redis": False}
    status_code = 200

    try:
        row = query_one("SELECT 1 AS ok")
        checks["database"] = bool(row and row["ok"] == 1)
    except Exception:
        checks["database"] = False
        status_code = 503

    if REDIS_URL:
        client = get_rate_limit_client()
        try:
            checks["redis"] = bool(client is not None and client.ping())
        except Exception:
            checks["redis"] = False
            status_code = 503
    else:
        checks["redis"] = True

    checks["status"] = "ready" if status_code == 200 else "degraded"
    checks["time"] = now_iso()
    return jsonify(checks), status_code


@app.route("/admin/drivers/<int:user_id>/verify", methods=["POST"])
@login_required(role="admin")
def verify_driver(user_id):
    driver = query_one("SELECT * FROM users WHERE id = ? AND role = 'driver'", (user_id,))
    if driver is None:
        abort(404)
    execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user_id,))
    create_notification(user_id, "Verification approved", "Your driver documents have been verified by admin.")
    flash("Driver verified.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/disputes/<int:dispute_id>/resolve", methods=["POST"])
@login_required(role="admin")
def resolve_dispute(dispute_id):
    dispute = query_one("SELECT * FROM disputes WHERE id = ?", (dispute_id,))
    if dispute is None:
        abort(404)
    execute("UPDATE disputes SET status = 'resolved' WHERE id = ?", (dispute_id,))
    create_notification(dispute["raised_by"], "Dispute resolved", f"Dispute #{dispute_id} has been resolved.")
    flash("Dispute marked as resolved.", "success")
    return redirect(url_for("admin_dashboard"))


@app.errorhandler(403)
def forbidden(_error):
    return render_template("base_error.html", title="Forbidden", message="You do not have permission to access this page."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("base_error.html", title="Not found", message="The requested page could not be found."), 404


@app.errorhandler(400)
def bad_request(_error):
    return render_template("base_error.html", title="Bad request", message="Your request could not be processed."), 400


with app.app_context():
    if DATABASE_BACKEND == "sqlite":
        init_db()
    else:
        # Postgres runtime expects schema to be pre-provisioned via migration tooling.
        ensure_default_admin()


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])