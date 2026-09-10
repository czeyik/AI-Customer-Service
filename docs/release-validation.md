# Release Validation and Public-Beta Activation

Status: **PASS**
Go/no-go owner: Cze Yik
Support lead and CCO: Jane
Beta: 10–14 September 2026

Production is live with `PUBLIC_BETA_ENABLED=true`, `META_SEND_ENABLED=true`, and the approved
notification waiver (`NOTIFICATION_SEND_ENABLED=false`) through 14 September 2026.

## Approved release

- Commit: `579ac9efc85400c0e8dae55f9b57acb4cfbdb9c5`
- Application index: `sha256:c31c606bd0f2bc6a7d7ad5a0b16df6b26a25b00435dbdf655e4ada8c75c51bcb`
- ClamAV index: `sha256:f0ffa992f925fd37687efab9ac004878ec7a29bff46d21146c7c4fa40c91f0bc`
- Migration: `c81d4e2a7f10`

Mutable tags are not release evidence.

## Required checks

| Evidence | Result |
| --- | --- |
| Full suite and dependency integrity | PASS: 159 passed, 3 skipped |
| Trilingual outage evaluation | PASS: 36/36; all 15 model calls safely fell back |
| Hosted `glm-5.3-flash` evaluation | PASS: 36/36, 15/15 model calls, 3.679-second p95, USD 0.002030 estimated |
| Security scans | PASS with the time-limited exceptions below |
| Staging, dependency failure, capacity, restore, and rollback | PASS |
| WhatsApp text/media, deduplication, ticket/admin flow, and delivery | PASS |
| Named admins and approved corpus | PASS: two admins and 24 active records |
| Atomic minute/daily/global/total beta limits | PASS |
| Production readiness, TLS, alarms, backup, spend, and rollback pair | PASS |
| Owner decision and observation | GO; 60-minute observation passed without a trigger |

Run the repeatable evaluation with:

```bash
python scripts/release_eval.py --mode outage
python scripts/release_eval.py --mode live \
  --input-price <USD-per-million-input-tokens> \
  --output-price <USD-per-million-output-tokens>
```

Both modes require every mandatory case, at least 95% overall, and p95 no higher than 30 seconds.
Store only aggregate counts, latency, tokens, and cost.

## Exceptions and operation

Cze Yik accepted AWS-0104 for required TCP 443/465 egress and AWS-0136 for AWS-managed SNS
encryption through 14 September 2026. Remediate or obtain a new explicit decision before later
traffic. Support notifications are waived for the same period while both admins monitor the inbox.

Activation and any repeat deployment are controlled configuration changes. Immediately check public
readiness, five alarms, delivery/read states, duplicates, dead letters, p95, tickets, notification
failures, host resources, provider errors, spend, and volume warnings.

Disable outbound traffic for the launch-contract security/data-loss triggers. Pause or roll back
after 15 minutes above 5% failures/duplicates, p95 above 30 seconds, total answer failure,
availability below 99%, total spend forecast above USD 65/actual at USD 70, or AWS actual at USD 30.
Preserve inbound events and use `/opt/dudu/rollback-images`; never reverse a migration destructively.
