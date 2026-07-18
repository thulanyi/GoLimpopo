# Go-Live External Infra Checklist

This checklist includes only external infrastructure and operations items that must be complete before full public launch.

## DNS and TLS
- Production domain points to your reverse proxy/load balancer.
- Valid TLS certificate is installed and auto-renew enabled.
- HTTP to HTTPS redirect is enforced.

## Runtime Platform
- App runs behind a production process manager/server (gunicorn workers).
- Reverse proxy is configured (Nginx/Caddy/ingress) with timeouts and request size limits.
- Rolling restart strategy is in place to avoid downtime during deploys.

## Database (Postgres)
- Managed Postgres instance is provisioned.
- `MAGAYISA_POSTGRES_DSN` is set via secret manager.
- Daily backups are enabled and restore test is documented.
- DB access is restricted by network rules/security groups.

## Redis
- Managed Redis instance is provisioned.
- `MAGAYISA_REDIS_URL` is set via secret manager.
- Redis persistence/HA policy matches your uptime target.

## Secrets and Config
- `SECRET_KEY` is strong, unique, and stored outside git.
- Production admin credentials are set via secret manager (no defaults).
- Live payment credentials are configured and sandbox mode is disabled.
- Sentry DSN and alert webhooks are configured.

## Monitoring and Alerts
- Uptime probes on `/healthz` and `/readyz` are active.
- Alert routing is configured (email/Slack/PagerDuty).
- 5xx spike, payment failure, and readiness failure alerts are enabled.

## Compliance and Ops
- Terms of Service and Privacy Policy are published.
- Incident response and on-call ownership are defined.
- Support contact path and SLA are published for beta testers/users.

Reference docs:
- `docs/INCIDENT_RESPONSE_RUNBOOK.md`
- `docs/SUPPORT_SLA.md`
