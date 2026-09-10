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
| 7 | PG-07 | CCO knowledge governance and launch corpus | PASS |
| 8 | PG-08 | Secure image/video pipeline | PASS |
| 9 | PG-09 | Application security | PASS |
| 10 | PG-10 | Privacy, retention, and deletion | PASS |
| 11 | PG-11 | Production platform and operations | PASS |
| 12 | PG-12 | Release validation and pilot activation | IN_PROGRESS |

Valid statuses: `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `PASS`.

## Information Required From the Owner

Agents should ask only for unresolved inputs needed by their wave. Never paste secrets into chat
or commit them; provision secrets directly in the chosen secret manager.

| Input | Required decision or information | Status | Waves |
| --- | --- | --- | --- |
| OI-01 | Pilot date, region, cohort, traffic/volume limits, duration, budget, success measures, rollback triggers, and go/no-go owner | RESOLVED | 1, 11, 12 |
| OI-02 | Hosting/staging platform, cloud region, domain/DNS, data residency, Git/CI/registry workflow, and resource/release owners | RESOLVED | 1, 2, 11 |
| OI-03 | Human support workflow, assignees, ticket statuses/notifications, escalation contacts, admin roster, CCO identity, and recovery approver | RESOLVED | 1, 3, 6, 7 |
| OI-04 | Meta Business/WABA/app/phone readiness, API version, opt-in approval, test recipients, and secure credential provisioning | RESOLVED | 4, 12 |
| OI-05 | Z.AI account, exact enabled model ID, data terms, region, quotas, timeout, availability needs, spend limit, and outage fallback approval | RESOLVED | 5, 12 |
| OI-06 | CCO-approved knowledge and customer copy in all three languages, including bot disclosure, emergency, consent, partnership, and WhatsApp profile text | RESOLVED | 3, 7, 12 |
| OI-07 | Allowed media types/sizes, private object store, malware scanner, reviewer access policy, signed-link lifetime, and media-analysis policy | RESOLVED | 8, 10, 11 |
| OI-08 | Privacy notice, controller/contact, deletion versus anonymization, legal holds, backup retention, incident owner, security owner, secret manager, scan policy, and risk approver | RESOLVED | 3, 9, 10, 11, 12 |
| OI-09 | Logging/metrics/error tools, alerts, on-call roster, SLOs, maintenance window, RPO/RTO, and incident/operational escalation path | RESOLVED | 11, 12 |

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

### Wave 7 — 2026-09-09
Status: PASS
Owner decisions: Cze Yik asked for the eight existing seed topics as a trilingual corpus and
confirmed that Jane approved all English, Bahasa Malaysia, and Simplified Chinese content, the
recorded sources, and the 9 September 2026 effective date with no exceptions. Cze Yik approved the
website workflow in which DUDU sitemap pages are extracted as inactive drafts and each page or
translation remains excluded until Jane activates it through her named CCO account. OI-06 is
`RESOLVED` for Wave 7.
Files/migrations and commit/PR/release: Added the approved 24-record corpus in
`docs/wave-7-knowledge-corpus.md`, versioned knowledge metadata and migration `8b61c2f2a8d7`, CCO-only
draft/publish/remove/rollback services and authenticated API actions, source/version traceability,
bounded indexed PostgreSQL trigram candidate retrieval, an idempotent corpus publisher, and a
bounded `duducar.co` sitemap/page draft importer. The API and both import paths use the same
ingestion function. Removed the shared knowledge API key, English-only JSONL seed, hash embeddings,
pgvector extension setup, and pgvector development image. The completed Wave 7 change set was
committed locally on `dev` at the owner's request; no push, PR, merge, release, production
deployment, administrator provisioning, live traffic, or customer contact occurred.
Verification commands/results: The clean digest-pinned Python 3.11 image built as
`dudu-support:wave7-check`; `python -m pip check` reported no broken requirements, all 97 tests
passed in 27.24s, and compile checks passed. Focused tests prove CCO-only mutations with CSRF,
attributable source/version audits, update, removal, rollback, exclusion of draft/superseded/removed
content, uncertainty for absent content, traceability, and representative retrieval plus complete
eight-topic coverage in all three languages. A fresh PostgreSQL 16 database migrated from zero to
`8b61c2f2a8d7 (head)`; `alembic check` reported no drift, `pg_trgm` and both the trigram and
single-active-version indexes were present, and the vector extension was absent. A legacy shared-key
knowledge row migrated as an inactive draft and its embedding column was removed. On another fresh
database, the publisher created exactly 24 active records; one live booking-policy page became one
draft record and retrieval of draft-only text returned zero results. `docker compose config
--quiet` and `git diff --check` passed.
External evidence (no secrets or customer data): The official DUDU Car sitemap and booking-policy
page were reviewed on 9 September 2026. The sitemap returned 28 unique same-host HTTPS URLs. The
bounded extractor successfully identified the booking-policy title, English language, and three
content chunks while excluding page chrome. Source URLs: `https://duducar.co/sitemap.xml` and
`https://duducar.co/booking-policy`.
Gate update and residual risks: PG-07 is `PASS`. Jane can publish, update, remove, and roll back
without second-person approval; every action is named, versioned, sourced, effective-dated, and
audited. Only the 24 approved current records are searchable. The other website pages and any
machine translations remain inactive until Jane reviews them, which does not weaken the approved
launch corpus. Real customer traffic remains disabled.
Next-wave notes: Wave 8 has not started and remains blocked on OI-07 plus the applicable OI-08
privacy/security decisions for media types, limits, storage, malware scanning, reviewer access,
signed-link lifetime, analysis, deletion, and risk approval.

### Wave 8 — 2026-09-09
Status: PASS
Owner decisions: Cze Yik approved JPEG/PNG images up to 5 MB; MP4/3GP videos up to 16 MB; a
private Lightsail object-storage bucket in AWS Malaysia (`ap-southeast-5`) attached to the
same-region instance; ClamAV scanning; review by active named administrators through audited
five-minute signed links; no automated media analysis or LLM media access; immediate disposal of
rejected/failed bytes; deletion with the ticket under the approved 36-month post-closure rule;
and Cze Yik as privacy, security, incident, and risk owner. OI-07 is `RESOLVED`. The owner does not
need a separate AWS account or project: Wave 11 will create the bucket as a resource in the
existing account. OI-08 remains globally open for later legal-hold, backup, and launch-security
decisions.
Files/migrations and commit/PR/release: Added signed WhatsApp image/video intake, a durable media
queue, authenticated Graph API `v26.0` metadata/download requests, bounded streaming, SHA-256
verification, structural content detection, quarantine, ClamAV `INSTREAM` scanning, private
S3-compatible storage, generated object keys, ticket/conversation linkage, bounded retry/failure
states, deletion, active-admin review with audited five-minute links, and trilingual
acknowledgements/refusals. Added migration `69f51c2de537`, the media worker, production
configuration validation, focused and live-boundary tests, and `docs/wave-8-secure-media.md`.
Boto3 1.43.90 and its transitive dependencies are hash locked. Changes remain uncommitted on
`dev`; no commit, push, PR, merge, deployment, AWS resource, billable action, customer traffic, or
pilot-user contact occurred.
Verification commands/results: The clean digest-pinned Python 3.11 image built as
`dudu-support:wave8-check`; `python -m pip check` reported no broken requirements and all 114
normal tests passed with the separately invoked integration module skipped in 28.25 seconds.
Focused tests cover authenticated Meta requests, webhook/media-ID duplication, JPEG/PNG and
MP4/3GP policy, streaming limits, SHA-256 and MIME mismatch, corruption, sensitive indicators,
malware, scanner/storage failure and bounded retry, ticket linkage, reviewer authorization,
five-minute links, and deletion invalidation. Against temporary real MinIO and ClamAV 1.4.6
services, the integration check passed: anonymous retrieval returned 403, signed retrieval
returned the exact clean PNG, integrity metadata round-tripped, ClamAV accepted the clean image,
and rejected the EICAR test file. A fresh PostgreSQL 16 database migrated from zero to
`69f51c2de537 (head)` and `alembic check` reported `No new upgrade operations detected.` The
temporary object, services, database, containers, and networks were removed.
External evidence (no secrets or customer data): Meta's official WhatsApp Business Platform API
collection confirmed the authenticated two-request media retrieval flow, five-minute provider
URL, supported JPEG/PNG and MP4/3GP formats, and 5 MB/16 MB limits. AWS documentation confirmed
Lightsail buckets are private by default, account-level Block Public Access applies, Lightsail
uses AWS-managed server-side encryption and HTTPS, and a same-region instance attachment avoids
stored bucket credentials. ClamAV documentation confirmed the framed `INSTREAM` protocol. Boto3
1.43.90 was the current AWS SDK release on PyPI. Exact source URLs and review date are recorded in
`docs/wave-8-secure-media.md`.
Gate update and residual risks: PG-08 is `PASS`. Only successfully sniffed, integrity-verified,
ClamAV-clean media receives an object key or reviewer link; rejected and exhausted media never
becomes accessible. Production fail-closed configuration requires media processing, the Malaysia
region, bucket, registered Meta phone, access token, ClamAV, and the exact five-minute lifetime.
The actual Lightsail bucket/scanner deployment remains Wave 11, and representative real WhatsApp
image/video validation remains part of Wave 12 release validation. Real customer traffic remains
disabled.
Next-wave notes: Wave 9 is unblocked for code work but requires the unresolved OI-08 application
security, secret-manager, scan-gate, and risk-acceptance decisions before it starts. Do not
provision the Wave 8 AWS bucket until Wave 11.

### Wave 9 — 2026-09-09
Status: PASS
Owner decisions: Cze Yik approved AWS Secrets Manager for production secrets and PostgreSQL for
the shared bounded abuse-control store, with no Redis service. Cze Yik approved Gitleaks,
pip-audit, Bandit, Trivy source/container, and OWASP ZAP scans. Verified secrets, dependency or
container high/critical findings, Bandit high-severity/high-confidence findings, and ZAP high-risk
findings block release; medium findings require review. A high/critical exception requires Cze
Yik's written, time-limited acceptance naming an owner and remediation date. Cze Yik is the
security and risk-acceptance owner. These decisions resolve the Wave 9 portion of OI-08; OI-08
remains globally unresolved for Wave 10 legal-hold, deletion/anonymization, and backup-retention
decisions.
Files/migrations and commit/PR/release: Added a bounded shared PostgreSQL rate limiter with hashed
identities and migration `49b1f7a0c2de`; IP-first and caller/admin limits; bounded webhook bodies;
redirect-safe website ingestion; expanded credential and identity redaction; production HTTPS,
HSTS, CSP, browser, cache, cookie, CSRF, CORS, and API-documentation controls; and production AWS
Secrets Manager validation. Added a digest-pinned non-root Alpine application image, hash-locked
patched dependencies, a pinned security workflow, focused application-security tests,
`docs/wave-9-application-security.md`, and repeatable checklist evidence. Removed the obsolete
pgvector CI service. Changes remain uncommitted on `dev`; no commit, push, PR, merge, release,
deployment, secret provisioning, risk waiver, billable action, customer traffic, or pilot-user
contact occurred.
Verification commands/results: The clean `dudu-support:wave9-final` image built successfully;
`python -m pip check` reported no broken requirements and all 126 tests passed with one integration
test skipped in 41.76 seconds. A fresh PostgreSQL 16 database migrated from zero to
`49b1f7a0c2de (head)`, `alembic check` reported `No new upgrade operations detected`, and a
cross-session limiter check proved shared counters with hashed stored identities. Gitleaks scanned
all 19 commits with no leak; pip-audit reported no known vulnerability; Bandit reported zero
high-severity/high-confidence findings; Trivy 0.74.0 reported zero source/image high or critical
vulnerability, secret, or misconfiguration findings; and ZAP 2.17.0 reported zero high-risk alert.
Five Bandit medium findings and five lower-risk ZAP development-surface warnings were reviewed and
documented without a waiver. Workflow YAML, Compose configuration, compilation, and
`git diff --check` passed.
External evidence (no secrets or customer data): Official Gitleaks 8.28.0, pip-audit 2.10.1,
Bandit 1.9.4, Trivy Action 0.36.0/Trivy 0.74.0, and ZAP baseline documentation were reviewed on 9
September 2026. Exact source URLs and the approved gate semantics are recorded in
`docs/wave-9-application-security.md`.
Gate update and residual risks: PG-09 is `PASS`; every application-security checklist control now
maps to repeatable evidence and no unaccepted launch-blocking application finding remains.
Platform TLS termination, AWS policies/runtime injection, host/network controls, authenticated
dark-production dynamic testing, and monitoring remain Wave 11/12 work. Retention, legal holds,
and backup lifecycle remain Wave 10/11 work. Real customer traffic remains disabled.
Next-wave notes: Wave 10 has not started and is blocked on the remaining OI-08 owner decisions for
deletion versus anonymization, narrow legal holds, backup retention and deletion propagation.
Obtain and record those decisions before starting Wave 10.

### Wave 10 — 2026-09-09
Status: PASS
Owner decisions: Cze Yik confirmed permanent deletion rather than anonymization: each chat copy
expires after 90 days, and a closed ticket plus its attachments expires after 36 calendar months.
Only Cze Yik, as the named privacy owner, may create or release a legal hold; each hold is limited
to one conversation or ticket and requires a reason, case/reference, future expiry, and audit.
Cze Yik approved the recommended maximum 35-day backup retention and required retention to run
successfully before a restored environment can serve traffic. Together with the previously
recorded controller/contact, incident/security/risk owners, AWS Secrets Manager, and scan policy,
these decisions resolve OI-08.
Files/migrations and commit/PR/release: Added migration `c81d4e2a7f10`, indexed audit subjects,
narrow legal holds, fail-closed production lifecycle settings, and a bounded daily retention
worker. The worker permanently removes 90-day messages, WhatsApp copies, stale conversation
state, unlinked media metadata/objects and applicable webhook IP audits; at 36 calendar months
after closure it removes the ticket, notes, notifications, audit/index entries, attachment
metadata, and objects. Object deletion precedes database deletion; failures preserve metadata,
write an audit alert, emit an error, exit non-zero, and safely retry. Added authenticated
privacy-owner hold management, dry runs, restore-gate commands, the complete owned-data inventory,
and `docs/wave-10-privacy-data-lifecycle.md`. Changes remain uncommitted on `dev`; no commit, push,
PR, merge, release, deployment, backup change, live deletion, customer traffic, or pilot-user
contact occurred.
Verification commands/results: The clean `dudu-support:wave10-final` image built successfully;
`python -m pip check` reported no broken requirements, compilation passed, and all 135 tests passed
with two opt-in integration tests skipped in 76.10 seconds. Lifecycle tests use an injected clock
and object store to prove the exact 90-day and 36-calendar-month edges, dry-run non-deletion,
retained-ticket detachment, permanent object/database/index coverage, owner-only audited holds,
object failure alerting, retry, and a repeated no-op pass. A fresh PostgreSQL 16 database migrated
from zero to `c81d4e2a7f10 (head)`; `alembic check` reported `No new upgrade operations detected`,
and the opt-in PostgreSQL lifecycle test passed against that database. Gitleaks scanned all 20
commits with no leak; Bandit reported no high-severity/high-confidence issue; and Trivy 0.74.0
reported no high/critical source or image vulnerability, secret, or misconfiguration finding.
`git diff --check` passed. The disposable database, container, and network were removed.
Gate update and residual risks: PG-10 is `PASS`. Every application-owned customer-data store has
an owner and lifecycle, and the backup exception has a fixed maximum lifetime plus a mandatory
post-restore deletion gate. Wave 11 must configure and prove the 35-day database/object backup
expiry, scheduler, failure-alert routing, and restore traffic gate in the intended AWS account;
it must not alter the approved periods silently. Real customer traffic remains disabled.
Next-wave notes: Wave 11 has not started. Its remaining owner inputs are OI-02 and OI-09; obtain
the hosting/CI ownership and observability/on-call/SLO/RPO/RTO decisions before starting it.

### Wave 11 — 2026-09-10
Status: PASS
Owner decisions: Cze Yik approved `support.duducaradmin.com`, one AWS account with separately
isolated staging and production resources, AWS-native observability, and email-only alerts to
`support@duducar.co`. Cze Yik owns infrastructure, releases, incidents, and critical on-call;
Jane receives support-impact alerts. The SLO is 99% availability and 95% of valid messages
answered within 30 seconds; RPO is one hour, RTO is four hours, and the maintenance window is
2:00–4:00 AM Malaysia time. The approved host is EC2 `t4g.small` with a USD 20 monthly ceiling;
promotion to `t4g.medium` requires explicit approval after a failed capacity test.
Files/migrations and commit/PR/release: Added the AWS CloudFormation foundation, budget, and
environment stacks; production Compose/runtime, provisioning, deployment, rollback, monitoring,
backup, and load-test tooling; the digest-gated release workflow; object-storage, readiness, and
combined-worker support; focused operations tests; and `docs/wave-11-production-platform.md`.
Production was deployed dark. Changes remain uncommitted on `dev`; no commit, push, PR, merge,
real customer traffic, or Wave 12 release occurred.
Verification commands/results: The final ARM64 image passed 146 tests with two opt-in integration
tests skipped. Bash, Compose, Actionlint, CloudFormation linting, drift detection, and
`git diff --check` passed. The scan-clean final application and ClamAV digest pair passed isolated
staging TLS/readiness, 1,000/1,000 requests at 0.796-second p95, dependency failure/recovery,
encrypted PostgreSQL restore plus retention in 13 seconds, and pair-aware deploy, rollback, and
roll-forward. The encrypted AWS Backup recovery point has 35-day expiry. Production readiness is
HTTP 200 with valid TLS; all five alarms are `OK`; the only active project instance is the
production `t4g.small`. Temporary staging and every retained billable artifact were deleted.
External evidence (no secrets or customer data): Current AWS pricing, service characteristics,
OIDC, IAM-role, and backup documentation are linked in `docs/wave-11-production-platform.md`.
The verified steady-state price basis is approximately USD 19.54/month; the USD 20 budget and
USD 10/15/18 notifications are active. Cze Yik confirmed the production SNS subscription, and a
CloudWatch `OK` → `ALARM` routing test recorded a successful SNS action to the confirmed endpoint
before the alarm was reset to `OK`.
Gate update and residual risks: PG-11 is `PASS`. Dark production, alerts, restore, retention after
restore, RPO/RTO, capacity, dependency recovery, rollback, least privilege, encryption, TLS/DNS,
and the cost ceiling passed in the intended AWS account. Usage-driven charges require continued
budget monitoring. Meta and notification sends remain disabled and no real customer traffic ran.
Next-wave notes: Wave 12 has not started. Keep production dark until its release validation,
go/no-go decision, and pilot activation gate pass.

### Wave 12 — 2026-09-10
Status: IN_PROGRESS
Owner decisions at the initial blocked checkpoint: The previously approved pilot contract, owners,
Meta/Z.AI choices, content, privacy controls, platform and operational thresholds remained
authoritative. Authorization had not yet been received to push/freeze the release candidate,
create temporary staging, make the 15 billable hosted-model evaluation calls, contact the two
owner-controlled testers, enable real WhatsApp text/media or activate the pilot. Production also
needed Cze Yik and Jane provisioned
through the trusted operator path; credentials, TOTP values and personal contact details must not
be supplied in chat. Cze Yik must either accept the documented Trivy findings through 30 September
2026 with himself as owner and remediation required before broader launch, or approve a revised
architecture and budget.
Files/migrations and commit/PR/release: Marked Wave 12 in progress and added
`docs/wave-12-release-validation.md`, the 36-case `scripts/release_eval.py`, and focused release
tests. Fixed false grounding of unrelated policy questions in the shared retrieval tokenizer and
included the approved corpus in the release image so seed/evaluation commands work there. Narrowed
the proposed production egress from all protocols to required TCP 443/465, enabled free AWS-managed
KMS encryption for S3/SNS, and added platform assertions. Updated the README and security evidence
map. No migration, commit, push, PR, release-candidate freeze, staging deployment, production
deployment, billable model generation, outbound message, tester contact, pilot activation or live
traffic occurred.
Verification commands/results: The clean Python 3.11 container suite passed 149 tests with two
opt-in integrations skipped. The deterministic provider-outage evaluation passed all 36 English,
Bahasa Malaysia and Simplified Chinese cases; all 15 grounded-answer calls traversed the adapter,
failed deliberately, and safely used approved-corpus fallback at 0.004-second local p95. Focused
release/retrieval/platform tests passed 15/15; compilation, dependency integrity, Compose parsing,
CloudFormation validation, Actionlint and `git diff --check` passed. Gitleaks found no leak across
22 commits; pip-audit found no known dependency vulnerability; Bandit found no high-severity,
high-confidence issue; the application image had no high/critical Trivy finding; and ZAP had zero
high-risk alert. The Trivy source gate remains red on two AWS-0104 critical findings for required
public TCP 443/465 egress and one AWS-0136 high finding for AWS-managed rather than customer-managed
SNS KMS encryption. Production preflight returned readiness 200 with valid TLS, five healthy
containers, all five project alarms `OK`, no queued outbound/media work, and usable current and
rollback digest pairs; both send switches were false. The fresh production database contained zero
active administrators and zero active knowledge documents, so admin review and grounded production
responses cannot pass yet.
External evidence (no secrets or customer data): Meta's current official opt-in, service-window and
media documentation was reviewed on 10 September 2026 and still matches the approved named-business
opt-in, 24-hour response window, JPEG/PNG 5 MB and MP4/3GPP 16 MB controls; exact URLs are in
`docs/wave-12-release-validation.md`. An authenticated non-generation Z.AI models query confirmed
that the configured account exposes exact model `glm-5.3-flash`; no paid inference ran. Official
AWS guidance confirms that dynamic-hostname egress filtering requires a Network Firewall-style
DNS/SNI architecture, AWS-managed SNS KMS encryption is supported, and a customer-managed KMS key
costs USD 1/month before requests. This would exceed the verified USD 19.54 basis under the USD 20
ceiling; sources are recorded in the Wave 12 document.
Gate update and residual risks: PG-12 is `BLOCKED`, not `PASS`. PG-01 through PG-11 remain `PASS`.
The release cannot satisfy the approved no-unaccepted-high/critical-finding rule, production admin
and corpus checks, hosted-model evaluation, real WhatsApp text/media/admin test, documented
go/no-go, cohort activation or observation window without the owner actions above. Production
remains dark with real customer traffic disabled.
Next-wave notes: There is no Wave 13. Resume Wave 12 only after the owner records the scan decision,
authorizes the enumerated external validation actions, confirms current non-secret Z.AI input and
output prices, and provisions both named production administrators securely. Then publish the 24
approved corpus records through Jane, freeze and push one reviewed SHA, run CI/security and exact
digest staging, complete hosted/WhatsApp/media/admin/failure checks, hold the go/no-go, and activate
only the approved cohort if every gate passes.

Resume update — 2026-09-10: Cze Yik accepted, through 30 September 2026, the two narrowly scoped
pilot exceptions for required public TCP 443/465 egress (AWS-0104) and AWS-managed SNS encryption
(AWS-0136), with remediation required before a broader launch. The exceptions expire on 1 October
2026. He also authorized freezing and pushing the release candidate, temporary staging, 15
billable Z.AI evaluation calls, contact with the two owner-controlled testers, and real WhatsApp
text/media validation. This is validation authorization only; final production cohort activation
still requires the completed evidence and documented go/no-go decision. Wave 12 is resumed.

Resume progress — 2026-09-10: RC `77ce6c9a23f125b3e3ef5f52cf0672d6dbb9646b` was pushed in PR 2;
GitHub CI and security passed. Its zero-finding ARM64 application and ClamAV digest pair deployed
to isolated temporary staging at migration head `c81d4e2a7f10`. Staging passed public readiness,
1,000/1,000 four-way capacity requests at 0.060-second p95, database 503/recovery, ClamAV
fail-closed/recovery and EICAR rejection, encrypted S3 round-trip, and outbound-disabled checks.
The authorized Z.AI run passed all 36 scenarios at 4.245-second p95 for estimated USD 0.001863,
but only 14/15 provider calls succeeded; the failed call safely fell back. The strict live-model
gate is therefore failed and no rerun is authorized. Production infrastructure was updated without
replacement to required TCP 443/465 egress, `alias/aws/s3`, and `alias/aws/sns`; readiness remained
200. Named production admins, corpus publication, real WhatsApp/media/admin review, PR review,
final go/no-go, production RC deployment, cohort activation, observation, and staging cleanup are
still pending. Production outbound remains disabled.

Second resume update — 2026-09-10: Both named production administrator records now exist with one
CCO and one recovery approver. The current secret matches Cze Yik's TOTP reference but does not yet
contain Jane's reference; her mapping must be merged before admin review. Jane nevertheless
attributed publication of all 24 approved corpus records through the trusted operator command.
Cze Yik authorized exactly one further 15-call hosted-model run. It passed 15/15 provider calls and
36/36 scenarios at 3.679-second p95, using 6,989 input and 1,964 output tokens for estimated USD
0.002030. No more hosted-model call is authorized or needed. The remaining Wave 12 work is Jane's
TOTP mapping, reviewed/merged PR and dark production RC deployment, real two-tester WhatsApp
text/media/admin lifecycle validation, final owner go/no-go, limited cohort activation and
observation. Production outbound remains disabled.

Current blocker — 2026-09-10: Production is healthy and dark with two active named admins and 24
active approved records, and the hosted-model gate now passes. The runtime secret contains only
Cze Yik's matching TOTP entry; Jane's generated reference is absent. Jane must merge her mapping
into the same compact JSON object, refresh the runtime file, and prove login before the authorized
two-tester WhatsApp/media/admin review can proceed. PR 2 also still awaits owner review/merge and
no production RC deployment or final pilot go/no-go has been authorized.

Third resume update — 2026-09-10: Jane's mapping was merged and both active administrator TOTP
references now match the two-entry runtime secret. Cze Yik approved PR 2; it merged to `dev` as
`0a6b3b3177a797c13e9bcc69582843388d55fc31`. The exact staging-tested release pair was deployed
dark to production by SSM command `d30e721d-e496-41a7-bb7b-2228695b7102`. API and ClamAV are
healthy on the new digests, PostgreSQL remained healthy, public readiness is 200, all five alarms
are `OK`, the current backup is completed, the AWS budget reports USD 0 actual against USD 20,
and the prior pair is recorded for rollback. Both send switches remain false. DUDU Car Malaysia's
configured WhatsApp number is quality GREEN and the public callback passes verification. Real
tester text/media and admin lifecycle review now await Cze Yik and Jane sending the prescribed
messages; final pilot go/no-go remains unapproved.

## Fresh-Chat Prompt

> Read `DELEGATION.md`, `AGENTS.md`, `docs/requirements-summary.md`, and
> `docs/security-launch-checklist.md`. Execute only Wave N using the mandatory workflow. Reinspect
> the repository, verify prerequisites, and ask only for unresolved inputs needed by this wave.
> Then create the prescribed goal and pursue it until every exit criterion is evidenced or the
> goal function's genuine blocked condition applies. Update `DELEGATION.md` before completing the
> goal. Do not begin the next wave.
