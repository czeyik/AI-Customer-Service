# DUDU Car WhatsApp Pilot Launch Contract

Status: **APPROVED — 4 September 2026**  
Prepared: 4 September 2026  
Owner: Cze Yik

This contract does not authorize deployment, spending, invitations, or live traffic. All twelve
gates in `DELEGATION.md` must pass first.

## Pilot

- **Dates:** 15–29 September 2026. Readiness review: 12 September; go/no-go: 14 September.
  If any gate is not `PASS`, Cze Yik defers launch.
- **Region and cohort:** invitation-only in Kuala Lumpur and Selangor; up to 100 participants with
  Malay, Chinese, and Indian representation across rider, driver, and partnership scenarios.
- **Languages/channel:** English, Bahasa Malaysia, and Simplified Chinese on WhatsApp only.
- **Scope:** approved-knowledge answers, consent-first tickets, human escalation, and approved
  image/video attachments. Human coverage is 9:00 AM–6:00 PM daily, Malaysia time.
- **Limits:** 15,000 inbound messages total, 1,000/day, and 20/participant/day. One normal outbound
  bot reply per accepted inbound message. Rate limits and the outbound kill switch enforce this.

### Budget

External-service ceiling: **USD 70**, excluding staff, domain, and existing account costs.

| Area | Limit |
| --- | ---: |
| AWS Lightsail and supporting AWS services | USD 30 |
| Hosted LLMs | USD 15 |
| WhatsApp and email | USD 10 |
| Reserve for retries, extra logs/media scans, test templates, or brief release overlap | USD 15 |

AWS alerts: USD 15 forecast, USD 25 actual, USD 29 actual. Total-spend alerts: USD 35, USD 55,
and USD 65. At USD 65, pause enrolment; at USD 70, disable outbound traffic pending Cze Yik's
approval. Wave 11 must confirm the estimate before provisioning.

### Success criteria

1. Every production gate is `PASS`; no unresolved P0/P1 security, privacy, or data-loss incident.
2. At least 95% of valid WhatsApp texts receive exactly one accepted response within 30 seconds;
   retries create no duplicate ticket.
3. At least 95% of the trilingual release set passes; all safety, human-request, consent/name/email,
   prohibited-action, and uncertainty cases pass.
4. Reviewed responses contain no invented DUDU policy, price, commitment, account fact, or action.
5. At least 90% of feedback rates the answer useful or confirms correct uncertainty/escalation.
6. At least 90% of tickets meet their first-response target; every acknowledgement includes public
   ID, priority, target, and support hours.
7. Pilot availability is at least 99%, excluding evidenced Meta outages, and spend is at most USD 70.

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
  USD 65/actual spend at USD 70.
- Preserve inbound events for replay. Resume only after staging passes and Cze Yik approves.

## Architecture

```text
WhatsApp -> Meta -> Route 53 -> Lightsail static IP -> Caddy TLS -> FastAPI
                                                                  |
                                                     PostgreSQL queue/data
                                                        |             |
                                                      worker      dead letters
                                                   /    |    \
                                            knowledge  LLMs  private media
                                                   \    |    /
                                                    Meta outbound

GitHub -> GitHub Actions -> ECR image digest -> temporary staging -> Lightsail
```

- AWS Malaysia (`ap-southeast-5`); customer data, logs, media, dumps, and snapshots remain there.
  Meta and approved LLM calls are explicit external processing.
- One 4 GB Lightsail host runs Caddy, API, worker, and PostgreSQL containers. Only HTTPS is public;
  SSH is restricted break-glass access. There is no direct EC2, RDS, Fargate, SQS, NAT Gateway,
  load balancer, or WAF charge.
- PostgreSQL atomically stores unique provider message IDs and queue rows. The worker uses row
  locking, bounded retries, and dead-letter rows.
- Private Lightsail object storage holds quarantined/approved media. Names, email, tickets, and
  media are not sent to an LLM by default.
- Staging uses a temporary Lightsail host in a separate AWS account. Production stays dark until
  Wave 12. Infrastructure uses CloudFormation where supported plus a versioned host bootstrap.

> **ponytail:** One host/Availability Zone is accepted for this 100-participant, 1,000-message/day
> pilot. Host/zone failure may interrupt service until restore. Upgrade to multi-AZ RDS and multiple
> Fargate tasks behind an ALB when higher availability or capacity is required.

### Data classes

| Class | Examples | Handling |
| --- | --- | --- |
| Public | active CCO-approved knowledge, public ticket ID | Customer responses allowed; version and audit knowledge. |
| Internal | runbooks, aggregate metrics, audit metadata | Named staff/service access only. |
| Confidential | phone/user ID, name, email, messages, tickets, trip/account IDs, IP | Encrypt, least privilege, redact logs, enforce retention, minimize provider disclosure. |
| Restricted | credentials, signing/TOTP secrets, recovery data, quarantined uploads | Secrets Manager or quarantine only; never log or send to LLMs. |

Reject or redact payment cards, passwords, OTPs, API secrets, and identity numbers/documents.
Chats retain for 90 days; tickets/media for 24 months, subject to approved legal hold.

### Owned dependencies

| Dependency | Owner | Due |
| --- | --- | --- |
| AWS accounts, Malaysia/Lightsail enablement, billing, ECR and GitHub OIDC | Cze Yik | Waves 2/11 |
| GitHub protections and Actions | Cze Yik | Wave 2 |
| Domain and Route 53 | Cze Yik | Wave 11 |
| Meta WABA/app/number, API version and credentials | Cze Yik | Wave 4 |
| GLM/OpenAI accounts, models, terms and quotas | Cze Yik | Wave 5 |
| Support mailbox and WhatsApp templates | Jane; Cze Yik provisions | Wave 6 |
| Approved trilingual knowledge/customer copy | Jane | Waves 3/7 |
| Media limits, scanner and reviewer policy | Cze Yik | Wave 8 |
| Privacy, deletion/legal hold, monitoring, SLO and recovery decisions | Cze Yik | Waves 3/10/11 |

## Approval state

Approved by Cze Yik on 4 September 2026: the combined contract, including scope, thresholds,
notifications, change rules, USD 30 Lightsail design, USD 70 total ceiling, and ticket lifecycle.

Sources checked 4 September 2026: `docs/requirements-summary.md`,
`docs/security-launch-checklist.md`, [AWS Regions](https://docs.aws.amazon.com/global-infrastructure/latest/regions/aws-regions.html),
[AWS Lightsail pricing](https://aws.amazon.com/lightsail/pricing/), and
[WhatsApp pricing](https://whatsappbusiness.com/products/platform-pricing/).
