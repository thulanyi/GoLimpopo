# Public Beta Runbook (Magayisa)

## 1) Configure beta-safe environment

Set these environment variables in your host:

- `MAGAYISA_PRODUCTION=1`
- `MAGAYISA_DEBUG=0`
- `MAGAYISA_TRUST_PROXY=1`
- `MAGAYISA_SESSION_COOKIE_SECURE=1`
- `MAGAYISA_REDIS_URL=redis://<host>:6379/0`
- `MAGAYISA_BETA_MODE=1`
- `MAGAYISA_BETA_MAX_DAILY_ACTIVE_USERS=200`
- `MAGAYISA_SENTRY_DSN=<your_dsn>`
- `SECRET_KEY=<strong-random-secret>`

Database:

- For public beta use Postgres runtime:
  - `MAGAYISA_POSTGRES_DSN=postgresql://user:pass@host:5432/magayisa`

## 2) Migrate data to Postgres

```powershell
$env:MAGAYISA_DATABASE_PATH = "instance/golimpopo.sqlite"
$env:MAGAYISA_POSTGRES_DSN = "postgresql://user:password@localhost:5432/magayisa"
python scripts/migrate_sqlite_to_postgres.py
```

## 3) Start app (production)

```powershell
python app.py
```

Local one-command beta startup:

```powershell
./scripts/start_public_beta.ps1
```

Or behind gunicorn on Linux:

```bash
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

## 4) Run public URL smoke checks

```powershell
python scripts/public_beta_smoke.py --base-url https://your-beta-domain.example.com
```

Expected:

- `/healthz` returns `status: ok`
- `/readyz` returns `status: ready`
- `/`, `/login`, `/register` return HTTP 200

## 5) Open limited beta

- Share invite link with a small tester group first.
- Keep `MAGAYISA_BETA_MODE=1` enabled.
- Monitor Sentry and uptime checks continuously.

## 6) Daily operations during beta

- Export reconciliation daily from admin dashboard.
- Review payout audit logs daily.
- Review 4xx/5xx spikes and act on alerts.
