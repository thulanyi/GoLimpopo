# Incident Response Runbook

## Severity definitions
- Sev 1: Full outage, payment failure across users, or data integrity risk.
- Sev 2: Core flows degraded (login, booking, dashboard) with partial impact.
- Sev 3: Non-critical defects with workaround.

## On-call ownership
- Primary owner: Product maintainer
- Backup owner: Engineering backup

## First 15 minutes
1. Acknowledge the alert in the incident channel.
2. Classify severity and declare an incident lead.
3. Check `/healthz` and `/readyz`.
4. Check recent deploys and rollback if incident started immediately after deployment.
5. Check Postgres and Redis provider dashboards.

## Investigation checklist
- Confirm app logs for stack traces and request spikes.
- Confirm Sentry issue volume and top errors.
- Verify DB connection saturation and slow queries.
- Verify payment provider availability and callbacks.

## Containment actions
- Enable maintenance notice if required.
- Roll back to last known stable revision.
- Temporarily disable high-risk endpoints if needed.

## Communication
- Post status update every 15 minutes for Sev 1, every 30 minutes for Sev 2.
- Include impact, mitigation progress, ETA, and next update time.

## Recovery and closure
1. Verify key flows: login, register, trips, booking, payment callback.
2. Confirm alert recovery and error-rate normalization.
3. Publish incident summary with root cause and prevention items.

## Post-incident follow-up
- Create action items with owners and due dates.
- Add tests and monitors for the failure mode.
- Review runbook updates in next engineering sync.
