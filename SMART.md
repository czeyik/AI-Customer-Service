# Smart Chatbot Execution Handoff

Date: 10 September 2026

Status: Implemented locally; verification and release gates pending. Not committed, pushed, deployed, or activated.

## Current agent handoff — 10 September 2026

**Read this section first.** It supersedes the original “not implemented” status. The requirements
below remain the acceptance contract. The user interrupted implementation to request this handoff;
continue from the working tree, not from scratch. Do not discard or recreate the changes.

### Workspace and authorization

- Repository: `/home/czeyik/Documents/AI-Customer-Service`; branch `dev`.
- Current base HEAD: `9d6c1ea6d0e291d1b953694ce487f6fda2aed3ff`.
- Remote: `https://github.com/czeyik/AI-Customer-Service.git`.
- All implementation changes are **uncommitted**. `SMART.md`, the evaluation data/results, migration,
  inbox service and SMART regression tests are untracked; include them deliberately in the release.
- Local implementation and synthetic hosted-model testing were authorized. No production secrets,
  knowledge, configuration, deployment, or customer messages were changed by this work.
- Do not impersonate Jane/CCO approval, publish unreviewed policies, or infer a production GO from
  passing local tests. Cze Yik owns GO/no-go; Jane is the support lead and CCO.
- Follow root `AGENTS.md`: minimal shared fixes, no new dependencies or unrelated infrastructure.
  No subagents were used; do not introduce delegation unless authorized.

### Completed implementation

1. **One structured GLM call per eligible turn** in `app/services/answer_generation.py` and
   `chatbot.py`: minimized current question, bounded sanitized prior exchange/topic/source context,
   actual local prompt, intake stage and field-role indicators. The old answer-rewriter path was
   removed. Answer, clarification, troubleshooting, offers and intake actions share one schema.
2. **Local authority over mutations:** consent/submission, ownership, priority and notifications
   remain local. Explicit local handoff requests and Yes/No/Done/Skip/Submit override model action
   proposals. Pause/cancel/resume need matching local intent. Unknown model-only handoffs are
   optional offers and leave intake idle. Standalone identity identifies an automated AI assistant;
   mixed identity/business questions can still use GLM.
3. **Flexible intake:** fields in any order, combined fields, corrections, explicit contact numbers
   preserved, actual issue collection after a bare human request, side questions, pause/resume,
   expiry, bounded accumulated details, missing-field recovery, local correction review, and
   explicit submission. “No” to more details finishes details; a trip ID does not submit. Control
   words cannot become names. Existing-case updates/reopening require ownership and confirmation.
4. **Provider boundary:** separate local extraction/storage and provider minimization in `pii.py`.
   Known identifiers/contact fields become opaque roles; uncertain identifying narratives and
   oversized questions stay local. No raw history, ticket bodies, attachments or contact values are
   deliberately forwarded. Regex is not represented as a universal anonymizer. Regression checks
   capture provider inputs. Whole excerpts are dropped to fit 8,000 characters; output stays at
   300 tokens with an eight-second timeout. Citation/numeric/unsupported-claim rejection checks
   remain guards, not a semantic proof. Usage, reasoning details and outcomes are logged.
5. **Retrieval and governed website knowledge:** rank the complete effective corpus up to the
   explicit 500-chunk ceiling, include titles/multiword tags and cross-language sources, preserve
   source language, clarify unresolved conflicts. Website staging uses an exact CCO-selected HTTPS
   allowlist (empty by default), bounded extraction and redirect restrictions, preserves nested
   lists/tables/conditions/links, and permits an audited effective date on activation. Changed pages
   stay drafts. No website content has been approved or published in this task.
6. **Durable WhatsApp processing:** webhooks persist inbound events before acknowledgment. New
   `app/services/inbound.py` uses ordered per-sender claims and four workers; provider calls happen
   outside DB transactions. A persisted attempt marker prevents a second model request after a
   crash. State versions, prompt IDs, quoted-message IDs and timestamps reject stale controls.
   Ticket/audit/outbox commit together. Outbound retries preserve sender order, including timestamp
   ties. API clients must return `prompt_id` for consequential boolean controls.
7. **Evidence ownership:** explicit evidence groups bind pre/during-intake uploads to the intended
   case. Scan timing changes availability, not ownership. Pending and approved media bind at
   submission; rejected/other-case media do not. Staff can see confirmed customer case updates.
8. **Migration:** `d9010a1b2c3d_smart_conversation_integrity.py`, after `c81d4e2a7f10`, adds unique
   conversation ownership, inbound claims/status and evidence groups. It refuses pre-existing
   duplicate `(channel, external_user_id)` owners before DDL. Existing inbound rows remain `done`.
9. **Artifacts/docs:** `data/evaluation/smart-non-escalation.tsv` has 100 distinct scenarios in EN/MS/ZH;
   `scripts/smart_eval.py`, `scripts/smart_burst.py`, new SMART tests and updated existing tests are
   present. `release_eval.py` defaults to SMART; `--suite legacy` preserves the old suite and
   `--intake-only` runs handoff/intake diagnostics. Docker includes evaluation data. Architecture,
   privacy-copy draft, governance and rollback notes are in `docs/smart-implementation.md` and the
   modified privacy/security/requirements documents.

### Verification evidence — exact status at handoff

- Latest completed **current-source Docker test build**: **208 passed, 4 skipped**.
  Image: `dudu-support:smart-check`; log: `/tmp/smart-build-verified.log`.
- Previous PostgreSQL-inclusive full run: **204 passed, 2 skipped**, before the last action-precedence
  parameterization and equal-timestamp outbox change. Do not label this the final-source PG run.
  The final-source PG run remains to be executed; expected count is 210 passed / 2 skipped, but
  record the actual result, not that expectation.
- Latest focused intake tests: **87 passed** (`/tmp/smart-action-check.log`). They include all six
  competing model actions against local controls. Latest transport tests: **18 passed**
  (`/tmp/smart-order-check.log`), including equal-timestamp retry ordering.
- Latest completed **corrected live intake diagnostic**, `docs/evaluation/smart-live-intake-final.json`:
  **18/18 necessary handoffs and 30/30 correct intake completions**, 6/6 and 10/10 respectively in
  each language. Actual name/email/phone values were checked, not just ticket existence.
  236 calls, 235 provider successes, 278,444 input tokens, 31,199 completion tokens; USD **0.057366**
  at verified list prices. One failed provider call safely fell back. This diagnostic has **zero
  non-escalation sessions** and is not a substitute for the full matrix.
- Latest outage artifact: `docs/evaluation/smart-outage.json`: 300/300 non-escalation, 18/18 handoffs,
  30/30 correct intakes, zero unintended mutations. It predates the final precedence/outbox patch;
  refresh it after the final-source checks.
- Synthetic PG burst: `docs/evaluation/smart-burst.json`, eight senders/four workers/two-second
  simulated provider: **5.991697-second reply-queued p95, zero duplicate replies**. This excludes
  external Meta delivery; it is not staging end-to-end evidence.
- Local runtime image: `dudu-support:smart-local`, **amd64**, content ID
  `sha256:cc1ba542afed2088747b2ba9b46ca4f689d492a1f886497b6ce83bb6d9e9e619`.
  This is a local image ID, **not** the ARM64 ECR digest required for production. Runtime build log:
  `/tmp/smart-runtime-build.log`.
- Migration head was successfully applied to the disposable local PostgreSQL database. Nothing was
  migrated in staging/production during this task. `git diff --check` passed before this handoff.
- All semantic correctness/grounded-relevance review denominators are **0 / pending CCO review**.
  Do not claim 95% semantic correctness from routing checks or API successes. The 20 designated
  holdout scenarios were rerun during development; add a fresh independent holdout for release review.

**Final full live run completed while this handoff was being written.**
`docs/evaluation/smart-live-verified.json` is now complete; do not launch another paid run unless
source changes or a specific acceptance failure requires it. Results: **295/300 non-escalation**
(EN 99/100, MS 97/100, ZH 99/100), **18/18 necessary handoffs**, **30/30 correct intakes**,
**zero unintended mutations**, five non-mutating unexpected offers. Every language exceeds the
95% automated non-escalation threshold. Provider: 531 calls / 527 successes, 602,547 input tokens,
67,608 completion tokens; USD **0.124186** measured at the verified list prices; sample projection
**USD 2.339 per 10,000 calls**; non-escalation response p95 **3.413 seconds**. These are synthetic
service timings and sample cost projections, not real Meta latency or a hard budget maximum.
Family failures: {"corporate": 1, "fraud": 3, "safety": 1}. Designated holdout results: 60/60
translations (development reruns, not a fresh independent release holdout). Semantic/grounding
review remains unscored. The run includes the final conversation fixes; the later outbox tie-break
change does not participate in these direct-service calls. Log: `/tmp/smart-live-verified.log`.
No further model evaluation needs to remain running for this handoff.

Earlier artifacts `smart-live.json`, `smart-live-iteration-2.json`, `smart-live-iteration-3.json`, and
`smart-live-intake.json` are **failed development iterations**, not release passes. They exposed
false model-only intake, issue-collection misses, then model pause/cancel/correction/case proposals
overriding local prompts. The fixes above address those causes. Keep their provenance; never combine
repeated runs/translations as additional distinct scenarios. One intermediate full run was stopped
before producing a report; do not claim its usage as measured. Complete-run costs and projections
are in their JSON files; aggregate development spend is not a complete billing reconciliation.

### Resume checklist — do these in order

#### 1. Close local verification and record the completed results

1. Read the completed `docs/evaluation/smart-live-verified.json` (results above). Record its
   per-language metrics, family/holdout results, usage, cost and p95 in `docs/smart-implementation.md`
   and the new release record. Do not repeat this paid run just to obtain an already available result.
2. Apply the acceptance gates in this file explicitly. **SMART CLI currently emits a report and
   `rollout_ready=false`; exit code 0 is not a release pass.** For the automated development slice,
   require >=95% non-escalation per language/overall, >=95% handoff, >=95% correct cooperative intake,
   all critical human/safety/consent/correction regressions, and zero consequential false mutations.
   If failures remain, inspect the saved intake traces, fix the shared cause, add one regression,
   rerun affected cases and then the full suite. Do not tune only an individual matrix phrase.
3. Run the final-source PostgreSQL checks below. The disposable DB already exists and is migrated;
   only clear its synthetic rate counters before rerunning (otherwise fixed-key rate tests collide).
   **Never execute this TRUNCATE against a real environment.**

```bash
cd /home/czeyik/Documents/AI-Customer-Service
docker exec smart-check-postgres psql -U postgres -d smart_checks -c 'TRUNCATE rate_limit_buckets;'
docker run --rm --network smart-check \
  -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  -e TEST_POSTGRES_URL=postgresql+psycopg://postgres:synthetic-check@smart-check-postgres/smart_checks \
  dudu-support:smart-check python -m pytest -q -p no:cacheprovider --tb=short
```

4. Refresh outage evidence without reading the real `.env`:

```bash
docker run --rm -v "$PWD:/work:ro" -v /dev/null:/work/.env:ro -w /work \
  dudu-support:smart-check python scripts/release_eval.py --suite smart --mode outage \
  --input-price 0.15 --output-price 0.50 > docs/evaluation/smart-outage.json
```

5. Only if a live rerun is necessary, use synthetic inputs and the existing local key privately:

```bash
docker run --rm --user 0 -v "$PWD:/work:ro" -w /work dudu-support:smart-check \
  python scripts/release_eval.py --suite smart --mode live \
  --input-price 0.15 --output-price 0.50 > docs/evaluation/smart-live-verified.json
```

   Do not overwrite a running report. `--intake-only` is available for focused diagnostics. The live
   script uses in-memory SQLite, does not contact Meta or send notifications, and guards observed
   model spend at USD 0.25 per language. Recheck official pricing if the date/model changes. The
   system `.venv` is broken; use Docker rather than rebuilding the local Python environment.
6. If source code changes, rebuild both `--target test` and `--target final`, rerun appropriate
   checks, and replace the recorded local image ID. Do not reuse stale image evidence.
7. Finish release documentation: add a new **SMART pending/validated release section** to
   `docs/release-validation.md`; its current PASS/159-test/old-SHA entry is historical and must not
   be presented as this release. Add the new defaults to `infra/production/runtime.env.example`
   (currently still missing them): `LLM_CUSTOMER_CONTEXT_ENABLED=false`, `INTAKE_EXPIRY_MINUTES=60`,
   `WEBSITE_KNOWLEDGE_URLS=[]`. `.env.example` and `app/config.py` already contain these controls.
8. After local DB verification is complete, remove only the disposable `smart-check-postgres`
   container and `smart-check` network. Preserve the existing BuildKit container and both images.

#### 2. Obtain the specific release inputs that code cannot supply

These are the remaining owner/CCO decisions, not reasons to redo implementation:

- Jane/CCO and Cze Yik: approve the revised customer-question/context data flow, privacy-copy
  wording and effective date, confirm provider DPA coverage, and publish the approved notice before
  enabling real customer-context transmission. Draft is `docs/privacy-notice-chatbot-addendum.md`.
- Jane/CCO: select the exact `https://duducar.co/...` support/policy URLs, review extracted snapshots
  and conditions/conflicts, and supply their effective dates. Do not invent URLs or an approver.
- Jane/CCO: review captured EN/MS/ZH answers and faithful translations, score answer correctness and
  relevant grounded answers separately, and review a fresh independent holdout. Populate genuine
  review values rather than treating `null` as a pass. `run_smart(..., reviews=...)` accepts a
  dictionary keyed by IDs such as `en-000` with `answer_correct` and `grounded_relevant` booleans;
  the CLI does not currently expose a reviews-file argument. Attach the approved review artifact
  and denominators to the release record. Require >=95% in each applicable language/overall.
- Cze Yik: record the new GO/no-go after all gates below pass. Existing beta/notification/security
  waivers expire 14 September 2026; confirm current validity if deployment occurs later. Do not
  silently extend beta dates, spend caps or waivers.

#### 3. Commit, push, CI, and dark staging deployment

1. Inspect `git diff --check` and `git status`, include all intended modified/new files including
   this handoff and evaluation artifacts, exclude `.env`, local secrets, logs and unrelated files.
   Commit the verified change on a release branch from the current working tree; push it. Record
   its exact 40-character SHA. Run `.github/workflows/ci.yml` and `security.yml` and resolve failures
   or obtain the existing explicitly governed exception process; do not invent scan passes.
2. Use the existing AWS/GitHub setup, **not new infrastructure**: region `ap-southeast-5`, foundation
   stack `dudu-support-foundation`, environment stacks `dudu-support-staging` and
   `dudu-support-production`. Check actual stack/environment availability read-only first; the
   current cloud state was not revalidated in this conversation. Use Systems Manager, not SSH.
3. Prepare the staging runtime secret `dudu-support/staging/runtime-env` privately. Keep
   `META_SEND_ENABLED=false` for deployment; keep customer context disabled until the approvals in
   step 2 permit its intended use. Preserve existing secrets/caps. Do not print populated secrets.
4. Dispatch `.github/workflows/release.yml` with `environment=staging` on the pushed release ref:
   `gh workflow run release.yml --ref <release-ref> -f environment=staging`.
   Watch the run to success. It builds ARM64 app and ClamAV images, resolves ECR immutable digests,
   deploys via SSM, runs `alembic upgrade head`, retention and `/ready` checks. Record both ECR
   digests and SHA. The amd64 local image above is not the production artifact.
5. Before migration on any real DB, check:
   `SELECT channel, external_user_id, COUNT(*) FROM conversations GROUP BY channel, external_user_id HAVING COUNT(*) > 1;`
   If rows exist, stop migration and reconcile ownership/history with the owner; do not delete
   conversations or drop the uniqueness requirement to get a green deploy. Confirm a recoverable
   backup. Expect new head `d9010a1b2c3d`. Existing processed inbox rows stay done; do not replay them.

#### 4. Staging acceptance on the exact immutable release

1. After approved processing scope/configuration is in place, set staging
   `LLM_CUSTOMER_CONTEXT_ENABLED=true` through the existing secret/config refresh process. Confirm
   workers and API both receive it. Enable staging Meta sending only for the authorized test setup.
2. Set the approved exact `WEBSITE_KNOWLEDGE_URLS` JSON list. Stage each selected URL with
   `python scripts/stage_website.py --cco-username <actual-CCO-username> --url <approved-url>` under
   authorized CCO operation. Review drafts in the existing knowledge admin flow. Activation is
   `POST /api/knowledge/documents/{document_id}/activate` with authenticated CCO session/CSRF and
   `{"effective_at":"<approved-ISO-date>"}`. The agent must not log in as or invent approval by Jane.
3. Exercise actual staging WhatsApp in EN/MS/ZH: informational questions and followups, mixed
   identity/business questions, contextual offer acceptance with separate consent, negative consent,
   combined/out-of-order fields, all contact corrections, side questions, No/Done/Skip, missing
   mandatory fields, expiry, pause/resume/cancel, issue collection, corrected review/submission,
   legitimate human/safety/fraud/complaint/account/partnership requests, existing-case confirmation,
   and a new unrelated issue after a closed case. Verify persisted values and exactly one ticket.
4. Exercise text captions and attachment-only messages, uploads before/during/after intake,
   pending/approved/rejected scans, confirmed existing-case evidence, and unrelated evidence groups.
   Verify ownership and staff display, not merely reply text.
5. Exercise duplicate and out-of-order webhooks, quoted stale Yes, equal timestamps, retries,
   concurrent senders, worker termination after claim/provider attempt and before commit, outbound
   retry ordering, provider timeout/invalid/ungrounded output, and DB/ClamAV/S3/Meta/SMTP recovery.
   Verify durable inbox/outbox, no lost fields, no duplicate consent/tickets/notifications, beta
   limits, and no second provider call on a reclaimed attempted event.
6. Measure actual inbound-to-Meta-accepted latency under representative four-way bursts: >=95%
   exactly one response within 30 seconds; p95 below 30 seconds. Preserve the approved call/message
   and spend caps. Record model usage including reasoning, rejection/fallback rates and costs.
7. Repeat the existing production readiness/security/dependency/backup/restore/rollback/roll-forward
   checks described in `docs/production-platform.md`, `docs/release-validation.md`, and CI/security
   workflows on these exact digests. `/ready` alone only validates DB migration, not release fitness.
8. Record actual results and CCO review. Any code change means a new SHA/image and renewed relevant
   staging validation. Only a fully passed staging SHA/digest pair is eligible for production.

#### 5. Production deployment, activation, observation, rollback

1. Obtain the recorded Cze Yik GO after all preceding gates, with published privacy notice, approved
   corpus/effective dates and current waiver/beta validity. Capture current production state and
   rollback pair read-only; historical production SHA in `docs/release-validation.md` is not proof
   of today's deployed state.
2. Back up and check duplicate owners before migration. Set the production runtime secret
   `dudu-support/production/runtime-env` to `META_SEND_ENABLED=false` and
   `LLM_CUSTOMER_CONTEXT_ENABLED=false` for the dark deploy. Plan the inbox/worker transition so
   received events remain durable; never purge the inbox or replay old processed events.
3. Dispatch: `gh workflow run release.yml --ref <release-ref> -f environment=production -f release_sha=<exact-staging-tested-40-character-SHA>`.
   The workflow reuses the staging ECR image pair. Verify workflow/SSM success, the exact digests,
   migration `d9010a1b2c3d`, API/worker health, and `https://support.duducaradmin.com/ready`.
4. Stage/activate only the same CCO-approved production knowledge snapshots through the audited
   knowledge flow; verify effective dates and source versions match release evidence. Set the
   approved runtime allowlist and expiry. Do not make arbitrary production knowledge edits.
5. Activate as the separate controlled config change: `LLM_CUSTOMER_CONTEXT_ENABLED=true`, restore
   the authorized `META_SEND_ENABLED`/beta settings, refresh the API and worker through the existing
   compose/secret process. Keep notifications under the explicit valid waiver or approved setting.
   Never change the eight-second/300-token/model/budget defaults without new evidence and review.
6. Observe for 60 minutes and record readiness, delivery/read states, duplicates/dead letters,
   intake/ticket counts, consent/evidence integrity, model outcomes/rejections/errors, p95, host
   resources, alarms, backup, spend and beta volumes. Apply the existing launch-contract triggers:
   immediate disable for security/data-loss triggers; pause/rollback after 15 minutes above 5%
   failures/duplicates or p95 >30s, total answer failure, availability <99%, total spend forecast
   >USD65/actual USD70, or AWS actual USD30. Reconfirm current approved thresholds before activation.
7. Rollback: first disable customer-context transmission and outbound sending, preserve and
   deliberately drain/pause the durable inbox, then use `/opt/dudu/rollback-images` with the existing
   `/opt/dudu/current/deploy.sh <app-digest> <clamav-digest>` process through SSM. **Do not downgrade
   the migration.** An old worker does not understand the queued inbox; account for every
   queued/processing event before returning to that image. If compatibility cannot be assured,
   keep traffic paused and forward-fix rather than losing/replaying events.
8. Final deliverable: committed SHA, staging/production immutable app+ClamAV digests, actual complete
   test/evaluation/CCO/staging/readiness results, publication/effective-date and GO records,
   activation timestamp, observation outcome, and verified rollback pair in the release record.

---

## Revised execution architecture — takes precedence over earlier details

This revision incorporates the user's review request and approval to revise the handoff. The goal
is natural GLM conversation with grounded answers and human support only when needed. Implement
the shared conversation model below rather than adding one keyword exception per example.

### Conversation interpretation and context

Use at most one GLM call per eligible inbound turn to interpret ordinary language, mixed intent,
clarification, troubleshooting and intake interruptions. Local code enforces consent, validation,
permissions, ticket mutations, urgency and notifications. Model output proposes actions; it does
not authorize or execute them. Preserve narrowly defined immediate-safety responses.

Only standalone, unambiguous identity questions should short-circuit to a local response. For
"Hi, are you human? How do I book a car?", answer the business question as well. Do not introduce
another broad keyword router for greetings, scope, complaints or prohibited actions before GLM.

Maintain bounded sanitized context: current topic, relevant source IDs, previous question/options,
troubleshooting already attempted, pending offer, intake stage and field-presence indicators.
Use a short sanitized prior exchange where needed, with explicit size limits. Raw history,
contact values, ticket bodies and attachments remain excluded from provider input. This revises
the earlier current-question-only restriction: it cannot support "What about tomorrow?", "I tried
that", "the second option", or corrections naturally. Update the privacy/data-flow documentation
accordingly. Prior model answers are context, never authoritative policy.

The response schema must support answering, clarifying, troubleshooting, offering handoff,
interpreting intake, correcting fields, pausing/resuming, cancelling and submitting. These may
be separate fields: one turn can answer a side question and correct a field. Require citations
for business claims, not for clarification, small talk or local identity statements. Add a clear
clarification path before escalation when the request or antecedent is ambiguous.

### Flexible local intake

Keep a local validated intake record. Ask only for missing required information, accept multiple
fields in any order, support corrections and preserve information across side questions. Separate
pause, cancellation, expiry and submission. Remember pending ticket offers without forcing intake,
so a contextual acceptance works while unrelated acknowledgements never imply consent.

Fix these additional confirmed weaknesses in `app/services/chatbot.py` and shared callers:

- `_continue_intake` treats `No` as cancellation at every stage. "No" to "Any more details?" must
  finish details, not erase the ticket. Distinguish refusal of consent from missing information.
- A sentence containing name and email can become the entire name. Extract and validate fields
  locally and ask about ambiguous assignments rather than repeatedly requesting supplied details.
- Corrections at later stages are appended as ride details rather than updating the intended field.
- `_capture_supplied` repeatedly defaults to the WhatsApp sender number, overwriting an explicitly
  chosen alternative contact. Default only when no explicit contact exists.
- A ticket initiated by "I need a human" never necessarily collects the actual issue. Obtain a
  useful issue description and include relevant later details for staff.
- Supplying a structured trip ID marks all details complete. Field presence is not submission intent.
- Accumulated ride details can exceed the final 2,000-character schema limit and fail at submission.
  Bound/validate accumulation early and preserve details without silent truncation.
- Only safety has an explicit priority update during intake. Reassess newly disclosed fraud/payment
  information against the intended case without keyword-only urgency upgrades.
- Provide a concise local summary and correction opportunity before ambiguous extracted information
  is submitted. Do not add repeated confirmations after an unambiguous review/submission.
- Missing required email/name/phone currently causes a dead end. Explain requirements and an honest
  recovery path. Do not claim submission or silently relax mandatory fields; any partial-ticket or
  alternative-contact policy change requires an owner decision if needed.
- Support additional details/corrections for the intended existing case after submission. Distinguish
  new issues from continued cases; do not create duplicates or reopen unrelated closed tickets.

Separate inbound local parsing, permitted local storage and provider payload construction. A
stronger provider redactor must not erase email/phone values before local intake extracts them.
Keep actual values local and supply opaque placeholders and field roles to GLM. Do not claim regex
redaction guarantees anonymity for arbitrary prose. If minimization is uncertain, use local
clarification instead of forwarding raw sensitive content.

### Knowledge and reasoning

Fix candidate selection as well as ranking: content-only candidate filtering currently prevents
tag-only synonyms from retrieving a document, and limiting before ranking can exclude the best
website source. Use bounded broader retrieval or clarification on a lexical miss; do not equate
the existing overlap score with answerability. Reuse existing PostgreSQL and helpers first.

Separate source language from response language. Approved English website facts may be faithfully
translated into Malay/Chinese, with evaluation; this does not create an independently approved
translated policy. Do not invent source versions or exclude users solely for asking in another
language. This supersedes the earlier prohibition on cross-language retrieval.

Citation, number and word-overlap checks do not establish factual entailment. Test plausible invented
claims that share words/numbers with their sources. Permit straightforward deductions from approved
facts, validate calculations locally where applicable, and preserve conditions/units. Never invent
eligibility, exceptions, prices, account status or completed actions. Define precedence for conflicting
seed/website facts; clarify unresolved conflicts instead of silently combining them. Verify website
extraction preserves relevant nested text, lists, tables, conditions and links before approval.

### Evidence ownership and state consistency

Current ticket creation and media scanning use different timestamp-based attachment selection.
An upload before intake can be omitted when scanned early but attached when scanned later, or attached
to the wrong later case. Bind evidence to the intended intake/case explicitly. Scan timing determines
availability, not ownership. Test uploads before/during/after intake and pending/rejected evidence.
Media stays out of GLM: answer caption text or ask for a description without claiming to see content.

Inspect existing locks and shared callers, then ensure concurrent/duplicate/out-of-order events cannot
lose fields, create duplicate conversations/tickets, or duplicate notifications. Conversation lookup
currently has no uniqueness guarantee in its index. Bind consequential replies to active prompt/state
using available metadata, without requiring every WhatsApp user to quote replies manually.

Provider calls currently run during webhook processing before commit. Test realistic provider latency
under bursts. Avoid long database locks over network calls; use state-version checks or existing queue
patterns if needed. Preserve event durability, ordered replies and beta caps. Never promise ticket
creation before commit succeeds.

### Revised evaluation and rollout requirements

Use at least 100 distinct non-escalation scenarios represented in all three languages, paired with
legitimate escalation cases. Include held-out paraphrases and multi-turn sessions. Count distinct
scenarios separately from translations and repeat runs. Measure unnecessary escalation, missed
necessary escalation, answer correctness and intake completion separately.

- At least 95% correct non-escalating behavior overall and per language on the reviewed suite.
- At least 95% relevant grounded answers for answerable sessions overall and per language.
- At least 95% appropriate handoff for legitimate escalation sessions; all critical safety and
  explicit-human-request regressions must pass.
- At least 95% correct completion for cooperative intake sessions; all critical consent, correction
  and data-preservation regressions must pass.
- Zero unintended consequential mutations in regressions: consent, ticket creation/reopening,
  urgent upgrade, staff notification, or cross-case evidence assignment.
- Zero prohibited sensitive values in captured provider inputs for privacy regression cases.
- Existing end-to-end p95 below 30 seconds under representative concurrency and approved spend caps.

These replace blanket "all linguistic cases pass" wording below while retaining zero-tolerance
regression checks for consequential errors. Passing a finite suite does not prove coverage of 95%
of all possible human utterances. Report denominators, per-family failures and held-out results.

Historical cost/latency figures below are context, not guarantees. Verify actual model availability,
pricing and token accounting; estimate costs from representative new prompts and bounded context.
The previous USD 30 figure is not a hard upper bound: character limits do not establish token limits,
and reasoning usage must be accounted for. The 300-token output cap may not fit richer structured
responses; adjust only with measured evidence and corresponding production validation changes.

Execution order: reproduce bugs; fix local intake/state and provider minimization; add context and
single-call interpretation; fix retrieval and governed website staging; verify case/media/transport
integrity; update documentation; run expanded live/outage and staging evaluations. Deliver code,
runnable checks, scenario results, actual cost/latency projections and rollout/rollback evidence.
Follow CCO publication and existing production go/no-go controls. No impersonated approvals, raw
customer test transmissions, or unrelated infrastructure expansion.

## Objective

Make the DUDU Car chatbot answer straightforward customer questions intelligently from approved
knowledge instead of prematurely or repeatedly directing customers to human follow-up. Preserve
the capped-beta limits, deterministic safety controls, knowledge governance, privacy redaction,
and outage fallback.

Success means that natural English, Bahasa Malaysia, and Simplified Chinese paraphrases of
approved support questions receive relevant grounded answers, while explicit human requests,
genuine incidents, complaints, prohibited account actions, and unknown questions follow the
appropriate controlled path.

## Confirmed diagnosis

The GLM API is enabled in production with `glm-5.3-flash`; this is not primarily a provider outage
or model-quality problem. The application currently prevents GLM from acting as the chatbot's
reasoning layer:

1. `ChatbotService._respond` applies deterministic ticket rules and lexical retrieval before GLM.
   A ticket decision cannot be reconsidered by the model (`app/services/chatbot.py`).
2. Low retrieval confidence immediately starts ticket intake rather than offering an optional
   escalation while leaving the conversation idle.
3. Retrieval uses literal token overlap. Semantically equivalent wording such as "order a car"
   and "book a ride" can fail to match (`app/services/retrieval.py`).
4. Broad complaint terms such as `problem`, `issue`, and `report` can trigger ticket intake even
   when used in an ordinary informational question (`app/services/guardrails.py`).
5. Once ticket intake starts, all later messages are handled as intake input. Natural cancellation
   or a new question is ignored unless it exactly matches a small allowlist such as `No`, `stop`,
   or `never mind` (`app/services/chatbot.py`).
6. GLM receives only retrieved excerpts, not the customer's question. It is therefore an answer
   rewriter, not a question-answering reasoner (`app/services/answer_generation.py`).
7. Production beta traffic inspected during the investigation made zero GLM-generation calls.
   The observed conversations entered complaint or unconfirmed-question intake instead.
8. Existing release evaluation uses canned phrases and verifies routing, source selection, and
   provider calls, but does not adequately measure paraphrase quality, negation, interruption,
   or recovery from unwanted ticket intake (`scripts/release_eval.py`).

## Decisions approved by Cze Yik

- Send the redacted current customer question and bounded sanitized context to Z.AI for reasoning.
- Do not send customer identifiers, raw sensitive data, tickets, attachments, or full conversation
  history to Z.AI.
- Use one GLM request for normal question answering and disposition; do not add a separate model
  routing call.
- Keep deterministic handling for security, immediate safety, explicit human requests, complaints
  that genuinely require review, and prohibited account actions.
- Unknown questions may offer a support ticket but must not enter ticket intake until the customer
  explicitly accepts.
- Bot-identity questions must receive a deterministic answer identifying the assistant as an
  automated AI assistant; they must not trigger GLM or human follow-up.
- Harmless greetings and small talk should receive a brief natural response and redirect to DUDU
  Car support without human follow-up.
- Questions unrelated to DUDU Car should receive a concise scope redirect without a ticket offer.
- Questions about DUDU Car ownership, directors, licensing, or management may be answered only from
  approved corporate sources. Never infer ethnicity, nationality, or another personal
  characteristic from names, language, appearance, or location. If the requested company fact is
  not verified, say so; offer a ticket only when the customer explicitly asks the company to reply.
- Anger, profanity, or criticism directed at the chatbot is conversational frustration, not by
  itself a complaint requiring human follow-up. Respond calmly, acknowledge the failed interaction,
  stop any unwanted consent loop, and invite the customer to restate the question. Do not retaliate,
  lecture, claim feelings, or create a ticket merely because profanity was used.
- Distinguish chatbot-directed frustration from a genuine complaint about a driver, ride, payment,
  DUDU Car service, or another reviewable business event. An explicit human request still starts
  the consent flow, and a credible threat or immediate danger still uses the safety flow.
- Natural cancellation, negation, and new questions must be able to exit or interrupt pending
  consent.
- Use selected, reviewed `duducar.co` support and policy pages as governed knowledge.
- Do not ingest the entire website sitemap indiscriminately.
- Website content must be snapshotted as a draft, reviewed by the CCO, assigned an effective date,
  and activated before customer retrieval can use it.
- Changed website content must return to draft review rather than silently changing active answers.
- Preserve verified source-language metadata; allow faithful, evaluated translation into the
  requested response language without inventing translated source records.
- Preserve deterministic fallback when GLM is unavailable, invalid, unsafe, or ungrounded.

These decisions supersede the current rule that only approved knowledge, never a customer message,
may be sent to Z.AI. Update the affected approved documentation and customer-facing privacy copy
before activation. Permitted provider context follows the revised architecture above.

## Target architecture

```text
Inbound customer message
        |
        +-- redact sensitive data
        |
        +-- deterministic security, immediate-safety, and bot-identity checks
        |
        +-- local field extraction and bounded sanitized conversation context
        |
        +-- retrieve relevant active knowledge
        |      +-- approved seed corpus
        |      +-- CCO-approved website snapshots
        |
        +-- one GLM request
               +-- redacted current question
               +-- sanitized topic, previous prompt and intake field-presence context
               +-- relevant approved excerpts
               +-- requested response language
               +-- structured result with disposition, answer, and citations
        |
        +-- validate proposed changes and apply authorized local state/ticket operations
```

Suggested structured dispositions include `answer`, `clarify`, `troubleshoot`, `offer_ticket`, and
`explicit_handoff`, plus proposed intake continuation/correction/pause/cancel/submission. The exact
schema may differ if a smaller change fits the existing code, but output must remain validated and
grounded. Do not give GLM action tools or authority to create tickets directly.

Because website knowledge can exceed the existing 8,000-character prompt limit, retrieve a bounded
set of relevant active chunks before generation. Do not send the complete website or full corpus
on every request.

## Minimal implementation scope

### 1. Answer generation

- Change `ApprovedKnowledgeResponder.generate` and `_messages` to accept the already-redacted
  current question and bounded sanitized context described in the revised architecture.
- Include that question in the model prompt.
- Require structured JSON containing a validated disposition, answer, and non-empty citations when
  an answer is returned.
- Continue rejecting ungrounded numbers, URLs, unsafe action claims, malformed output, and invalid
  citations.
- Preserve the deterministic fallback and the existing provider-neutral adapter.
- Keep one provider call per eligible customer message, `reasoning_effort: low`, the eight-second
  timeout, and bounded input/output sizes unless evaluation demonstrates a necessary change.

### 2. Routing and ticket intake

- Attempt a grounded answer for ordinary informational questions before offering human follow-up.
- Answer bot-identity and basic capability questions deterministically without a provider call.
- Handle greetings and harmless small talk briefly, then redirect to DUDU Car assistance.
- Redirect unrelated general-knowledge questions to the DUDU Car scope without offering or starting
  a ticket.
- Answer corporate ownership, leadership, licensing, and management questions only from approved
  corporate knowledge. Do not infer ethnicity, nationality, or other personal characteristics.
- Treat insults, profanity, and frustration directed at the chatbot as a recoverable conversation
  failure. Give one concise de-escalating response, cancel unwanted pending consent when indicated,
  and return to normal question handling.
- Set a brief neutral boundary for repeated abuse that contains no support request. Do not escalate
  merely as punishment; retain the existing rate limit for spam and abuse volume.
- Narrow broad complaint matching so words such as `problem`, `issue`, or `report` alone do not
  force a ticket when the message is an informational question.
- Retain urgent safety and fraud handling and explicit human escalation.
- For low-confidence or unsupported questions, answer that the information cannot be confirmed and
  offer a ticket without changing `conversation.intake_state`.
- Start consent intake only after an explicit acceptance or explicit ticket request.
- Expand yes/no/cancel understanding to natural negation and cancellation in all three languages.
- While awaiting consent, treat a clear unrelated question as a new question rather than repeating
  the consent prompt.
- Do not weaken consent requirements for actual ticket creation.

#### Known false-escalation cases to eliminate

The current implementation makes decisions from isolated keyword presence and exact state-machine
phrases. The implementation and evaluation must cover these collision classes, not only the example
wording:

- Bot-directed anger: `angry`, `rude`, `terrible`, `bad service`, `not happy`, insults, and profanity
  can be criticism of the automated interaction rather than a reportable business incident.
- Generic problem language: `issue`, `problem`, `report`, `aduan`, `masalah`, `问题`, and similar words
  can appear in informational questions, instructions, quotations, or hypothetical examples.
- Human-related words without human intent: questions such as "Are you human?", "What are human
  support hours?", or text containing `agent` or `representative` do not necessarily request a
  handoff.
- Informational safety language: questions about accident procedure, harassment policy, hospitals,
  police, emergencies, or safety rules do not necessarily report a current incident. Preserve the
  urgent path when the message describes a credible current event or immediate danger.
- Informational fraud language: questions about fraud prevention, scams, or unauthorized-transaction
  policy do not necessarily report that fraud occurred to the customer.
- Informational partnership language: questions about existing partnerships or collaboration policy
  do not necessarily propose a new partnership.
- Quoted or negated actions: "The app says cancel my ride", "Why can't I delete my account?", or
  "I do not want a refund" must not be treated as commands merely because an action phrase appears.
- Paraphrases, typos, and morphology: literal token overlap can miss `order` versus `book`, singular
  versus plural, common misspellings, and other semantically equivalent wording.
- Long but relevant questions: the current confidence score divides overlap by every query token, so
  added detail can lower confidence below the escalation threshold.
- Short, mixed-language, or misdetected messages: language selection can search the wrong language
  corpus, particularly for short Malay messages without one of the fixed hints.
- Stopword-only and conversational messages: greetings, acknowledgements, and basic identity or
  capability questions can produce no retrieval tokens and fall into unconfirmed-question intake.
- Sticky intake at every stage: a new question can be mistaken for consent, a name, ride details, or
  additional evidence after any ticket intake has started. Interruption handling is required beyond
  only the `awaiting_consent` state.
- Stale intake across sessions: `started_at` is stored but never used to expire or reset intake, so a
  user returning hours or days later can still be forced through an abandoned ticket flow. Define a
  bounded intake expiry and return expired conversations to normal question handling without
  creating a ticket.
- Unconditional closed-ticket reopening: `ChatbotService.handle` currently calls
  `reopen_closed_ticket_for_customer` before interpreting every message. A greeting, identity
  question, unrelated question, or ordinary FAQ can silently reopen the customer's latest closed
  ticket and notify staff. Reopen only on explicit intent to continue that case, ideally with an
  unambiguous ticket reference or confirmation.
- Unbound WhatsApp replies: the webhook stores `context_message_id` but the chatbot does not use it.
  A delayed or quoted `Yes` can be applied to the wrong prompt or intake stage. Consent and other
  consequential replies must be bound to the active prompt/state and handled safely when stale or
  out of order.
- Language-selection commands during intake: a request such as "speak English" can be processed as
  a name or ride detail after switching the response language. Language commands must change only
  the language and then repeat or resume the correct prompt.
- Safety words during unrelated intake: any later message containing a safety keyword upgrades the
  pending ticket to urgent even when it is informational, quoted, or a new question. Urgency changes
  require evidence of a current incident, not keyword presence alone.
- Media without incident intent: an image or video sent for an informational question must not
  automatically force ticket collection solely because an attachment exists.
- Bare acknowledgements and reactions: `yes`, `thanks`, emoji-only messages, or other context-free
  replies received while idle must not create an unconfirmed-question ticket offer. When reply
  context is unavailable, ask a neutral clarification without escalation.
- Repeated ticket language in valid answers: the deterministic fallback appends a ticket invitation
  to every grounded answer, and several approved excerpts mention human review. An answerable FAQ
  must not sound escalated merely because the source contains escalation wording. Mention a ticket
  only when the selected disposition warrants it or the customer says the answer did not resolve
  the question.
- Security phrases in benign context: quoted or educational discussion of phrases such as "ignore
  previous instructions" must not automatically become a hostile prompt-injection refusal when the
  surrounding request is clearly benign. Maintain a secure refusal for actual instruction override
  attempts.
- Mixed-intent priority collisions: a message can contain both an ordinary question and a trigger,
  or both a security phrase and a genuine safety report. Define explicit precedence so the system
  answers what is safe to answer, never misses immediate danger, and does not escalate solely on the
  lower-confidence interpretation.
- Client-supplied control fields: `create_ticket` and `consent_to_ticket` bypass natural-language
  intent when set by an API client. Accept them only as deliberate UI/API actions with tested
  provenance; accidental defaults or stale client state must not start intake or imply consent.

The implementation should determine intent from the whole redacted message and conversation state.
Keywords may remain high-recall signals, but except for narrowly justified security or immediate
safety cases, a single keyword must not be sufficient to start ticket intake.

### 3. Knowledge retrieval

- Improve retrieval for paraphrases and synonyms without introducing a new service prematurely.
- Reuse PostgreSQL and its existing `pg_trgm` support where useful, and retain bounded in-process
  ranking. Add curated aliases/tags for known support concepts if that is the smallest reliable
  solution.
- Evaluate retrieval using natural phrasings rather than only exact corpus vocabulary.
- Introduce embeddings or a vector database only if the expanded evaluation proves the simpler
  retrieval approach inadequate. With the present corpus size, they are not a default requirement.

### 4. Website knowledge

The repository already includes an HTTPS-only `duducar.co` extractor and staging command in
`app/services/website_knowledge.py` and `scripts/stage_website.py`. Production currently contains
only 24 active `cco_approved_corpus` documents—eight topics in three languages—and no website
documents.

- Add an explicit allowlist for customer-support, service, booking, payment, chargeback/dispute,
  driver-conduct, and other CCO-selected policy URLs.
- Exclude general marketing pages, counters, promotional claims, contact forms, and unrelated
  partnership announcements unless specifically approved as support facts.
- Fix the staging workflow: it currently creates website drafts without `effective_at`, while
  activation rejects documents without an effective date. Ensure a reviewed draft can receive an
  effective date and be activated through the governed workflow.
- Keep drafts inactive until CCO approval.
- Preserve URL, content hash, version, approver, effective date, and audit history.
- On a later crawl, create a new draft only when content changed. Never overwrite or automatically
  activate the current version.
- Retrieval and GLM prompts may use only effective `active` versions.
- Z.AI must never crawl `duducar.co` directly; the application supplies only selected approved
  snapshot excerpts.

### 5. Privacy and documentation

- Update `docs/requirements-summary.md`, `docs/privacy-notice-chatbot-addendum.md`,
  `docs/launch-contract.md`, the relevant customer copy, and data-flow/security documentation to
  reflect the approved transmission of the redacted current question and bounded sanitized context
  to Z.AI in Singapore.
- State the minimization boundary accurately: no raw identifiers, ticket bodies, attachments,
  account data or full conversation history are sent; bounded sanitized context is allowed.
- Extend `redact_sensitive` before sending questions to Z.AI. It currently redacts payment cards,
  identity numbers, and labelled secrets, but does not generally redact email addresses, phone
  numbers, or names embedded in free text. Structured customer fields must remain excluded, and
  free-text email/phone patterns must be covered. Handle names conservatively without destroying the
  meaning needed to answer the support question.
- Keep the existing API DPA/provider review record and update it if the approved processing scope
  needs amendment.
- Update tests that currently assert no part of the customer message reaches the provider. Replace
  that assertion with proof that the redacted question is present while identifiers and sensitive
  values are absent.

### 6. Observability

- Record provider outcome, latency, prompt/completion token counts, grounding rejection, and
  deterministic fallback in operationally visible structured logs or metrics.
- Do not log raw prompts, raw customer questions, identifiers, or provider responses.
- Add aggregate measurements for answered, ticket-offered, ticket-started, provider-failed, and
  grounding-rejected outcomes.

## Cost and capacity constraints

The previous live release evaluation recorded USD 0.002030 for 15 model calls, approximately
USD 0.000135 per call. At that observed rate, 10,000 calls would be about USD 1.35. Larger prompts
containing the redacted question and more knowledge make USD 2-5 a reasonable beta planning range.

The exact public price for `glm-5.3-flash` was not listed on the Z.AI pricing page during this
investigation. Using the published, more expensive GLM-5 rates as a conservative proxy and the
assumed token usage previously gave an illustrative USD 30 scenario for 10,000 one-call messages.
This is not an upper bound or a verified price for the configured model. Recalculate using measured
context/reasoning usage and verified billing rates. A separate routing call is not the default design.

Remain within the existing capped-beta controls:

- 200 messages per user per day.
- 2,000 messages per day globally.
- 10,000 messages total.
- Existing USD 15 hosted-model allowance and overall launch spend controls.
- At most one GLM call per eligible inbound message.
- No larger EC2 instance or new managed data service unless measurements prove it necessary and the
  owner approves the additional scope.

## Required executable checks

Follow the repository's existing test style and leave the smallest reliable executable checks for
all non-trivial logic. At minimum cover:

- A redacted question reaches the fake provider, but name, email, phone, account/trip identifiers,
  payment-card data, OTPs, and external user ID do not.
- Natural paraphrases of booking, fare/payment, promotion, login, driver onboarding, support hours,
  and approved website topics retrieve the correct source and produce a grounded answer.
- A fresh "How do I order a car?"-style question is answered rather than escalated.
- "Are you AI?" and "Are you a human?" receive the deterministic bot-identity answer, make no GLM
  call, and do not offer or start a ticket.
- Greetings and harmless small talk respond briefly without escalation.
- Unrelated questions redirect to DUDU Car support without offering or starting a ticket.
- Questions that ask whether the company is run by people of a particular ethnicity or nationality
  do not cause demographic inference. The chatbot answers only with verified approved corporate
  facts or says that it lacks verified information, without automatic escalation.
- Chatbot-directed anger, insults, or profanity receive a calm recovery response and no ticket
  offer. If the message also contains a substantive DUDU Car question, that question is answered.
- Anger about a concrete driver, ride, payment, or service event remains eligible for the genuine
  complaint flow; credible threats or immediate danger remain eligible for the safety flow.
- Informational, hypothetical, quoted, and negated uses of every complaint, human, safety, fraud,
  partnership, and prohibited-action keyword do not start intake solely because of that keyword.
- Relevant long-form, typo-containing, mixed-language, and natural paraphrase questions do not fall
  below the answer path solely because of lexical scoring artifacts.
- A new question interrupts any ticket-intake stage safely rather than being stored as a name,
  consent decision, ride detail, or evidence description.
- Abandoned intake expires after the approved interval and a later greeting or FAQ resumes normal
  support without escalation.
- A greeting, FAQ, identity question, or unrelated message never reopens a closed ticket. Explicit
  case-continuation intent and confirmation reopen only the intended closed ticket.
- Delayed, quoted, stale, or out-of-order WhatsApp replies cannot provide consent or populate the
  wrong intake field merely because their text is `Yes`, `No`, `Done`, or another state keyword.
- A language-switch command during every intake stage changes language without being stored as
  consent, name, contact data, ride details, or evidence.
- Informational safety wording during an existing normal intake does not upgrade urgency; a paired
  current-danger statement still upgrades it immediately.
- Informational media messages do not force ticket intake when no incident, complaint, human request,
  or explicit ticket request is present.
- Context-free acknowledgements, thanks, reactions, and emoji-only messages do not offer or start a
  ticket while idle.
- Grounded FAQ answers and deterministic fallbacks do not append generic human-follow-up language
  unless the response disposition is `offer_ticket` or the customer explicitly remains unresolved.
- Deliberate API control actions still work, while absent, accidental, or stale `create_ticket` and
  `consent_to_ticket` values cannot falsely start intake or create a ticket.
- An unsupported DUDU Car question may offer a ticket but leaves intake idle.
- A subsequent ordinary question can be answered after an unsupported question.
- Natural phrases equivalent to "I don't need a human follow-up" and "stop bro" cancel pending
  consent; add Malay and Chinese equivalents.
- Explicit `Yes` still starts/continues intake, explicit `No` cancels it, and a ticket is never
  created without consent and required details.
- Informational uses of `problem`, `issue`, and `report` do not automatically create tickets.
- Explicit human requests, genuine complaints, fraud, safety emergencies, and prohibited account
  actions retain their required behavior and priority.
- Provider timeout, invalid JSON, invalid citations, unsupported numbers/URLs, and unsafe claims
  fall back safely.
- Website staging enforces the host/redirect/size limits, uses the allowlist, ignores unwanted HTML,
  creates inactive versions, supports assigning an effective date, and never auto-activates
  changed pages.
- Website knowledge is unavailable before activation and retrievable after CCO activation.
- Approved English website facts can support faithful Malay/Chinese answers; tests verify meaning,
  conditions and source attribution without inventing translated source versions.

Expand `scripts/release_eval.py` with varied natural phrasing, negation, multi-turn interruption,
and unsupported questions. Semantic answer relevance—not merely a non-empty source list—must be
part of evaluation.

Build a CCO-reviewed false-escalation matrix with no fewer than 100 cases and representation in all
three launch languages. Cover every configured complaint, human, safety, fraud, partnership,
account-action, and prompt-injection trigger in multiple intent forms where linguistically valid:

- genuine affirmative incident or request;
- informational question;
- negation;
- quotation or reported speech;
- hypothetical or policy question;
- chatbot-directed criticism;
- unrelated homonym/context;
- mixed intent;
- active, stale, and interrupted intake state; and
- fresh, active, and closed-ticket conversation state.

Include retrieval misses, long queries, typos, code-switching, greetings, reactions, media captions,
and client control-field cases in the same suite. Record expected `disposition`, intake state,
ticket-state mutation, urgency, provider-call count, and whether a ticket was offered. Measure false
escalation as any unexpected ticket offer, intake transition, ticket creation/reopening, urgency
upgrade, or staff notification.

## Acceptance criteria

Implementation is ready for staged release only when all of the following are true:

1. At least 95% of an expanded, CCO-reviewed trilingual evaluation set receives the expected
   disposition and, where answerable, a factually correct grounded answer.
2. At least 95% of answerable paraphrases across every active knowledge topic are answered without
   human follow-up.
3. Zero test cases create a ticket without explicit consent and the existing required contact data.
4. Zero prohibited identifiers or sensitive values reach the provider in automated redaction tests.
5. Every generated factual answer cites only active, effective knowledge supplied in that request.
6. Unsupported questions do not lock the conversation into ticket intake.
7. Explicit safety, fraud, human, complaint, and prohibited-action flows retain their approved
   priorities and wording requirements.
8. Provider failure produces the approved deterministic fallback and never causes total answer
   failure.
9. End-to-end p95 remains below the existing 30-second launch threshold under representative
   concurrency. The historical 3.679-second evaluation is a comparison, not a fixed requirement.
10. Projected model spend remains within the USD 15 beta allowance and at most one provider call is
    made per eligible inbound message.
11. Website changes cannot reach customers without a versioned CCO approval and effective date.
12. The full automated suite, live-model evaluation, outage evaluation, staging WhatsApp flow, and
    production readiness checks pass before activation.
13. Evaluated identity, greeting, harmless small-talk, unrelated, and unverified demographic
    questions produce the approved non-escalating behavior; none starts or offers ticket intake
    unless the customer explicitly asks the company to respond.
14. Evaluated chatbot-directed anger, keyword-collision, negation, quotation, hypothetical,
    paraphrase, long-query, language-mismatch, intake-interruption, and informational-media cases
    avoid false escalation, while paired genuine incident cases still take the required controlled
    path.
15. At least 95% of the CCO-reviewed false-escalation matrix avoids every unexpected escalation
    effect, with no individual launch language below 95%. The denominator and failures must be
    reported; test omissions do not count as passes.
16. Regardless of the aggregate percentage, there are zero false ticket creations, zero false
    closed-ticket reopenings, zero false consent recordings, zero false urgent upgrades, and zero
    staff notifications caused by non-escalation scenarios. The 5% tolerance applies only to a
    non-mutating wrong response disposition or ticket offer during evaluation.

## Deployment sequence

1. Implement and test locally without modifying production knowledge or configuration.
2. Update and approve the privacy/data-flow documentation before enabling customer-question
   transmission.
3. Select and review the initial website URL allowlist with the CCO.
4. Stage website snapshots, assign effective dates, review them, and activate only approved pages in
   staging.
5. Run the expanded outage and live-model evaluations and record aggregate results, latency, token
   use, and projected cost.
6. Exercise natural multi-turn WhatsApp conversations in staging, including cancellation and
   switching from a ticket offer back to a general question.
7. Deploy the exact staging-tested immutable image to production through the existing release
   process.
8. Activate the behavior as a controlled change and monitor answer disposition, provider failures,
   grounding rejection, latency, spend, and ticket-start rate during the observation window.
9. Use the existing rollback process if launch thresholds fail; do not destructively reverse data
   migrations.

## Out of scope

- Giving GLM account, booking, refund, cancellation, payment, or ticket-creation tools.
- Sending attachments, full conversation history, tickets, or raw identifiers to Z.AI.
- Automatic publication of website changes.
- Crawling sites other than `https://duducar.co`.
- Adding web search to customer answers.
- Adding a vector database, second model call, larger host, or new dependency without evaluation
  evidence that the approved minimal design cannot meet the acceptance criteria.
