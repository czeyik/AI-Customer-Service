# Wave 10 Privacy and Data Lifecycle

Approved by Cze Yik on 9 September 2026.

## Policy

- Permanently delete each chat copy after 90 days; do not anonymize it.
- Permanently delete a closed ticket, its notes, notifications, indexed metadata, and objects at
  36 calendar months after `closed_at`. Open and reopened tickets do not age toward deletion.
- Retain backups for at most 35 days. An individual record may remain only in an inaccessible,
  encrypted backup until that backup expires. After any restore, run retention successfully
  before application or worker traffic is enabled.
- Only the active `czeyik` named administrator, as privacy owner, can create or release a legal
  hold. Every hold is limited to one conversation or ticket and requires a reason, case/reference,
  and future expiry. Creation and release are audited; an expired hold stops overriding retention.

## Data inventory and lifecycle

| Store | Customer data | Owner | Lifecycle |
| --- | --- | --- | --- |
| PostgreSQL `conversations`, `messages` | Contact identity, language, chat, intake state | Privacy owner | Per-message deletion at 90 days; delete empty stale conversation state after detaching retained tickets/media |
| PostgreSQL WhatsApp inbound/outbound | Sender/recipient, redacted webhook text, reply and provider IDs | Privacy owner | Permanent deletion at 90 days; retained media metadata is detached first |
| PostgreSQL `tickets`, notes, notifications | Contact details, issue, internal notes and delivery payloads | Support owner; privacy owner for lifecycle | Permanent deletion 36 calendar months after closure |
| PostgreSQL `media_attachments` | Provider/integrity/object metadata | Privacy owner | Unlinked chat media at 90 days; ticket media at ticket expiry |
| Private Lightsail object bucket | Approved image/video bytes | Privacy owner | Object deletion must succeed before its metadata or ticket is removed |
| PostgreSQL indexes | Index entries for the rows above | Database owner | Removed transactionally with each database row; no separate search service exists |
| PostgreSQL subject audit records | Ticket, attachment, notification, and hold identifiers | Security/privacy owner | Customer-subject and hold audit rows are removed with their retained subject |
| Application logs | Event names and internal IDs only; no chat, ticket content, contact details, or object URLs | Security owner | Platform log storage/expiry is configured in Wave 11; chat webhook IP audit rows expire at 90 days |
| PostgreSQL legal-hold registry | Narrow subject ID, reason, reference, expiry and named actors | Privacy owner | Removed with the expired/released subject after normal retention resumes |
| PostgreSQL rate-limit buckets | SHA-256 identity keys and short counters | Security owner | Existing limiter deletes expired windows; no raw identity is stored |
| Encrypted backups | Point-in-time copies of the PostgreSQL and object stores | Infrastructure owner; privacy owner for lifecycle | Maximum 35 days, then automatic backup expiry; retention is reapplied before a restore is exposed |
| Meta WhatsApp | Provider-side messages/media | Meta under the approved WhatsApp terms | Outside the application's deletion boundary; DUDU-controlled copies follow the rows above |
| Z.AI | Approved knowledge only | CCO/provider owner | No customer chats, identifiers, tickets, or media are sent |

Temporary download files are closed after scanning and never become a durable data store.
There is no external conversation/search index to purge.

## Operation and failure handling

Run a preview without deleting customer data:

```bash
python -m app.workers.retention --dry-run
```

Run one bounded deletion pass (also the mandatory post-restore gate):

```bash
python -m app.workers.retention --once
```

Run the normal daily worker:

```bash
python -m app.workers.retention --interval-hours 24
```

Each pass processes at most `RETENTION_BATCH_SIZE` rows per class. Repeated passes are safe.
Object deletion happens before database deletion; a storage error preserves the ticket and
metadata, writes `retention_deletion_failed`, emits an error log, and exits the worker non-zero.
The supervisor must alert Cze Yik and retry the job. Do not manually delete the database row.
The run is complete only when the error is cleared and a later pass records
`retention_run_completed` with zero failures.

Manage holds only from the trusted operator shell; the command requires the privacy owner's
password and individual 2FA:

```bash
python scripts/manage_legal_hold.py create ticket DUDU-123 \
  --reason "Preserve for active case" --reference CASE-123 \
  --expires-at 2027-01-31T16:00:00Z
python scripts/manage_legal_hold.py release <hold-uuid>
```

For a conversation hold, pass its database UUID instead of a public ticket ID. Review active and
soon-expiring holds from `legal_holds`; renew only against current written authorization.

## Restore gate and Wave 11 boundary

Wave 11 must configure database and object backup policies with a hard 35-day maximum and prove
expiry/restore in the intended AWS account. A restored environment remains dark: migrations run,
object access and application traffic remain disabled, `--dry-run` is reviewed, and `--once`
must finish with zero failures before health checks can admit traffic. This prevents records that
expired after the backup was taken from returning to service.

## Repeatable evidence

`tests/test_data_lifecycle.py` uses an injected clock and object store to prove the 90-day edge,
36-calendar-month edge, dry runs, permanent database/object/index deletion, retained-ticket
detachment, owner-only holds, audited release, failure alerts, and successful idempotent retry.
`tests/test_config.py` proves production cannot change the approved 90-day, 36-month, 35-day,
privacy-owner, or batch limits silently.
