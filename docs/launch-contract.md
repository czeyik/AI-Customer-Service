# DUDU Car WhatsApp Public Beta Launch Contract

Status: **APPROVED — amended 10 September 2026**
Prepared: 4 September 2026  
Owner: Cze Yik

This contract does not authorize deployment, spending, invitations, or live traffic. All twelve
gates in `DELEGATION.md` must pass first.

## Public beta

- **Dates:** 10–14 September 2026. Activation begins only after the implementation, renewed
  validation and go/no-go gates pass; missed time is not backdated or extended automatically.
- **Region and audience:** publicly accessible WhatsApp beta for DUDU Car's Kuala Lumpur and
  Selangor service scope. There is no invitee list or participant-count target.
- **Languages/channel:** English, Bahasa Malaysia, and Simplified Chinese on WhatsApp only.
- **Scope:** approved-knowledge answers, consent-first tickets, human escalation, and approved
  image/video attachments. Human coverage is 9:00 AM–6:00 PM daily, Malaysia time.
- **Limits:** 10,000 unique inbound messages over the beta, 2,000/day globally, 200/user/day, and
  the existing 20/user/minute burst limit. Day boundaries use Malaysia time. Count only an
  authenticated, deduplicated inbound provider message; an attachment message counts once.
  Shared PostgreSQL counters enforce all limits atomically and fail closed. Alert at 80% and 90%;
  at 100%, stop normal processing and send at most one clear capacity response per affected user.
  One normal outbound bot reply is allowed per accepted inbound message.

### Budget

External-service ceiling: **USD 70**, excluding staff, domain, and existing account costs. AWS
infrastructure has a separate hard operating ceiling of **USD 30 per month**.

| Area | Limit |
| --- | ---: |
| AWS EC2 and supporting AWS services | USD 30 |
| Hosted LLMs | USD 15 |
| WhatsApp and email | USD 10 |
| Reserve for retries, extra logs/media scans, test templates, or brief release overlap | USD 15 |

AWS alerts: USD 20 forecast, USD 25 actual, and USD 28 actual. At USD 28, terminate staging and
freeze infrastructure expansion. At USD 30, disable outbound traffic and stop nonessential AWS
resources after a successful backup pending Cze Yik's approval. Total-spend alerts remain USD 35,
USD 55, and USD 65. At USD 65, pause new conversations; at USD 70, disable outbound traffic pending Cze
Yik's approval. Budgets and alarms are controls, not instantaneous billing cut-offs, so the owner
reviews daily cost during the public beta.

The production-only 730-hour price basis verified on 10 September 2026 is approximately USD 19.54:
USD 13.94 EC2, USD 3.65 public IPv4, USD 1.38 gp3, USD 0.40 Secrets Manager, and USD 0.16 for the
first snapshot's 3.59 GiB of billed blocks. CloudWatch usage stays within its published free tier
at the approved scale. Staging is temporary and must be deleted with its retained storage after
validation; growth in snapshots, logs, transfer, S3, or requests is governed by the USD 10/15/18
alerts and daily review.

### Success criteria

1. Every production gate is `PASS`; no unresolved P0/P1 security, privacy, or data-loss incident.
2. At least 95% of valid WhatsApp texts receive exactly one accepted response within 30 seconds;
   retries create no duplicate ticket.
3. At least 95% of the trilingual release set passes; all safety, human-request,
   consent/name/email/phone, prohibited-action, and uncertainty cases pass.
4. Reviewed responses contain no invented DUDU policy, price, commitment, account fact, or action.
5. At least 90% of feedback rates the answer useful or confirms correct uncertainty/escalation.
6. At least 90% of tickets meet their first-response target; every acknowledgement includes public
   ID, priority, target, and support hours.
7. Public-beta availability is at least 99%, excluding evidenced Meta outages; AWS spend is at
   most USD 30 per month and total external-service spend is at most USD 70.

### Non-goals

No Instagram, marketing broadcast, CRM, complex admin roles, general-purpose assistance, or
automated video analysis without later approval. The bot cannot perform refunds, cancellations,
bookings, payments, account actions/lookups, driver decisions, bans, or contracts.

## People and support

| Responsibility | Owner |
| --- | --- |
| Launch, go/no-go, production, infrastructure, releases, rollback and budget | Cze Yik |
| Privacy, security, incidents, risk acceptance and admin recovery | Cze Yik |
| Support lead, ticket assignment, participant communication and CCO | Jane |
| Administrators and escalation contacts | Cze Yik and Jane |

Ticket lifecycle: `open` → `in_progress` → `closed`. Assignment and priority are separate. Waiting
for the customer is a note; a new reply reopens a closed ticket to `open`. Changes are attributable
and audited. Customers receive acknowledgements and material updates on WhatsApp. Jane receives
new/reassigned-ticket email; urgent tickets and incidents notify Jane and Cze Yik by email plus an
approved WhatsApp template when required.

## Change and rollback rules

- Main changes use reviewed GitHub pull requests with passing checks. Cze Yik approves production
  releases; Jane approves customer knowledge/copy through her named CCO account.
- Promote the same image digest and migration set from temporary staging to production. Never roll
  back a schema by destroying customer data; forward-fix or use the approved restore procedure.
- Store secrets only in AWS Secrets Manager. Do not put them in chat, Git, images, logs, or handoffs.
- Immediately disable outbound traffic for suspected data/secret exposure, signature bypass,
  unauthorized admin access, prohibited action, wrong emergency advice, data loss, unsafe media,
  or uncontrolled duplicates.
- Pause or roll back after 15 minutes above 5% failures/duplicates, p95 latency above 30 seconds,
  complete LLM and deterministic-fallback failure, availability below 99%, or forecast spend above
  USD 65/actual spend at USD 70. The AWS-specific USD 18/20 actions in the budget section apply
  first.
- Preserve inbound events for replay. Resume only after staging passes and Cze Yik approves.

## Architecture

```text
WhatsApp -> Meta -> Route 53 -> EC2 Elastic IP -> Caddy TLS -> FastAPI
                                                                  |
                                                     PostgreSQL queue/data
                                                        |             |
                                                      worker      dead letters
                                                   /    |    \
                                            knowledge  LLMs  private media
                                                   \    |    /
                                                    Meta outbound

GitHub -> GitHub Actions OIDC -> ECR image digest -> temporary EC2 staging -> EC2 production
```

- AWS Malaysia (`ap-southeast-5`); customer data, logs, media, dumps, and snapshots remain there.
  Meta and approved LLM calls are explicit external processing.
- One Graviton `t4g.small` EC2 instance with an encrypted gp3 volume runs Caddy, API, workers,
  ClamAV, and PostgreSQL containers. Only HTTP/HTTPS is public; administration uses AWS Systems
  Manager Session Manager, with no public SSH port. There is no RDS, Fargate, SQS, NAT Gateway,
  load balancer, or WAF charge.
- PostgreSQL atomically stores unique provider message IDs and queue rows. The worker uses row
  locking, bounded retries, and dead-letter rows.
- Private Amazon S3 storage holds quarantined/approved media and encrypted database backups, with
  lifecycle expiry capped at 35 days for backups. The EC2 instance profile grants only the needed
  object prefixes; no AWS access keys are stored on the host. Names, email, tickets, and media are
  not sent to an LLM by default.
- Staging uses temporary, separately named EC2 resources, IAM roles, secrets, storage, and data in
  the same AWS account as production. Production stays dark until Wave 12. GitHub Actions receives
  short-lived AWS credentials through repository-scoped OIDC and promotes an immutable ECR digest.
- AWS Secrets Manager holds runtime secrets; CloudWatch receives bounded structured logs and
  metrics; Systems Manager provides operator access. Daily snapshots and hourly PostgreSQL backups
  target the approved one-hour RPO and four-hour RTO.

> **ponytail:** One 2 GB host/Availability Zone is accepted for this five-day,
> 2,000-message/day public beta only after renewed capped-volume validation passes. Host/zone failure may
> interrupt service until restore. Promotion to `t4g.medium` requires Cze Yik's explicit approval;
> upgrade to multi-AZ RDS and multiple application tasks when higher availability is required.

### Data classes

| Class | Examples | Handling |
| --- | --- | --- |
| Public | active CCO-approved knowledge, public ticket ID | Customer responses allowed; version and audit knowledge. |
| Internal | runbooks, aggregate metrics, audit metadata | Named staff/service access only. |
| Confidential | phone/user ID, name, email, messages, tickets, trip/account IDs, IP | Encrypt, least privilege, redact logs, enforce retention, minimize provider disclosure. |
| Restricted | credentials, signing/TOTP secrets, recovery data, quarantined uploads | Secrets Manager or quarantine only; never log or send to LLMs. |

Reject or redact payment cards, passwords, OTPs, API secrets, and identity numbers/documents.
Chats retain for 90 days; tickets/media for 36 months after ticket closure, subject to approved
legal hold.

### Owned dependencies

| Dependency | Owner | Due |
| --- | --- | --- |
| AWS account, Malaysia EC2 enablement, billing, ECR and GitHub OIDC | Cze Yik | Waves 2/11 |
| GitHub protections and Actions | Cze Yik | Wave 2 |
| `support.duducaradmin.com` and Route 53 | Cze Yik | Wave 11 |
| Meta WABA/app/number, API version and credentials | Cze Yik | Wave 4 |
| GLM/OpenAI accounts, models, terms and quotas | Cze Yik | Wave 5 |
| Support mailbox and WhatsApp templates | Jane; Cze Yik provisions | Wave 6 |
| Approved trilingual knowledge/customer copy | Jane | Waves 3/7 |
| Media limits, scanner and reviewer policy | Cze Yik | Wave 8 |
| Privacy, deletion/legal hold, monitoring, SLO and recovery decisions | Cze Yik | Waves 3/10/11 |

## Approval state

Approved by Cze Yik on 4 September 2026: the combined contract, including scope, thresholds,
notifications, change rules, USD 70 total ceiling, and ticket lifecycle. Architecture amendment
approved by Cze Yik on 10 September 2026: one AWS account with isolated staging/production,
`support.duducaradmin.com`, EC2 `t4g.small` as the initial production size, and a USD 20 monthly AWS
ceiling. On 10 September 2026, Cze Yik replaced the invitation-only pilot with the capped public
beta defined above and approved its USD 30 AWS ceiling. The staged public launch and its proposed
availability, security, capacity and budget contract are explicitly deferred. Promotion to
`t4g.medium` or any post-beta public launch requires separate approval.

Activation amendment approved by Cze Yik on 10 September 2026: support notifications may remain
disabled through 14 September because production SMTP and notification templates are not
provisioned. Cze Yik and Jane monitor the admin dashboard during this waiver. This does not waive
customer Meta replies, ticket creation, audit logging, urgent handling or any security/data-loss
rollback trigger.

Sources checked 4 September 2026: `docs/requirements-summary.md`,
`docs/security-launch-checklist.md`, [AWS Regions](https://docs.aws.amazon.com/global-infrastructure/latest/regions/aws-regions.html),
and [WhatsApp pricing](https://whatsappbusiness.com/products/platform-pricing/). Amendment sources
checked 10 September 2026: [AWS EC2 T4g](https://aws.amazon.com/ec2/instance-types/t4/) and
[Amazon VPC pricing](https://aws.amazon.com/vpc/pricing/).
