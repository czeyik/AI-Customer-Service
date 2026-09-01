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
- Hosted primary/fallback LLM integration and release evaluation do not exist.
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
| 1 | PG-01 | Approved launch contract and architecture | NOT_STARTED |
| 2 | PG-02 | Reproducible build, migrations, and CI | NOT_STARTED |
| 3 | PG-03 | Stateful multilingual ticket flow | NOT_STARTED |
| 4 | PG-04 | Reliable WhatsApp send/receive path | NOT_STARTED |
| 5 | PG-05 | Grounded hosted LLM and failover | NOT_STARTED |
| 6 | PG-06 | Named admins and ticket operations | NOT_STARTED |
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
| OI-01 | Pilot date, region, cohort, traffic/volume limits, duration, budget, success measures, rollback triggers, and go/no-go owner | UNRESOLVED | 1, 11, 12 |
| OI-02 | Hosting/staging platform, cloud region, domain/DNS, data residency, Git/CI/registry workflow, and resource/release owners | UNRESOLVED | 1, 2, 11 |
| OI-03 | Human support workflow, assignees, ticket statuses/notifications, escalation contacts, admin roster, CCO identity, and recovery approver | UNRESOLVED | 1, 3, 6, 7 |
| OI-04 | Meta Business/WABA/app/phone readiness, API version, opt-in approval, test recipients, and secure credential provisioning | UNRESOLVED | 4, 12 |
| OI-05 | GLM/OpenAI accounts, exact enabled model IDs, data terms, regions, quotas, timeouts, availability needs, spend limits, and fallback approval | UNRESOLVED | 5, 12 |
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

### Wave 5 — Hosted LLM and Failover

Inputs: OI-05 and provider-related OI-08 decisions.

- Verify exact current models and implement a provider-neutral primary/fallback adapter with
  timeouts, limits, telemetry, data minimization, grounding checks, and deterministic fallback.
- Add contract tests and authorized live smoke tests for both providers and failure paths.

Exit: both configured models and total-provider-outage fallback pass with no unnecessary personal
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

- Finalize the data inventory and implement idempotent 90-day chat and 24-month ticket/media
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

No handoffs recorded.

## Fresh-Chat Prompt

> Read `DELEGATION.md`, `AGENTS.md`, `docs/requirements-summary.md`, and
> `docs/security-launch-checklist.md`. Execute only Wave N using the mandatory workflow. Reinspect
> the repository, verify prerequisites, and ask only for unresolved inputs needed by this wave.
> Then create the prescribed goal and pursue it until every exit criterion is evidenced or the
> goal function's genuine blocked condition applies. Update `DELEGATION.md` before completing the
> goal. Do not begin the next wave.
