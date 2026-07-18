# Magayisa

Magayisa is a Flask-based ride booking starter for passengers, drivers, and admins.

## Features

- Secure registration and login with hashed passwords
- Driver trip posting, editing, and cancellation
- Passenger trip browsing and booking with automatic receipts
- Mock payment processing hooks for Stripe, PayFast, and Flutterwave
- PayFast sandbox checkout is the default payment path with fallback mode support
- Notifications for bookings, payments, and driver verification
- Admin dashboard for driver verification, bookings, payments, and disputes
- Admin-controlled commission setting with automatic platform/driver split
- Driver payout workflow with payout status tracking
- Daily reconciliation CSV export for finance ops
- Admin audit log entries for payout and export actions
- Security basics: CSRF checks, upload validation, and route-level rate limiting
- Optional Redis-backed rate limiting for multi-instance deployments
- Limited public beta traffic cap control via environment flags
- Health and readiness endpoints for monitoring (`/healthz`, `/readyz`)
- Runtime DB adapter supports SQLite (default) or Postgres via environment config

## Run locally

1. Create a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Start the app with `python app.py`.
4. Open `http://127.0.0.1:5000`.

One-command local public beta launcher:

```powershell
./scripts/start_public_beta.ps1
```

## Run tests

- Install dependencies with `pip install -r requirements.txt`
- Run `python -m pytest -q`

## Default admin

If no admin user exists, the app seeds one automatically:

- Email: `admin@magayisa.local`
- Password: `Admin123!`

Override it with `MAGAYISA_ADMIN_EMAIL` and `MAGAYISA_ADMIN_PASSWORD`.
Legacy names `GOLIMPOPO_ADMIN_EMAIL` and `GOLIMPOPO_ADMIN_PASSWORD` are still supported.
In production mode, admin credentials are required and fallback defaults are disabled.

## Production start command

Use the strict production launcher (validates required env and runs gunicorn-style serving):

```powershell
./scripts/start_production.ps1
```

Production profile template:

- `.env.production.example`

## Production runtime flags

- `MAGAYISA_PRODUCTION=1`
- `MAGAYISA_DEBUG=0`
- `MAGAYISA_TRUST_PROXY=1`
- `MAGAYISA_PROXY_X_FOR=1`
- `MAGAYISA_PROXY_X_PROTO=1`
- `MAGAYISA_PROXY_X_HOST=1`
- `MAGAYISA_PROXY_X_PORT=1`
- `MAGAYISA_SESSION_COOKIE_SECURE=1`
- Optional DB override: `MAGAYISA_DATABASE_PATH=/path/to/db.sqlite`
- Postgres runtime: `MAGAYISA_POSTGRES_DSN=postgresql://user:pass@host:5432/dbname`
- `MAGAYISA_REDIS_URL=redis://localhost:6379/0`
- `MAGAYISA_BETA_MODE=1`
- `MAGAYISA_BETA_MAX_DAILY_ACTIVE_USERS=200`
- `MAGAYISA_SENTRY_DSN=...`
- `PAYFAST_MERCHANT_ID=...`
- `PAYFAST_MERCHANT_KEY=...`
- `PAYFAST_PASSPHRASE=...`
- `PAYFAST_SANDBOX=1`
- `PAYFAST_TEST_MODE_FALLBACK=1`
- `MAGAYISA_PUBLIC_BASE_URL=https://your-domain.example.com`

## Postgres migration (full launch)

1. Create a Postgres database.
2. Set environment variables:
	- `MAGAYISA_DATABASE_PATH` (existing SQLite DB)
	- `MAGAYISA_POSTGRES_DSN` (target Postgres DSN)
3. Run migration script:

```powershell
python scripts/migrate_sqlite_to_postgres.py
```

4. Start app with Postgres runtime:

```powershell
$env:MAGAYISA_POSTGRES_DSN = "postgresql://user:password@localhost:5432/magayisa"
python app.py
```

## Production docs

- Deployment guide: `DEPLOYMENT.md`
- Go-live checklist: `PRODUCTION_CHECKLIST.md`
- External infra go-live gate: `docs/GO_LIVE_EXTERNAL_INFRA_CHECKLIST.md`
- Environment template: `.env.example`
- Production environment template: `.env.production.example`
- Monitoring guide: `docs/MONITORING_ALERTS.md`
- Public beta runbook: `PUBLIC_BETA_RUNBOOK.md`
