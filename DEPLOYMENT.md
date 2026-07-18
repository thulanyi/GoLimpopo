# Deploying Magayisa

## 1. Install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Configure environment

Use `.env.production.example` as the production baseline and copy values into your host environment (or secret manager).

Required for production startup:

- `SECRET_KEY`
- `MAGAYISA_ADMIN_EMAIL`
- `MAGAYISA_ADMIN_PASSWORD`
- `MAGAYISA_PRODUCTION=1`
- `MAGAYISA_TRUST_PROXY=1`
- `MAGAYISA_SESSION_COOKIE_SECURE=1`
- `MAGAYISA_REDIS_URL` (required)
- `MAGAYISA_BETA_MODE=1` and `MAGAYISA_BETA_MAX_DAILY_ACTIVE_USERS` (for beta rollout)
- `MAGAYISA_SENTRY_DSN` (for error monitoring)
- `MAGAYISA_POSTGRES_DSN` (required; switches runtime DB adapter to Postgres)
- SMTP variables if email is required

## 3. Run in production mode

PowerShell launcher (validates required env and starts gunicorn):

```powershell
./scripts/start_production.ps1
```

Preflight-only check:

```powershell
python scripts/production_preflight.py
```

Gunicorn example (Linux container/VM):

```bash
python -m gunicorn -w ${WEB_CONCURRENCY:-4} -b 0.0.0.0:${PORT:-8000} --access-logfile - --error-logfile - app:app
```

## 4. Reverse proxy

Put Nginx/Caddy in front for:

- TLS/HTTPS
- request buffering and timeouts
- gzip/brotli
- static file caching for `/static/*`

## 5. Database recommendation

Current app uses SQLite (`instance/golimpopo.sqlite`).
For public launch, migrate to Postgres and run with managed backups.

Migration command:

```powershell
$env:MAGAYISA_DATABASE_PATH = "instance/golimpopo.sqlite"
$env:MAGAYISA_POSTGRES_DSN = "postgresql://user:password@localhost:5432/magayisa"
python scripts/migrate_sqlite_to_postgres.py
```

Then point application runtime to Postgres in your infrastructure (recommended behind a managed service).

Runtime example:

```powershell
$env:MAGAYISA_POSTGRES_DSN = "postgresql://user:password@localhost:5432/magayisa"
python app.py
```

## 6. Monitoring and alerts

- Probe `/healthz` and `/readyz` from your uptime monitor.
- Configure alerting channels for outages and degraded readiness.
- Configure Sentry DSN for runtime exceptions.
- See `docs/MONITORING_ALERTS.md` for baseline settings.

## 7. Post-deploy smoke checks

- Register/login works
- Passenger booking payment writes records
- Driver close/cancel trip flow works
- Admin commission and payout actions work
- Notification and chat links open correctly

## 8. Public beta runbook

- Follow `PUBLIC_BETA_RUNBOOK.md` for limited-release rollout.
- Smoke test your live URL with:

```powershell
python scripts/public_beta_smoke.py --base-url https://your-beta-domain.example.com
```

## 9. External infra go-live gate

- Complete `docs/GO_LIVE_EXTERNAL_INFRA_CHECKLIST.md` before full public launch.

## 10. Render one-click infra (web + Postgres + Redis)

This repo includes `render.yaml` for managed deployment.

1. In Render, create a new Blueprint deployment from this repository.
2. Render provisions:
	- Web service (`magayisa-web`)
	- Managed Postgres (`magayisa-postgres`)
	- Managed Redis (`magayisa-redis`)
3. Set required secret env vars in Render:
	- `MAGAYISA_ADMIN_EMAIL`
	- `MAGAYISA_ADMIN_PASSWORD`
	- `MAGAYISA_PUBLIC_BASE_URL`
	- `PAYFAST_MERCHANT_ID`
	- `PAYFAST_MERCHANT_KEY`
	- `PAYFAST_PASSPHRASE`
	- `MAGAYISA_SENTRY_DSN` (recommended)
