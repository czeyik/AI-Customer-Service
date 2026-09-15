# Privacy and Data Lifecycle

Approved by Cze Yik on 9 September 2026.

## Policy and inventory

| Data | Owner | Lifecycle |
| --- | --- | --- |
| Conversations, dialogue drafts/control metadata, messages, WhatsApp copies | Privacy owner | Permanently delete each copy after 90 days; remove empty state after detaching retained tickets/media |
| Closed tickets, notes, notifications, audit/index rows | Support/privacy owner | Permanently delete 36 calendar months after `closed_at`; reopened tickets do not age |
| S3 media | Privacy owner | Delete with its ticket; object deletion must succeed before metadata deletion |
| Application/CloudWatch logs | Security owner | No content, contacts, or object URLs; CloudWatch expires after 30 days |
| Rate-limit buckets | Security owner | Hashed identities only; delete expired windows |
| Encrypted backups | Infrastructure/privacy owner | Expire within 35 days; reapply retention before restored traffic |
| Z.AI | CCO/provider owner | Approved knowledge, minimized question, bounded sanitized context and tool results with opaque field references; no raw identifiers, ticket bodies, account data, media or full history |

Meta-held data is outside the application deletion boundary. Temporary media files close after
scanning and are not durable storage. There is no external conversation/search index.

Only the active `czeyik` privacy-owner account may create or release a legal hold. A hold covers one
conversation or ticket, requires a reason, reference, future expiry, and audit, and stops overriding
retention when expired or released.

## Operations

```bash
python -m app.workers.retention --dry-run
python -m app.workers.retention --once
python -m app.workers.retention --interval-hours 24
```

Each pass is bounded by `RETENTION_BATCH_SIZE` and idempotent. Delete objects before database rows.
On failure, preserve metadata, write `retention_deletion_failed`, alert, exit non-zero, and retry;
completion requires `retention_run_completed` with zero failures.

```bash
python scripts/manage_legal_hold.py create ticket DUDU-123 \
  --reason "Preserve for active case" --reference CASE-123 \
  --expires-at 2027-01-31T16:00:00Z
python scripts/manage_legal_hold.py release <hold-uuid>
```

For restoration, keep traffic and object access disabled, run migrations, review `--dry-run`, and
require a zero-failure `--once` before readiness admits traffic.

Evidence: `tests/test_data_lifecycle.py` covers retention edges, holds, deletion order, failure,
retry, and idempotency; `tests/test_config.py` locks the approved periods, owner, and batch bounds.
