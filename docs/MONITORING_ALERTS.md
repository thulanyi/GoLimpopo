# Monitoring and Alerts Setup

## Endpoints

- Health endpoint: `/healthz`
- Readiness endpoint: `/readyz`

These endpoints are designed for uptime probes and service readiness checks.

## Sentry

Set these environment variables:

- `MAGAYISA_SENTRY_DSN`
- `MAGAYISA_SENTRY_TRACES_SAMPLE_RATE` (recommended `0.1`)

When set, the app initializes Sentry automatically during startup.

## Uptime checks

Recommended probes:

- `/healthz` every 60 seconds
- `/readyz` every 60 seconds

Recommended alert conditions:

- 2 consecutive failures on `/healthz`
- 1 failure on `/readyz` for more than 2 minutes

## Alert channels

Route alerts to at least one real-time channel:

- Email
- Slack/Teams webhook
- PagerDuty/Opsgenie for critical incidents

## Suggested severity

- Critical: `/readyz` down, payment flow errors, DB unavailable
- Warning: elevated 4xx/5xx rates, Redis unavailable fallback active
