# Production Platform and Operations

Status: **Platform evidence below is from the prior release; current candidate gates: [SMART.md](../SMART.md)**
Owner: Cze Yik
Region: AWS Malaysia (`ap-southeast-5`)
Domain: `support.duducaradmin.com`

## Platform

- Isolate staging and production stacks, VPCs, instances, roles, secrets, buckets, volumes, and data.
- Production runs Caddy, FastAPI, PostgreSQL, ClamAV, and the combined worker on one encrypted ARM64
  EC2 `t4g.small` with bounded containers/logs and encrypted swap.
- Only ports 80/443 are public; HTTP serves ACME and redirects to HTTPS. Use Systems Manager instead
  of SSH. Internal services expose no host ports and Caddy access logs are disabled.
- Use an EC2 role, Secrets Manager, private encrypted S3, ECR, CloudWatch, AWS Backup, Route 53, and
  GitHub OIDC. Production accepts only staging-tested commit SHAs and immutable image digests.
- Public-beta AWS ceiling: USD 30. Alert at USD 20 forecast, USD 25 actual, and USD 28 actual. At
  USD 28 remove staging/freeze expansion; at USD 30 disable outbound and stop nonessential resources
  after backup. Review billing daily.

The approved single host provides 99% beta availability, not high availability. Promotion to
`t4g.medium` or a multi-AZ design requires owner approval.

## Monitoring and recovery

- At least 95% of valid messages must receive exactly one accepted response within 30 seconds.
- Alert on failed EC2 checks, CPU above 80%, memory above 85%, failed public readiness, and application
  error patterns. Cze Yik owns critical on-call; Jane receives support-impact notifications.
- Maintenance window: 2:00–4:00 AM Malaysia time.
- Create encrypted PostgreSQL backups hourly and EC2 recovery points daily. S3 lifecycle and workers
  expire backups within 35 days. Reconcile orphaned media daily.
- RPO: one hour. RTO: four hours. Restore only into an isolated empty target; run migrations,
  retention, readiness, reconciliation, and smoke checks before changing DNS or enabling traffic.

```bash
python -m app.workers.backup --restore-key <key> --confirm-empty-target
```

## Deployment and rollback

1. Deploy `infra/aws/budget.yml` in `us-east-1`, then `infra/aws/foundation.yml` and the environment
   stack in `ap-southeast-5`. Apply the foundation update granting the deploy role repository-scoped
   `ecr:DescribeRepositories` before using the immutable-tag release gate.
2. Provision the runtime secret directly from `infra/production/runtime.env.example`; never commit
   or paste the populated file.
3. Dispatch `.github/workflows/release.yml` to staging, validate the immutable digest pair, then
   dispatch production with the exact 40-character commit SHA. Production requires a successful
   GitHub staging deployment for that SHA and immutable ECR tags before resolving the image pair.
   Keep Meta sending disabled during deployment; activation is separate.
4. The deployment script validates Compose and the candidate application configuration and pulls
   images before downtime, then stops API and combined-worker
   writers with a 90-second drain allowance, checks for remaining writer containers and active
   database transactions/claims, and takes a fresh S3 backup. Its key is stored in the release
   directory as `pre-migration-backup.s3`; a failed backup retry preserves the previous reference.
   It records all application table counts in `pre-migration-state.json`, preflights/converts legacy
   dialogue, checks schema drift, and validates every migrated dialogue record. The postmigration
   `migrated-state.json` must match those counts before retention runs and the new release starts. Caddy remains available and returns retryable upstream
   errors while the API is stopped; inbound queues and PostgreSQL volumes remain intact. This
   deliberately trades a maintenance outage for avoiding mixed old/new writers.
5. Backup, preflight, migration or inventory failure leaves application writers stopped and preserves
   the backup. Startup waits for database, API and ClamAV health and a complete combined-worker cycle;
   the worker clears its readiness marker on every restart. Public readiness must also pass before
   recording the image pair as last-good. A startup failure never automatically restarts an old image. Inspect the failure and fix forward before restarting; never acknowledge webhook
   messages without durable persistence.
6. After new-format dialogue commits, use the new runtime with `LLM_ENABLED=false` or a compatible
   forward fix. `/opt/dudu/rollback-images` may reference an incompatible legacy image: it is safe
   only before new writes or after a separately tested reverse conversion. Never restore an old
   snapshot over new customer records.

Staging must exercise PostgreSQL, ClamAV, S3, hosted-model, Meta, and SMTP failure/recovery plus
digest-pair deploy, rollback, and roll-forward.

## Uncertain outbound delivery

WhatsApp replies and support notifications persist `uncertain` before sending and release the
row lock for network I/O. A crash, timeout or malformed success response leaves that state in
place; later messages for the same recipient wait. An explicit rejected attempt may use the
bounded retry schedule. An uncertain attempt is never automatically resent.

Meta sends carry the local row ID in `biz_opaque_callback_data` (`outbox:<id>` or
`notification:<id>`). Signed status callbacks bind that ID, the recipient and the returned
provider message ID before reconciling delivery. This correlation is not provider idempotency.
The field is documented in [Meta status callbacks](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/status).
If callbacks never arrive, use provider evidence and the manual procedure below.

SMTP uses the stable Message-ID `<support-notification.<notification_id>@<from-domain>>`.
SMTP does not supply a reconciliation API here. To recover an uncertain attempt:

1. Pause the sender worker so the row is no longer in flight. Inspect the uncertain row and
   its send-start audit; keep customer bodies and credentials out of operational logs.
2. Match the stable Message-ID (SMTP) or callback/local row ID and provider message ID (Meta)
   against provider logs. Confirmed acceptance means `sent`; confirmed rejection may become
   `retry` or `dead_letter`. No evidence means it remains `uncertain`.
3. Lock the row, update only if it is still `uncertain`, and insert an audit in the same
   transaction naming the operator, row ID, old/new state and provider evidence reference.
   Set `sent_at` for accepted sends or `next_attempt_at` for an explicitly permitted retry.
   Resume the worker after verifying the transaction; it will drain the recipient's queue.

For an SMTP notification, use the existing application session with bound parameters. The
following transaction is a recovery template; supply an actual row, operator and verified
provider evidence before execution:

```python
from datetime import datetime
from app.database import SessionLocal
from app.models import AuditLog, SupportNotification

notification_id = "<uncertain notification UUID>"
operator = "<named operator>"
evidence = "<provider log reference>"
new_status = "sent"  # retry/dead_letter only after confirmed rejection
assert new_status in {"sent", "retry", "dead_letter"}
with SessionLocal.begin() as db:
    row = db.query(SupportNotification).filter_by(
        id=notification_id, status="uncertain"
    ).with_for_update().one()
    row.status = new_status
    if new_status == "sent":
        row.sent_at = datetime.utcnow()
    elif new_status == "retry":
        row.next_attempt_at = datetime.utcnow()
    db.add(AuditLog(actor=operator, event_type="support_notification_reconciled",
        subject_type="ticket", subject_id=row.ticket_id,
        details={"notification_id": row.id, "previous_status": "uncertain",
                 "status": new_status, "evidence": evidence}))
```

For an unreconciled Meta reply, apply the same locked/audited procedure to
`WhatsAppOutboundMessage`, preserving the confirmed `provider_message_id`. Do not infer that a
message was rejected merely because its callback is absent.

## Media recovery

Media downloads and scans can be repeated after a crash. Uploads use the stable attachment key
with S3 `If-None-Match: *`; an accepted upload is reused only when its stored checksum metadata
and content type match. A retry does not create another version. Database commit failures retain
the existing cleanup/reconciliation path. This is object reconciliation, not an atomic S3/database
transaction. See [S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html).

## Evidence

- CloudFormation lint/drift, HTTPS/readiness/security headers, alarm routing, staging failure tests,
  and digest deploy/rollback passed.
- Capacity passed 1,000/1,000 requests at four-way concurrency and 0.796-second p95.
- An encrypted isolated restore, migration, and retention pass completed within RPO/RTO.
- Release-specific SHAs, digests, scans, and activation evidence: `docs/release-validation.md`.
