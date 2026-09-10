# DUDU Car WhatsApp Public Beta Contract

Status: **APPROVED — 10 September 2026**
Owner: Cze Yik

## Scope and limits

- Public WhatsApp beta for Kuala Lumpur and Selangor, 10–14 September 2026.
- English, Bahasa Malaysia, and Simplified Chinese; approved answers, consent-first tickets, human
  escalation, and approved image/video attachments.
- Limits: 20 messages/user/minute, 200/user/day, 2,000/day globally, and 10,000 total. Malaysia-time
  day boundaries apply. Count authenticated, deduplicated inbound messages once.
- PostgreSQL enforces limits atomically and fails closed. Emit aggregate warnings at 80% and 90%; at
  100%, stop normal processing and send at most one localized capacity response per user/day.
- Availability target: 99%, excluding evidenced Meta outages. Human support: 9:00 AM–6:00 PM daily.

No Instagram, broadcasts, CRM, complex roles, automated media analysis, account-changing actions,
or commitments are in scope.

## Budget

- AWS ceiling: USD 30/month. Alerts: USD 20 forecast, USD 25 actual, USD 28 actual. At USD 28,
  remove staging and freeze expansion; at USD 30, disable outbound traffic and stop nonessential
  resources after a successful backup.
- Total external-service ceiling: USD 70. At USD 65 forecast, pause new conversations; at USD 70
  actual, disable outbound traffic.
- Hosted models: USD 15; WhatsApp/email: USD 10; reserve: USD 15. Review costs daily.

## Ownership

| Responsibility | Owner |
| --- | --- |
| Go/no-go, production, releases, rollback, budget, privacy, security, incidents, and recovery | Cze Yik |
| Support, ticket assignment, customer communication, and approved knowledge/copy | Jane |

Support notifications may remain disabled through 14 September 2026 while both owners monitor the
admin dashboard. Customer replies, ticketing, auditing, urgent handling, and rollback controls are
not waived.

## Platform and data policy

- AWS Malaysia (`ap-southeast-5`), one account with isolated staging and production. Production is
  one encrypted ARM64 EC2 `t4g.small`; promotion requires owner approval.
- Use ECR digests, GitHub OIDC, EC2 roles, Secrets Manager, private encrypted S3, CloudWatch, AWS
  Backup, Systems Manager, Caddy TLS, and PostgreSQL queues. No production access keys or public SSH.
- Customer data, logs, media, dumps, and snapshots remain in Malaysia except explicit Meta and
  hosted-model processing. Never send customer messages, identifiers, tickets, or media to the model.
- Retain chats 90 days, closed tickets/media 36 months, and backups at most 35 days, subject only to
  an approved legal hold.

The single host is accepted only for this capped beta. A host or Availability Zone failure may
interrupt service until restore; use multi-AZ data and application services for a broader launch.

## Change and rollback

- Promote the same reviewed image digest and migrations through temporary staging. Never reverse a
  migration by destroying data; forward-fix or restore.
- Disable outbound traffic immediately for suspected exposure, signature bypass, unauthorized admin
  access, prohibited action, unsafe advice/media, data loss, or uncontrolled duplicates.
- Pause or roll back after 15 minutes above 5% failures/duplicates, p95 above 30 seconds, total answer
  failure, availability below 99%, or a budget stop threshold. Preserve inbound events for replay.
- Resume only after staging passes and Cze Yik approves. Expansion beyond this contract requires a
  new availability, security, capacity, budget, and risk decision.
