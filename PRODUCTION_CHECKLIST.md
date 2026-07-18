# Magayisa Production Checklist

## Security
- Set a strong SECRET_KEY in environment (never commit it).
- Run behind HTTPS only.
- Restrict CORS/origin exposure at reverse proxy.
- Enable web server rate limits in addition to app limits.
- Verify CSRF protection on all POST forms.
- Validate file upload size/type and monitor uploads folder.

## Data and Database
- Move from SQLite to managed Postgres before public launch.
- Turn on daily automated backups and test restore.
- Set backup retention policy and encryption at rest.

## Payments and Payouts
- Confirm live payment gateway credentials.
- Verify commission percentage in admin settings.
- Reconcile paid/refunded amounts daily.
- Run payout approvals with maker-checker process (2-step ops where possible).

## Monitoring and Reliability
- Add uptime checks for app endpoint(s).
- Add centralized logs and error tracking.
- Set alerts for 5xx spikes and payment failures.

## Compliance and Operations
- Publish Terms of Service and Privacy Policy.
- Define support SLA and incident response runbook.
- Test account recovery and admin access procedures.
