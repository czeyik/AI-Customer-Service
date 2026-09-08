# Production Pilot Delegation

Last assessed: 2 September 2026

Purpose: guide one fresh Codex agent through each sequential wave until the WhatsApp pilot is
live. This file tracks execution; it does not replace the project requirements.

## Sources of Truth

- Agent operating guidelines: `AGENTS.md`
- Product requirements: `docs/requirements-summary.md`
- Launch security requirements: `docs/security-launch-checklist.md`
- Repository and tests: current code, not this dated assessment

Every wave agent must read `AGENTS.md` and both requirement documents before working. If they
conflict with this file, stop and ask the owner which document to amend.

## Current Status

The repository is an MVP, not a production system. It has basic chat, approved-knowledge
retrieval, tickets, guardrails, Meta webhook parsing, and a read-only admin inbox.

Assessment evidence:

- Nine unit tests pass when unrelated system pytest plugins are disabled.
- Normal pytest startup and `pip check` are not clean in the current local environment.
- Docker Compose configuration parses, but no production build or deployment was proven.

Critical blockers:

- WhatsApp receives messages but cannot send replies; webhook processing is not idempotent.
- WhatsApp ticket intake is stateless and cannot collect consent, name, and email across messages.
- Human escalation, partnership intake, required acknowledgements, and real media are incomplete.
- Hosted LLM release evaluation does not yet exist.
- Knowledge is English-only, shared-key published, unversioned, and not CCO-attributable.
- Administration uses a shared TOTP configuration and lacks an operational ticket workflow.
- Retention jobs, production security evidence, CI/migrations, infrastructure, monitoring,
  backup/restore, and rollback are absent.
- Meta/provider access, hosting, approved content, privacy decisions, owners, and pilot thresholds
  are not documented.

Real customer traffic must remain disabled until every gate below is `PASS`.

## Gate and Wave Tracker

| Wave | Gate | Focus | Status |
| ---: | --- | --- | --- |
| 1 | PG-01 | Approved launch contract and architecture | PASS |
| 2 | PG-02 | Reproducible build, migrations, and CI | PASS |
| 3 | PG-03 | Stateful multilingual ticket flow | PASS |
| 4 | PG-04 | Reliable WhatsApp send/receive path | PASS |
| 5 | PG-05 | Grounded hosted LLM and outage fallback | PASS |
| 6 | PG-06 | Named admins and ticket operations | PASS |
| 7 | PG-07 | CCO knowledge governance and launch corpus | NOT_STARTED |
| 8 | PG-08 | Secure image/video pipeline | NOT_STARTED |
| 9 | PG-09 | Application security | NOT_STARTED |
| 10 | PG-10 | Privacy, retention, and deletion | NOT_STARTED |
| 11 | PG-11 | Production platform and operations | NOT_STARTED |
| 12 | PG-12 | Release validation and pilot activation | NOT_STARTED |

Valid statuses: `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `PASS`.

## Information Required From the Owner

Agents should ask only for unresolved inputs needed by their wave. Never paste secrets into chat
or commit them; provision secrets directly in the chosen secret manager.

| Input | Required decision or information | Status | Waves |
| --- | --- | --- | --- |
| OI-01 | Pilot date, region, cohort, traffic/volume limits, duration, budget, success measures, rollback triggers, and go/no-go owner | RESOLVED | 1, 11, 12 |
| OI-02 | Hosting/staging platform, cloud region, domain/DNS, data residency, Git/CI/registry workflow, and resource/release owners | UNRESOLVED | 1, 2, 11 |
| OI-03 | Human support workflow, assignees, ticket statuses/notifications, escalation contacts, admin roster, CCO identity, and recovery approver | RESOLVED | 1, 3, 6, 7 |
| OI-04 | Meta Business/WABA/app/phone readiness, API version, opt-in approval, test recipients, and secure credential provisioning | RESOLVED | 4, 12 |
| OI-05 | Z.AI account, exact enabled model ID, data terms, region, quotas, timeout, availability needs, spend limit, and outage fallback approval | RESOLVED | 5, 12 |
| OI-06 | CCO-approved knowledge and customer copy in all three languages, including bot disclosure, emergency, consent, partnership, and WhatsApp profile text | UNRESOLVED | 3, 7, 12 |
| OI-07 | Allowed media types/sizes, private object store, malware scanner, reviewer access policy, signed-link lifetime, and media-analysis policy | UNRESOLVED | 8, 10, 11 |
| OI-08 | Privacy notice, controller/contact, deletion versus anonymization, legal holds, backup retention, incident owner, security owner, secret manager, scan policy, and risk approver | UNRESOLVED | 3, 9, 10, 11, 12 |
| OI-09 | Logging/metrics/error tools, alerts, on-call roster, SLOs, maintenance window, RPO/RTO, and incident/operational escalation path | UNRESOLVED | 11, 12 |

When an owner input is resolved, update its status and record the decision in the wave handoff.
Do not silently decide legal, privacy, budget, credential, risk-acceptance, or go-live questions.

## Mandatory Workflow for Every Wave

1. Read this file, `AGENTS.md`, and both requirement documents. Reinspect Git status, relevant
   code, and prior handoffs.
2. Work only on the requested wave. Verify all earlier waves are `PASS` before starting.
3. Ask for unresolved owner inputs listed for the wave. Do not request secrets in chat.
4. Call `get_goal`. When inputs are available, call `create_goal` with:
   **“Complete Wave N from DELEGATION.md, meet its exit criteria, record evidence and handoff in
   DELEGATION.md, and do not begin Wave N+1.”** Do not set a token budget unless requested.
5. Mark the wave `IN_PROGRESS`. Preserve user changes and keep discoveries for later waves in the
   handoff rather than expanding scope.
6. Implement and test only the assigned work. For external APIs, verify current official
   documentation and record exact API/model versions and verification date.
7. Do not push, merge, deploy, rotate credentials, enable billable/live traffic, or contact pilot
   users unless the owner explicitly authorizes that action for the wave.
8. Run the exit checks. Mock-only tests cannot prove an external integration gate.
9. Update the tracker, resolved inputs, and handoff before calling
   `update_goal(status="complete")`. Set the gate to `PASS` only when every exit criterion has
   evidence. Follow the goal function's rules for a genuine repeated blocker.
10. Report whether the next wave is unblocked, then stop. Do not begin it.

## Wave Briefs

### Wave 1 — Launch Contract and Architecture

Inputs: OI-01, OI-02, OI-03 and approvers for OI-08.

- Confirm pilot scope, non-goals, owners, measurable release/rollback thresholds, and change rules.
- Document the production component/data flow, data classification, external services, and release
  architecture. Assign every unresolved dependency an owner.

Exit: the owner approves a testable launch contract and no architecture decision blocks Wave 2.

### Wave 2 — Reproducible Delivery Foundation

Inputs: OI-02.

- Standardize Python, isolate tests from system plugins, lock dependencies, and make integrity
  checks clean.
- Add PostgreSQL migrations, CI, clean-install tests, and fail-closed production configuration
  validation. Keep development Compose separate from production deployment.

Exit: a clean supported environment passes tests/checks and a fresh PostgreSQL database migrates
from zero in green CI.

### Wave 3 — Stateful Customer and Ticket Flow

Inputs: OI-03, OI-06, relevant OI-08 decisions.

- Implement persistent multi-message intake and all behaviour required by R3–R6 and R12, including
  mandatory consent/name/email, human requests, safety, complaints, partnerships, bot identity,
  prohibited-action refusals, priorities, response targets, and human-hours wording.
- Finalize each response, ticket, audit, and message in one transaction; remove unused language,
  safety-assessment, and conversation fields unless the new flow actively uses them.
- Test complete and interrupted flows in English, Bahasa Malaysia, and Simplified Chinese.

Exit: service/API tests prove required fields cannot be bypassed and every launch flow has approved
localized behaviour.

### Wave 4 — WhatsApp Transport

Inputs: OI-04.

- Add versioned Meta outbound messaging, strict signature validation, message-ID idempotency,
  bounded retries, delivery/error handling, safe logs, and the approved queue boundary.
- Connect WhatsApp messages to Wave 3 state, make the send flag a real traffic kill switch, and
  remove the unused Instagram path. Keep public traffic disabled.

Exit: a real Meta test number completes one multi-turn ticket exactly once, including retry and
invalid-signature tests.

### Wave 5 — Hosted LLM and Outage Fallback

Inputs: OI-05 and provider-related OI-08 decisions.

- Verify the exact current model and implement a provider-neutral adapter with timeouts, limits,
  telemetry, data minimization, grounding checks, and deterministic outage fallback.
- Add contract tests and an authorized live smoke test for the configured provider and failure
  paths.

Exit: the configured model and total-provider-outage fallback pass with no unnecessary personal
data sent or logged.

### Wave 6 — Administration and Ticket Operations

Inputs: OI-03.

- Add named accounts, per-user 2FA, safe provisioning/disable/recovery, attributable audit events,
  and the minimum approved ticket assignment/status/notes/notification workflow.
- Establish the named CCO authority required by Wave 7.

Exit: two distinct test admins can be managed and audited, and support owners accept the tested
ticket lifecycle.

### Wave 7 — Knowledge Governance and Corpus

Inputs: OI-03, OI-06.

- Replace shared-key publication with named CCO actions; add versions, source/effective metadata,
  activation/removal, rollback, audit, and retrieval exclusion of non-current content.
- Use one ingestion function for API and seed data. Replace fetch-all retrieval with the selected
  bounded production method; either use real vector search or remove the dead hash embeddings and
  pgvector setup.
- Ingest only CCO-approved English, Bahasa Malaysia, and Simplified Chinese pilot content and test
  retrieval, uncertainty, traceability, and coverage.

Exit: CCO publish/update/remove/rollback is fully attributable and the approved trilingual corpus
passes retrieval tests.

### Wave 8 — Secure Media

Inputs: OI-07 and applicable OI-08 decisions.

- Implement authenticated Meta media download, streaming limits, content sniffing, quarantine,
  malware scan, private object storage, ticket linkage, integrity metadata, and secure reviewer
  access. Keep media outside PostgreSQL and LLMs by default.
- Add rejection, authorization, corruption, duplicate, failure, and deletion-link tests.

Exit: approved image/video reaches one ticket and authorized reviewer; unsafe or failed media never
becomes accessible.

### Wave 9 — Application Security

Inputs: OI-08.

- Complete the threat model and `docs/security-launch-checklist.md` application controls: secure
  sessions/CSRF, authorization, fail-closed config, shared abuse controls, safe logging, injection,
  PII, upload, webhook, and prohibited-action testing.
- Replace the unbounded process-local rate limiter with the approved shared, bounded implementation.
- Add secret, dependency, static, container, and dynamic scans with approved severity gates.

Exit: no unaccepted launch-blocking finding remains and every application-security checklist item
maps to repeatable evidence.

### Wave 10 — Privacy and Data Lifecycle

Inputs: OI-07, OI-08.

- Finalize the data inventory and implement idempotent 90-day chat and 36-month ticket/media
  lifecycle jobs across databases, objects, indexes, logs where applicable, and backup handling.
- Add narrow audited legal holds, dry runs, time-controlled tests, failure alerts, and runbooks.

Exit: tests prove retention, deletion/anonymization, holds, retries, object/index coverage, and no
unowned data store.

### Wave 11 — Production Platform and Operations

Inputs: OI-01, OI-02, OI-07, OI-08, OI-09.

- Provision immutable production API/worker/data services, TLS/DNS, secrets, least privilege,
  encryption, migrations, health/readiness, observability, alerts, SLOs, capacity, and runbooks.
- Prove backup/restore, retention after restore, RPO/RTO, deploy/rollback, and dependency failures.
  Deploy dark with Meta production traffic disabled.

Exit: dark production, alerts, restore, and rollback pass in the intended accounts without real
customer traffic.

### Wave 12 — Release Validation and Pilot Activation

Inputs: final approval of OI-01 and OI-03–OI-09.

- Freeze a release candidate and run the complete suite plus representative trilingual DUDU
  evaluation for every required scenario, real WhatsApp text/media, admin review, failures,
  security, operations, and cost/latency thresholds.
- Close the launch checklist and hold a documented go/no-go. If approved, enable only the agreed
  cohort/limits, monitor the observation window, and pause or roll back on a trigger.

Exit: all gates are `PASS`, no unresolved P0/P1 or unapproved waiver remains, the approved cohort
uses production successfully within thresholds, and rollback remains ready.

## Handoff Record

Append one entry per wave; do not erase earlier evidence.

```text
### Wave N — YYYY-MM-DD
Status: PASS | BLOCKED
Owner decisions:
Files/migrations and commit/PR/release:
Verification commands/results:
External evidence (no secrets or customer data):
Gate update and residual risks:
Next-wave notes:
```

### Wave 1 — 2026-09-04
Status: PASS
Owner decisions: Target 15 September 2026 for a 15-day invitation-only pilot in Kuala Lumpur and
Selangor, representing Malay, Chinese, and Indian communities; Cze Yik is launch, go/no-go,
production, infrastructure, release, recovery, privacy, security, incident, and risk owner. AWS
Malaysia is the hosting region and GitHub is the existing Git host. Jane is support lead and CCO;
Cze Yik and Jane are administrators and escalation contacts. Cze Yik requested recommendations
for traffic, budget, success/rollback thresholds, ticket states, notifications, CI, registry, and
production architecture. Those recommendations are recorded in `docs/launch-contract.md`. Cze Yik
approved the USD 30 Lightsail architecture and its USD 70 total external-service ceiling on 4
September 2026, then approved simplifying tickets to `open` → `in_progress` → `closed`. After the
requested concise revision, Cze Yik accepted the combined contract and directed Wave 1 to proceed.
Files/migrations and commit/PR/release: Added `docs/launch-contract.md`; updated this tracker. No
migration, commit, PR, release, infrastructure change, billable resource, or live traffic.
Verification commands/results: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/pytest -q` passed 9
tests in 5.70s; `./.venv/bin/pytest -q -p no:cacheprovider` passed 9 tests in 5.51s;
`git diff --check` and launch-contract structure/content checks passed. Normal shell `pytest` was
not on `PATH`; the repository virtual environment was used directly. After final owner revisions,
the approval/status/required-section assertions and `git diff --check` passed, and the same 9 tests
passed in 5.90s.
External evidence (no secrets or customer data): On 4 September 2026, official AWS documentation
confirmed `ap-southeast-5` is the opt-in Malaysia Region with three Availability Zones and that a
4 GB Lightsail Linux instance with public IPv4 is USD 24/month, billed hourly. The official
WhatsApp Business Platform pricing page confirmed per-delivered-message pricing and no charge for
service messages inside the user-opened 24-hour service window. Exact URLs and review date are
recorded in `docs/launch-contract.md`.
Gate update and residual risks: PG-01 is `PASS`. The approved contract records testable scope,
success and rollback thresholds, support workflow, component/data/release architecture, data
classification, change rules, and owners for unresolved dependencies. OI-01 and OI-03 are
`RESOLVED`. OI-02 remains open only for the final domain; Cze Yik owns it for Wave 11 and it does
not block Wave 2. Later-wave OI-08 decisions remain open. The accepted single-host availability
limit and upgrade path are explicit. Real customer traffic remains disabled.
Next-wave notes: Wave 2 is unblocked. Use GitHub Actions, Amazon ECR, PostgreSQL migrations, and the
approved Lightsail release target; do not provision or deploy production resources in Wave 2.

### Wave 2 — 2026-09-04
Status: PASS
Owner decisions: Wave 1 selected Python 3.11, GitHub Actions, Amazon ECR, PostgreSQL, and the
approved Lightsail release target. The remaining domain decision belongs to Wave 11 and does not
block this wave. Authorization to commit/push, open a pull request, and configure required branch
checks was requested after three consecutive goal turns and authorized by Cze Yik on 4 September
2026.
Files/migrations and commit/PR/release: Added the Python version contract, direct dependency input
and hash-locked transitive requirements, Alembic configuration and initial migration
`f371a5ab9b0b`, production configuration validation and tests, development Compose migration
startup, a digest-pinned application image build, Docker-context exclusions, GitHub Actions CI,
and delivery documentation. Removed application-startup `create_all`; migrations now own schema
creation. Commits `20cefd7` and `9dd52a5` are on branch `wave-2-delivery-foundation` in pull request
[#1](https://github.com/czeyik/AI-Customer-Service/pull/1) against `dev`. The completed Wave 1 commit
was synchronized to `origin/dev`. No merge, ECR publication, release, infrastructure change,
billable resource, or live traffic occurred.
Verification commands/results: A clean Python 3.11 image installed `requirements.txt` with
`--require-hashes`, passed `pip check`, and passed 20 isolated tests. The image built successfully
as `dudu-support:wave2-check`; `.env` was absent from it. Against an isolated empty PostgreSQL 16
container, the image ran `alembic upgrade head`, reported no model/schema drift from
`alembic check`, created eight public tables including `alembic_version`, and initialized one
development admin after migration. `docker compose config --quiet`, `git diff --check`, and
Actionlint 1.7.12 passed. The temporary database container/network were removed after verification.
GitHub Actions run
[33787943728](https://github.com/czeyik/AI-Customer-Service/actions/runs/33787943728) then passed the
clean Python 3.11 install, dependency integrity, zero-to-head PostgreSQL migration and schema-drift
check, 20 isolated tests, and application-image build in 45 seconds.
External evidence (no secrets or customer data): GitHub Actions is enabled. Pull request #1 has the
successful `test` check from the `github-actions` app on commit `9dd52a5`. Strict `test` status-check
protection is enabled and enforced for administrators on both `dev` and `main`. The first hosted
run also passed; its Node.js 20 deprecation annotation was eliminated by updating to
`actions/checkout@v5` and `actions/setup-python@v6` before the final green run.
Gate update and residual risks: PG-02 is `PASS`. The supported clean environment and fresh
PostgreSQL migration are green in hosted CI, dependencies and the base image are integrity-pinned,
unsafe production defaults fail closed, and development Compose remains separate from production
deployment. Pull request #1 remains open for owner review; nothing was merged or released.
Next-wave notes: Wave 3 is unblocked after pull request #1 is reviewed and integrated. Start from
the integrated Wave 2 migration head and do not restore application-startup schema creation.

### Wave 3 — 2026-09-04
Status: PASS
Owner decisions: Cze Yik supplied `https://duducar.co/privacy-notice` and asked for concise
English, Bahasa Malaysia, and Simplified Chinese customer copy plus chatbot-specific Privacy
Notice additions. On 5 September, Cze Yik required the ticket flow to collect a WhatsApp contact
number, a brief issue description, relevant ride details, and optional supporting evidence, and
confirmed `support@duducar.co` as the privacy contact. Cze Yik also approved permanent
ticket/attachment deletion 36 months after ticket closure and instructed that all authoritative
sources be amended. Jane approved the original trilingual customer copy on 5 September. Cze Yik
then requested warmer, kinder, more caring, friendly, and appropriately cheerful wording across
all candidates. Jane approved the revised situation-adaptive trilingual wording and tone policy
on 5 September, resolving the Wave 3 portion of OI-06. The Wave 3 privacy portion of OI-08 is
resolved; later-wave knowledge, provider, legal-hold, backup, and operational decisions remain
`UNRESOLVED` in the global input tracker.
Files/migrations and commit/PR/release: Added persistent conversation intake state, mandatory
ticket contact/consent constraints, normalized WhatsApp contact capture, brief-description and
ride-detail collection, optional evidence metadata, stateful trilingual ticket intake, explicit
human and partnership routing, localized safety/complaint/uncertainty/prohibited-action behaviour,
an approved warm, kind, concise, supportive automated-assistant voice, priority and support-hours
acknowledgements, natural-language language switching, transaction rollback, and migration
`bd20fbc9188d`. Added `docs/wave-3-customer-copy.md`,
`docs/privacy-notice-chatbot-addendum.md`, and focused service/API tests. Changes remain uncommitted
on `dev`; no commit, push, PR, merge, release, external configuration, billable action, or live
traffic occurred.
Verification commands/results: Before the 5 September intake additions,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/pytest -q` passed 62 tests in 9.94s. The
digest-pinned Python 3.11 application image built as `dudu-support:wave3-check`; after those
additions and the warmer, appropriately cheerful personality revision, its clean locked
environment passed all 64 tests in 9.61s and `python -m pip check`
reported no broken requirements. From that image, a fresh
PostgreSQL 16 database migrated from zero to `bd20fbc9188d (head)` and `alembic check` reported
`No new upgrade operations detected.` `git diff --check` passed. Temporary test containers and
network were removed.
External evidence (no secrets or customer data): The live DUDU Car Privacy Notice was reviewed on
4 September 2026. The Malaysian Personal Data Protection Commissioner's official quick guide
states that a privacy notice must be available in the national and English languages; its
Personal Data Protection Standard requires permanent deletion when data is no longer needed. The
official Malaysia government portal confirms 999 as the national emergency line. Exact URLs and
the review date are recorded in the two Wave 3 draft documents.
Gate update and residual risks: PG-03 is `PASS`. Technical tests prove that
consent, name, valid email, normalized WhatsApp contact, and a non-empty issue description cannot
be bypassed; complete and interrupted intake persists; ride/evidence intake is recorded; and
required launch behaviour is localized in all three languages. Jane's approval of the revised
tone policy and wording, the privacy contact, and the 90-day/36-month retention decisions are
recorded. Actual media storage and scanning remain Wave 8 work. Real customer traffic remains
disabled.
Next-wave notes: Wave 4 is unblocked but has not started. It requires OI-04 before WhatsApp
transport work begins.

### Wave 4 — 2026-09-05
Status: BLOCKED
Owner decisions: Cze Yik approved Meta Graph API `v26.0` and the named-business WhatsApp pilot
opt-in wording/process on 5 September 2026. Two test recipients are owner-authorized and control
their test numbers. The Meta business number is registered, but the app remains unpublished and
the callback setup is incomplete. No credential was supplied in chat or committed. OI-04 remains
`UNRESOLVED` until a non-placeholder verify token, App Secret, access token, Phone Number ID, and
public HTTPS callback are securely provisioned and the real-number test succeeds.
Files/migrations and commit/PR/release: Added strict WhatsApp-only webhook parsing and signature
validation, Meta message-ID idempotency, a transactional PostgreSQL inbox/outbox boundary,
Wave 3 state integration, Graph API `v26.0` text sending, the outbound kill switch, bounded retry
and dead-letter handling, delivery/error status updates, safe audit metadata, a worker entry point,
and migration `72b9b57f6d1a`. Removed the Instagram request/parser path. Updated configuration,
development commands, and focused tests. Changes remain uncommitted on `dev`; no commit, push, PR,
merge, release, production deployment, billable action, live traffic, or pilot-user contact
occurred.
Verification commands/results: The clean digest-pinned Python 3.11 image built as
`dudu-support:wave4-check`; `python -m pip check` reported no broken requirements and all 79 tests
passed in 12.14s. The 14 focused transport checks cover plaintext callback verification,
fail-closed invalid/missing signatures, atomic rollback, exactly-once multi-turn ticket intake
under duplicate delivery, the disabled send switch, approved versioned endpoint, transient Meta
errors, bounded retry exhaustion, dead letters, and signed delivery/failure updates. A fresh
PostgreSQL 16 database migrated from zero to `72b9b57f6d1a (head)`; `alembic check` reported
`No new upgrade operations detected.` `docker compose config --quiet`, compile checks, and
`git diff --check` passed.
External evidence (no secrets or customer data): Official Meta documentation reviewed on
5 September 2026 lists Graph API `v26.0` as released 29 July 2026; documents the
`POST /<PHONE_NUMBER_ID>/messages` endpoint, `X-Hub-Signature-256` validation, system-user access
tokens and permissions, opt-in requirements, delivery/error status webhooks, and retryable error
codes. Sources: `https://developers.facebook.com/docs/graph-api/changelog/versions`,
`https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started`,
`https://developers.facebook.com/documentation/business-messaging/whatsapp/access-tokens`,
`https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in`,
`https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint`,
`https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages`,
and `https://developers.facebook.com/documentation/business-messaging/whatsapp/support/error-codes`.
Gate update and residual risks: PG-04 is `BLOCKED`, not `PASS`. The local transport requirements
have repeatable evidence, and outbound traffic defaults to disabled. The mandatory external exit
check is missing: Meta cannot verify a callback or deliver real tester messages without the four
runtime values and a public HTTPS endpoint; the owner's Meta screenshot also states that an
unpublished app receives dashboard test webhooks only. No real Meta test number has completed a
multi-turn ticket or confirmed outbound delivery.
Next-wave notes: Wave 5 is blocked. Resume Wave 4 after the owner securely configures the four
runtime values, supplies the non-secret Phone Number ID, authorizes or provides a public HTTPS
endpoint, and publishes the Meta app as required for real tester traffic. Do not begin Wave 5.

Resume update — 2026-09-07: The owner provisioned the four Meta runtime values in the ignored
local environment file, whose permissions were tightened to owner-only, and authorized a
temporary HTTPS tunnel. The public callback challenge passed, Meta accepted the callback,
the `messages` field was subscribed, and one signed dashboard test webhook was accepted. The
dashboard sample correctly created no customer inbox/outbox row. The owner requested that the
current Wave 4 implementation be committed locally on `dev`; no push, PR, merge, release, or
production deployment was authorized. PG-04 remains incomplete pending permanent-token/app
publication confirmation, explicit authorization to enable outbound test traffic, and the real
multi-turn test-number result.

Completion update — 2026-09-09: Status: PASS. The owner replaced the expired token with a valid
non-expiring system-user token for the expected Meta app, confirmed the required WhatsApp
permissions, corrected the registered Phone Number ID, published the app, and explicitly
authorized outbound testing to the two owner-controlled testers. The callback was verified over
a temporary HTTPS tunnel and the app was subscribed to the WABA that owns the registered business
number. One authorized tester completed the five-turn human-support intake. Database evidence
showed five unique Meta inbound message IDs, five outbound rows with provider IDs, all five final
delivery states `read`, one ticket, one `ticket_created` audit, and the conversation returned to
`idle`; no duplicate ticket or message processing occurred. The retry, retry-exhaustion,
dead-letter, invalid/missing-signature, atomic-rollback, and duplicate-delivery paths remain covered
by the 14 focused transport tests. The clean Wave 4 image passed all 79 tests in 12.78s and
`python -m pip check`; `docker compose config --quiet` and `git diff --check` passed. Implementation
commit `da714d5` remains local on `dev`; no push, PR, merge, release, production deployment, or
traffic beyond the authorized testers occurred. After collecting evidence, `META_SEND_ENABLED`
was returned to `false`, the outbound worker and temporary tunnel were stopped, and only the local
API/database development services remained running. PG-04 is `PASS`; Wave 5 is unblocked but was
not started.

### Wave 5 — 2026-09-09
Status: PASS
Owner decisions: Cze Yik approved Z.AI `glm-5.3-flash` as the only hosted pilot model and removed
GPT-5.6 Luna from the approved requirements. The deterministic approved-knowledge responder is
the provider-outage fallback. Cze Yik approved an eight-second timeout, 8,000-character total
prompt limit, 300-token output limit, no model tools or hosted web search, the existing USD 15
hosted-LLM pilot ceiling, and Z.AI API processing in Singapore under its API DPA. Only approved
support knowledge may be sent; customer messages, names, telephone numbers, email addresses,
external user IDs, ticket data, and attachments remain in DUDU-controlled storage. The ignored
local `ZAI_API_KEY` was provisioned with owner-only file permissions. Cze Yik explicitly
authorized one billable synthetic live smoke call on 9 September 2026.
Files/migrations and commit/PR/release: Added the provider-neutral text-generation contract, Z.AI
chat-completions client, fail-closed production configuration, bounded provider request/response,
content-free telemetry, cited-output validation, unsafe/ungrounded rejection, and deterministic
outage fallback. Added focused provider, grounding, outage, configuration, and end-to-end
data-minimization tests; updated the environment example, README, privacy-notice draft, approved
requirements, and security checklist for the owner-approved single-provider decision. No schema
migration was needed. Changes remain uncommitted on `dev`; no push, PR, merge, release,
production deployment, public traffic, or pilot-user contact occurred.
Verification commands/results: The clean digest-pinned Python 3.11 image built as
`dudu-support:wave5-check`; `python -m pip check` reported no broken requirements, all 91 tests
passed in 15.24s, and compile checks passed. The tests prove the eight-second and 300-token API
contract, absence of tools, exact-model response validation, bounded prompt and provider response,
safe telemetry, invalid citation/number/commitment rejection, total-provider-outage fallback, and
that a FAQ containing a name, email, telephone number, and external user ID sends none of them to
the hosted provider. `docker compose config --quiet` and `git diff --check` passed.
External evidence (no secrets or customer data): Official Z.AI chat-completions documentation
reviewed on 9 September 2026 confirmed the HTTPS endpoint, bearer authentication, synchronous JSON
responses, `max_tokens`, JSON response format, usage telemetry, finish reasons, and references to
GLM-5.3-FLASH. The parameter enum did not consistently list the Flash identifier, so the
authorized live call supplied stronger account-specific evidence: `glm-5.3-flash` returned a
grounded response with a provider request ID in 1,826 ms using 196 prompt and 23 completion tokens.
The synthetic request contained one public fare sentence and no customer data. Z.AI's API DPA,
also reviewed on 9 September, states that API customer data is generally processed in Singapore,
API content is processed in real time and not stored, and API end-user content is not used to
develop or improve services without explicit agreement. Sources:
`https://docs.z.ai/api-reference/llm/chat-completion` and
`https://docs.z.ai/legal-agreement/privacy-policy`.
Gate update and residual risks: PG-05 is `PASS`. The configured hosted model and complete-provider
outage path both pass, and repeatable tests prove that no customer identifiers reach provider
payloads or telemetry. `LLM_ENABLED` remains false in the local development environment after the
smoke test, and Meta outbound traffic remains disabled. The full approved trilingual corpus and
release-quality/cost evaluation remain Wave 7 and Wave 12 work respectively.
Next-wave notes: Wave 6 is unblocked after these changes are reviewed and integrated. Do not send
customer text or add model tools when extending the adapter. Wave 6 has not started.

### Wave 6 — 2026-09-09
Status: PASS
Owner decisions: OI-03 was already resolved by the approved Wave 1 launch contract. Cze Yik and
Jane are the named administrators and escalation contacts; Jane is support lead, ticket-assignment
owner, and CCO; Cze Yik is the admin-recovery approver. The approved lifecycle remains `open` →
`in_progress` → `closed`, with assignment separate from status, waiting-for-customer represented
as an internal note, and a new customer reply reopening a closed ticket. Jane receives
new/reassigned-ticket email; urgent-ticket notifications target Jane and Cze Yik by email and,
when configured with an approved template, WhatsApp. Customer material status updates use approved
localized WhatsApp templates. Cze Yik's 4 September approval of the combined launch contract and
ticket lifecycle remains the recorded support-owner acceptance; no owner decision changed here.
Files/migrations and commit/PR/release: Added named administrator identity, individual TOTP secret
references, CCO and recovery-authority attribution, active/disabled state, session revocation,
authenticated provisioning/disable/recovery commands, and login/management audit events. Added
ticket assignment, constrained status transitions, closure timestamps, attributable internal
notes, customer-reply reopening, the admin operations UI, CSRF protection on mutations, durable
email/WhatsApp notification records, bounded notification delivery/dead-letter handling, and Meta
approved-template sending. Migration `11d254641917` disables the legacy shared account while
preserving its audit identity and requires explicit named-account provisioning. Updated runtime
configuration, environment example, README, and current-gap documentation. Changes remain
uncommitted on `dev`; no commit, push, PR, merge, release, production deployment, credential
change, real administrator provisioning, external notification, billable action, or public traffic
occurred.
Verification commands/results: The clean digest-pinned Python 3.11 image built as
`dudu-support:wave6-check`; `python -m pip check` reported no broken requirements and all 94 tests
passed in 24.37s. Four focused Wave 6 tests prove two distinct named admins with separate password
and TOTP checks, CCO/recovery authority, audited bootstrap and second-admin provisioning,
fail-closed no-fallback 2FA, authenticated login, CSRF rejection, disable/recovery and session
version invalidation, last/self-disable guards, assignment, valid-only status transitions,
closure/reopening, attributable notes, correct normal/urgent recipients, localized approved-template
selection, successful email/WhatsApp adapters, and notification dead-letter handling. A disposable
PostgreSQL 16 database upgraded from zero to `11d254641917 (head)` and `alembic check` reported
`No new upgrade operations detected.` A second disposable database migrated through the Wave 5
head with a legacy active shared admin row, then upgraded successfully; the row remained present
but was disabled with a non-login legacy TOTP reference. Compile checks, the admin management CLI
entry point, and `git diff --check` passed. Temporary database containers were removed.
External evidence (no secrets or customer data): Meta's official WhatsApp Business Platform Cloud
API collection and Meta-hosted SDK reference were reviewed on 9 September 2026. They confirm that
an existing approved/enabled template is sent with a template name, deterministic language code,
and component parameters through the Cloud API. Sources:
`https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api`
and `https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/messages/template/`. The transport
continues to use the Wave 4-approved Graph API `v26.0` endpoint.
Gate update and residual risks: PG-06 is `PASS`. The tested workflow meets the approved lifecycle,
and the two test administrators are individually managed and attributable. TOTP values are not
stored in PostgreSQL; the CLI emits each value once for storage under its database reference in
AWS Secrets Manager. The named production accounts, support mailbox credentials, administrator
contact values, and Meta-approved template names still require secure provisioning in the target
environment before notification delivery is enabled. Both notification and Meta send kill switches
remain false by default, and no live delivery was attempted.
Next-wave notes: Wave 7 is unblocked but has not started. Use `AdminUser.is_cco` and the authenticated
named-admin session for CCO knowledge actions; remove the shared knowledge API key rather than
adding a second authorization path. Do not store TOTP material in the database or knowledge audit.

## Fresh-Chat Prompt

> Read `DELEGATION.md`, `AGENTS.md`, `docs/requirements-summary.md`, and
> `docs/security-launch-checklist.md`. Execute only Wave N using the mandatory workflow. Reinspect
> the repository, verify prerequisites, and ask only for unresolved inputs needed by this wave.
> Then create the prescribed goal and pursue it until every exit criterion is evidenced or the
> goal function's genuine blocked condition applies. Update `DELEGATION.md` before completing the
> goal. Do not begin the next wave.
