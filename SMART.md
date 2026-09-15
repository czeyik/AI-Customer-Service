# LangChain dialogue refactor — execution handoff

Prepared: 13 September 2026.
Repository: /home/czeyik/Documents/AI-Customer-Service.
Reference revision: 6d2ce8d (Record SMART activation state).
Status: **Waves 1–7 complete. Wave 8 source publication and staging preparation in progress; the LangChain candidate is not yet deployed.**

## 1. Objective and scope

Replace the current dialogue engine with one LangChain create_agent agent. LangChain owns
understanding, conversational replies, tool selection, clarification, interruptions, corrections,
and the choice of which missing ticket detail to request next. Python business functions own
validation and authorization. PostgreSQL owns durable state and mutations.

This is a substantial replacement of the conversation core, not a wrapper around its existing
state machine. Preserve FastAPI, PostgreSQL, WhatsApp transport, administration, knowledge approval,
ticket operations, media processing, notifications, retention, and deployment infrastructure.

The user selected LangChain deliberately. Do not reopen the LangChain-versus-LangGraph decision.
Do not introduce a custom LangGraph workflow, Rasa, Langflow, multiple agents, a vector database,
a second model, hosted tracing, or a generic orchestration/tool framework. LangGraph dependencies
installed by LangChain are expected; writing a separate graph is not part of this design.

This document replaces the previous SMART handoff in full. Historical evaluation reports remain
historical evidence, not results for this refactor. This plan supersedes the former implementation
requirements for one model call per turn, no model tools, and the fixed intake-state dialogue.
It does not change approved customer-service policies, required ticket fields, spend/traffic limits,
knowledge authority, or permission to perform account/refund/payment/booking changes.

Read AGENTS.md and follow its minimal-code rules. LangChain and one necessary provider integration
are intentional additions. No further dependency is justified merely because an example uses it.
Use the configured development subagents when useful, as explicitly requested in the current execution session. The application still has one dialogue agent.

The user explicitly included pushing this refactor to production in the execution scope on
13 September 2026. When asked to execute this handoff, complete implementation, source publication,
staging validation and production deployment through wave 8. Do not stop after local completion or
ask for the same deployment authorization again once the stated gates pass. This document update
does not start execution now.

Production deployment includes the necessary Git push, PR/merge through repository policy, image
publication, tested migration and release workflow. Preserve the currently authorized customer-
sending and notification settings: deployment alone does not authorize enabling previously disabled
external messages, extending the beta, publishing knowledge, or inventing CCO approval. Carry forward
actual session authorization and honor real repository/environment protection gates. Missing access
does not prevent independent work; record the exact remaining access or gate when reached.

## 2. Fixed architecture decisions

### 2.1 One application path

~~~text
WhatsApp durable inbox / existing Chat API
    -> ChatbotService.handle: claim/load, validate input, build snapshot
    -> sanitize recent messages + draft view + approved knowledge
    -> LangChain create_agent: bounded model/tool loop
    -> validate final result and staged business requests
    -> short transaction: recheck version/lease/confirmation, apply changes
    -> commit conversation + ticket + audit + notifications + outbound reply
    -> existing WhatsApp sender / Chat API response
~~~

Keep ChatbotService.handle and the external ChatRequest / ChatResponse interface where practical.
Update every internal caller whose semantics change. Do not maintain two production dialogue owners.
The old engine may remain temporarily callable for baseline tests during development only.

### 2.2 Responsibility boundaries

| Responsibility | Owner |
| --- | --- |
| Interpret intent and choose answer/clarification/handoff/draft action | LangChain agent |
| Choose next missing field and phrase ordinary questions | LangChain agent |
| Answer side questions and resume a draft naturally | LangChain agent |
| Required fields, bounds, consent evidence, ownership, priority, legal transitions | Python domain functions |
| Exact consent/review/receipt text and control metadata | Local rendering after validation |
| Customer identity, field values, evidence ownership and case data | Application/database |
| Event deduplication, sender ordering, leases and outbox retries | Existing transport |
| Provider failure, explicit local controls and urgent safety response | Small deterministic fallback/control boundary |

The domain returns facts such as missing_fields, invalid_fields and confirmation_required.
It must not choose a fixed name-then-email-then-phone sequence on the successful agent path.
Ordinary clarification and FAQ handling must not pass through keyword-based intake branches.
Preserve actual action prohibitions and urgent safety handling; do not remove guardrails wholesale.

### 2.3 Draft and state contract

Use Pydantic, already installed, for validated state. Add Conversation.dialogue_data as JSON and
Conversation.dialogue_revision as an integer revision. Keep Message as the transcript store.
Do not add a memory database or a custom checkpointer.

The versioned dialogue_data contains:

- schema_version (initially 1), optional draft, optional pending offer, pending prompt, the current
  unassigned evidence_group, and the last relevant case reference/operation receipt. Keep unassigned
  media ownership here even before a draft exists; transfer the group to the intended draft/case.
- Draft: ID, version, status (active, paused, cancelled, submitted), start/expiry timestamps,
  validated contact/issue/ride fields, completion flags, issue type/priority proposals, consent
  evidence, review requirement, evidence group and existing media metadata.
- Pending prompt: server-generated ID, purpose (offer, consent, field, details, review,
  case_confirmation), optional field name, draft/case reference and version, and originating turn.
- Confirmation/receipt metadata: the customer input that authorized an operation, matching
  prompt/version and resulting case reference. Keep it bounded; no unbounded operation journal.

No draft is represented by draft=null. Derive missing fields instead of persisting a second truth.
Keep customer language/risk/role on the existing Conversation fields. Increment the revision for
every committed dialogue/evidence change that can invalidate a running turn, including media
association changes. Separate the draft version from the conversation revision: transcript changes
need not invalidate a confirmed draft, but changes to reviewed draft contents do.

The pending prompt records what was actually asked. It supports input extraction and confirmation;
it is not an awaiting_* state machine prescribing the next conversational branch.

### 2.4 Preserve current submission semantics

These are requirements, not optional redesign decisions:

1. An optional handoff offer does not start intake. Accepting the offer leads to separate consent.
   Explicit human/incident requests retain their required intake behavior.
2. Record consent only from an actual current customer reply to the relevant prompt or a valid
   API control carrying that prompt ID. A tool argument consent=true is never evidence.
3. Name, valid email, valid contact phone and actual issue are required. Preserve WhatsApp sender
   phone defaulting without overwriting an explicitly supplied contact number.
4. Fields may arrive together or in any order. Corrections preserve other fields. Multiple candidate
   emails require clarification. A trip ID alone does not complete the issue or submit.
5. Preserve existing Done/Skip/No-more-details submission paths when consent and all required
   details exist, the reply matches the current details prompt, and no correction requires review.
   Preserve the existing valid API supplied-details completion path too.
6. If review is required, only explicit submission against the current review version submits.
   Corrections invalidate review. Do not add mandatory review to every legacy completion path.
7. No to more details differs from explicit consent withdrawal. Withdrawal cancels. No to a required
   contact field preserves the draft and explains the requirement.
8. Pause/resume/cancel, language switches, side questions, expiry and provider failures preserve
   appropriate fields. Expiry uses the existing configured interval.
9. Case updates/reopening require ownership plus confirmation tied to the intended case/update.
   A greeting or unrelated question never reopens a ticket.
10. Preserve accumulated-detail and case-update limits, priorities, evidence groups, required
    safety/response-time wording and notification behavior.

Use a small shared validator for consequential replies. Accept known local phrases; clarify
   uncertain authorization instead of trusting model confidence. Other dialogue remains agent-owned.
A side question cannot silently turn into a field value or consent. Record prompts actually sent;
an abandoned prompt proposal is not authorization context.

### 2.5 Tool contract

Implement ordinary Python functions with LangChain bindings in services/dialogue.py. Domain
functions live in services/ticket_drafts.py and existing ticket services. No tool registry layer.

| Tool | Responsibility |
| --- | --- |
| search_knowledge | Existing governed retrieval; approved excerpt IDs, versions and content |
| update_ticket_draft | Start/update a turn-local draft with validated customer field references; return missing/invalid fields |
| set_draft_status | Stage pause/resume/cancel with validated customer intent |
| prepare_ticket_review | Validate draft; stage exact local review and prompt/version metadata |
| request_ticket_submission | Validate current consent/completion evidence and stage one submission |
| get_owned_case | Resolve only a case owned by the runtime customer; return a minimized view |
| request_case_update | Prepare confirmation or stage the matching confirmed update/reopening |

Identity, channel, database access, prompt authority and field values come from runtime context;
they are not model-controlled arguments. Field references resolve only to current customer input
or an already validated draft. Reject unknown references and model-invented values.
Issue/detail collection may refer to the locally stored current message; the model must not
reconstruct private narratives from sanitized text. Never return raw contacts, ticket bodies,
attachment content, secrets or arbitrary database records to the model.

Tools stage changes in one turn-local working copy. They must not commit SQL, send messages, enqueue
notifications or claim success. Reads use short transactions ending before the next model call.
Later tools see the working copy. Identical requests are idempotent; conflicting submissions/
updates in one turn are rejected. Results say prepared/rejected, never that an uncommitted ticket
was created. Server-generated tool-result metadata identifies staged operations.

Final structured output contains answer text, cited excerpt IDs and a bounded next-prompt proposal
(purpose and optional field). It does not repeat the old action enum as a second router.
Validate consequential prompts against the working draft. Locally render exact consent/review/
submission/case-update messages. If a staged mutation conflicts with the final response, reject
the inconsistent result rather than carrying out a hidden action.

### 2.6 Atomic execution and recovery

1. Reuse inbound claiming and sender ordering. Persist the existing provider-attempt marker before
   the first external call. Its new meaning is one agent execution attempted, not one model call.
2. Load transcript/draft/revision and prepare private field references. Release the transaction.
3. Execute the bounded agent with turn-local tools. No long transaction spans model I/O.
4. Validate completed output, citations, staged operations and limits. Failure discards agent
   proposals; local fallback may process only independently valid explicit controls.
5. Lock/reload conversation and inbound claim. Recheck revision, lease token, ordering, prompt/
   version, validation and ownership. A stale result cannot mutate the new state.
6. Apply operations once through existing services. Commit ticket, audit, notification rows,
   messages, conversation, inbound completion and one outbox reply together.
7. Render definitive receipts from actual database results inside that transaction; expose them
   only after commit. Never return a model's speculative success claim.
8. Crash before commit: retry through deterministic recovery without another agent run.
   Crash after commit: inbound deduplication and existing outbox delivery handle recovery.

For the Chat API, retain prompt/version controls and revision checks. Repeated confirmation of
the same operation returns its recorded outcome without repeating the mutation. Do not claim
exactly-once handling of arbitrary unkeyed HTTP requests; do not add a general request ledger.
API tests must distinguish retrying a confirmation from making a new unrelated request.

### 2.7 Context, retrieval, provider and cost

- Persist messages once. Reconstruct agent input each turn; no separate persistent LangChain
  checkpointer, summary service, embeddings or long-term personal memory in this refactor.
- Start with at most 12 recent inbound/outbound messages, oldest first, also bounded by the existing
  input cap. Current question and authoritative draft/control metadata take precedence. Trim older
  history first and remove whole excerpts if needed. Count system prompt, tool schemas, history,
  tool results and excerpts. Do not truncate confirmation into a different meaning.
- Sanitize every historical message and tool result, not just the newest input. Raw reviews contain
  contacts: replace them with a safe marker/field-presence view. If minimization fails, omit that
  history item or request a local rephrase for the current input.
- Reuse pii.py and local extraction. Bare names may be interpreted locally when responding to an
  actual name prompt. Corrected historical values must not leak on later turns.
- Keep approved retrieval unchanged initially, including the 500-chunk ceiling. Supply an initial
  search from the current sanitized question; the agent may search with a context-resolved query.
  Deduplicate identical searches within a turn.
- Cite only excerpts actually supplied this turn. Previous bot statements are not policy. Reject
  factual output if the supporting source versions cease to be approved/effective before commit.
- Retain the configured Z.AI model initially. Verify actual tool calling and structured output;
  the current JSON-only urllib adapter is not already agent compatible.
- User-approved active limits (validated live): 5 model requests total, 10 total native
  tool calls per turn including the structured final response (business-action calls may be fewer),
  60 seconds for the entire agent run, and each model call limited to
  min(30 seconds, remaining deadline).
  Schema-repair calls count. Disable hidden SDK retries. Check limits before the next request;
  execute draft-changing tools serially.
- Wave 6 measured an irreducible 11,835-character request after excerpt compaction; the user-approved input cap is now 15,000 characters,
  enforced on every call. Keep the 300-output-token cap. If the integration
  experiment shows they cannot fit tool schemas or valid output, make the smallest measured config
  adjustment and record token/spend/latency effects. Keep the approved 5-call, 60-second,
  USD 15 beta and 30-second response requirements.
- Record model attempts, tool calls, input/output/reasoning usage when reported, fallbacks and
  elapsed time. No private fields, prompts, raw tool arguments or hidden reasoning in logs.
- Measure cost per inbound turn and completed conversation/ticket. Project the existing
  10,000-message beta from messages, not 10,000 model calls. Preserve aggregate spend/traffic caps.

## 3. File boundaries

| File(s) | End state |
| --- | --- |
| app/services/chatbot.py | Thin turn handler; no successful-path fixed intake dialogue |
| app/services/dialogue.py (new) | Agent, prompt, bindings, context assembly and limits |
| app/services/ticket_drafts.py (new) | Typed draft/control models, pure validation and staged operations |
| app/services/answer_generation.py | Model integration, output guards, deterministic fallback/local rendering |
| app/services/tickets.py, ticket_operations.py | Reuse durable ticket/update/audit/notification operations |
| app/services/pii.py, language.py, guardrails.py | Shared extraction, minimization, language and safety behavior |
| app/models.py, new Alembic revision | Add dialogue JSON/revision; migrate existing drafts |
| app/services/inbound.py, app/routers/chat.py, webhooks_meta.py | Transport with updated state/evidence/attempt semantics |
| app/config.py, .env.example, requirements files | Necessary integration and bounded execution settings |
| infra/production/deploy.sh, .github/workflows/release.yml if needed | Tested quiesce/migrate/start sequence and immutable staging-to-production promotion |
| Existing tests, evaluation scripts and docs | Adapt assertions; evaluate new owner; remove stale descriptions |

Prefer these files over a new package hierarchy. A small additional test file is acceptable.
Do not create interfaces/base classes beyond framework requirements.

## 4. Execute in bounded waves

Run waves in order. Start each context window with AGENTS.md, sections 1–3, the ledger below,
and only the current wave's listed files. Do not reload every historical evaluation or the entire
repository on every resume. Each wave must end with a runnable state for its checks; intermediate
waves need not be deployable.

Use one wave per context window. If continuing automatically with available context, still finish
the checkpoint first. Do not request permission at each wave boundary. If context runs short
mid-wave, record the exact incomplete step; never label the wave complete.

At each wave boundary:

1. Run focused checks and git diff --check. Inspect failures; do not hide them with new skips.
2. Update the ledger: status, changed files, commands/results, measured decisions, blocker and exact
   next step. Keep the latest checkpoint under 25 lines.
3. Record environment prerequisites without secrets; link reports rather than pasting logs.
4. Preserve unrelated changes. Do not auto-commit/push unless requested in the execution session.
   Record the actual starting revision, which may differ from this plan's reference revision.

### Wave 1 — Baseline and provider compatibility

**Read:** requirements.in, requirements.txt header, pyproject.toml, Dockerfile, app/config.py,
answer_generation.py, chatbot.py, test/evaluation entry points, current requirements/release docs,
and official LangChain APIs below.

**Steps:**

1. Record branch, revision and working-tree status. Find Python 3.11 or use Docker; do not change
   the system Python. Run the existing full local suite once and record actual failures/skips.
2. Identify implementation-specific state assertions before changing the state machine. Preserve
   behavior outcomes as the baseline rather than promising unchanged private helper names.
3. Pin a compatible stable LangChain release with create_agent. Prefer an official Z.AI integration
   if it supports the configured model/features; otherwise use langchain-openai with the configured
   Z.AI endpoint if the protocol check passes. Do not silently switch model/provider.
4. Regenerate hash-locked requirements under Python 3.11 using the repository's pip-compile workflow.
   Preserve unrelated direct pins; inspect transitive changes. Keep lock tooling out of runtime.
   Check Alpine and intended ARM64 build/install compatibility. Report a concrete packaging blocker
   instead of changing the platform speculatively.
5. Add a small runnable fake-chat-model check: one tool call then structured final output, invalid
   response, timeout and attempt accounting. Use framework APIs; no custom model hierarchy if a
   supported integration works.
6. With provider access and session authorization, run the same bounded synthetic scenario against
   the configured model. Record model identity, tool/schema support, tokens and limitations. If
   unavailable, mark live compatibility pending and continue offline waves.

**Checks:** hash-locked install, pip check, baseline suite, fake integration test, runtime packaging.
Do not switch application dialogue yet.

**Exit:** exact dependency/provider choices recorded; baseline known; standard agent runs with a
fake model. Live support must be demonstrated before release, not inferred from mocks.

### Wave 2 — Extract draft rules and preserve behavior

**Read:** chatbot.py, tickets.py, ticket_operations.py, schemas.py, pii.py,
tests/test_ticket_intake.py, test_smart_conversation.py and test_smart_integrity.py.

**Steps:**

1. Use rg to find every caller of extraction/control/ticket/case helpers. Extract shared business
   checks into ticket_drafts.py; reuse existing email/phone validation.
2. Implement section 2.3 models and section 2.5 pure draft operations. Return working copies and
   validation results without SQL commits or fixed next-field selection.
3. Separate private values from model-safe field references. Prove customer provenance; reject
   invented identities/values and ambiguous candidates.
4. Bind consent, completion, review and case confirmation to prompt/version evidence. Implement all
   section 2.4 behavior, especially Done/No completion versus consent withdrawal/correction review.
5. Move exact control/review/receipt text and necessary fallback helpers into the response module.
   Agent and fallback must call the same business checks.
6. Temporarily adapt the old handler to extracted functions as needed to keep comparison tests
   runnable. Do not create a new normal-path rules router.

**Checks:** focused ticket/SMART tests; pure-rule tests for forged consent, changed versions, missing
fields, invalid references, bounds, ambiguous controls, owned cases and idempotent staging.

**Exit:** rules are testable without LLM/SQL commit, baseline dialogue remains available for comparison,
and no duplicated authorization rules exist in separate callers.

### Wave 3 — Persistent state, migration and bounded memory

**Read:** models.py, existing migrations, chatbot load/store helpers, pii.py,
webhooks_meta.py::_queue_media, retention.py, data-lifecycle/media integrity tests.

**Steps:**

1. Add dialogue_data and dialogue_revision through an additive Alembic revision against the actual
   migration head. Do not edit an applied migration.
2. Translate legacy drafts: idle becomes no draft unless an offer/case context exists; paused becomes
   paused; other awaiting_* states become active with a corresponding prompt purpose/field.
   Preserve consent, contacts, issue/details flags, expiry, review, last case, pending case update,
   prompt ID and evidence group. Preserve media-only conversation evidence even with no active draft.
3. Keep old columns as dormant diagnostic snapshots for this release. The new runtime cannot
   read/write them after conversion. Do not add permanent dual writes.
4. Preserve only provable confirmation metadata. When proof is missing, keep fields and require
   renewed confirmation. Preflight unknown/malformed records with IDs/reasons before mutation;
   provide a documented repair path instead of silently dropping/resetting data.
5. Build bounded context from Message rows and a sanitized draft view. Test references at least
   three exchanges back, safe omission, review sanitization and whole-input bounds.
6. Update new-runtime evidence access and revision increments. Extend existing retention/hold handling
   where necessary so new state does not retain private data beyond current rules.

**Checks:** fresh and populated disposable PostgreSQL migration; every legacy state including paused,
review and case confirmation; invalid-record preflight; alembic check; history PII capture/truncation;
evidence ownership; retention and holds.

**Exit:** migration loses no fields/cases/media; new typed storage and bounded history work.
Old writers must be quiesced before production migration; do not deploy intermediate waves.

### Wave 4 — LangChain dialogue and tools

**Read:** new draft/context code, selected model integration, retrieval.py, output validation,
official agent/tool/middleware APIs for the pinned version.

**Steps:**

1. Implement create_agent in dialogue.py with one model, seven tools, structured final output and
   only necessary context/limit middleware.
2. Prompt for approved-source EN/MS/ZH answers, ambiguity clarification, natural corrections,
   interruptions, resumable drafts and appropriate handoff. Supply authoritative draft facts rather
   than duplicating business validation in a large prompt.
3. Implement section 2.5 tool contracts with serial working-copy updates and short reads. Exclude
   arbitrary identity, SQL, external URL and filesystem capabilities.
4. Let the agent propose the next field/question. Validate it and locally render consequential
   prompts with IDs; ensure stored metadata exactly matches the prompt actually sent.
5. Enforce deadlines, model/tool budgets and usage accounting, including schema retries and tool-result
   size. A timeout must not leave background work capable of committing later.
6. Validate factual output against supplied sources and existing rejection checks. Semantic correctness
   remains a separate evaluation; citations/numeric matching do not prove it.
7. Keep retrieval unchanged; demonstrate a follow-up search rewritten using conversation context.

**Checks:** actual create_agent loop with a fake model, not a mock of the whole agent. Cover FAQ,
multi-turn reference, side question/resume, combined/corrected fields, bad schema, invented
source/field/case references, conflicting/repeated tools, limits, timeout and provider-input capture.

**Exit:** complete synthetic agent conversations run with no committed side effects and no hidden
fixed-order intake router.

### Wave 5 — Turn handler, transport and fallback cutover

**Read:** chatbot.py, inbound.py, chat/WhatsApp routers, ticket services, transport/transaction/media
tests, results of waves 2–4.

**Steps:**

1. Replace ChatbotService.handle internals with section 2.6. Preserve API response fields and prompt
   controls. Update every caller, including scripts/tests injecting the old answer_generator or
   using commit=False.
2. Stage operations throughout execution; apply under the final revision/lease lock. Commit ticket,
   audit, notifications, transcript, inbound completion and outbox together.
3. Keep ordering/quoted-reply/timestamp checks. Adapt the attempt marker to one bounded agent run;
   crash recovery cannot start another model loop.
4. Return committed local receipts. Reject stale state or withdrawn source versions without applying
   proposals. Preserve useful customer input for recovery.
5. Implement compact outage handling through shared draft validators, local required-field prompts,
   approved-knowledge fallback and urgent safety response. A fixed fallback collection order is
   allowed; retaining the old successful-path interpreter/router is not.
6. After tests pass, delete obsolete _prepare_turn/_respond/_continue_intake routing and the old
   ConversationResult/provider protocol. Preserve necessary business/security behavior in shared
   functions rather than keeping dead copies.
7. Check media, administration and retention state interactions and revision consistency.

**Checks:** real PostgreSQL faults/concurrency: crash before/after commit, lease loss, concurrent
customer turns, duplicate tools/confirmation/events, stale/quoted replies, case ownership,
pending/approved media, outbound retry ordering, API compatibility and outage intake completion.

**Exit:** one dialogue entry point; no model I/O holding locks; failure/data/mutation guarantees
preserved; old production dialogue deleted.

### Wave 6 — Evaluation, budgets and behavioral acceptance

**Read:** scripts/release_eval.py, smart_eval.py, smart_burst.py, existing TSV matrices,
release-validation tests, current launch contract and baseline checkpoint.

**Steps:**

1. Adapt the existing evaluation harness to the agent boundary and typed draft. Preserve scenarios
   and expected outcomes. Replace exact field-order/ordinary wording assertions only where agent
   freedom intentionally changes them; preserve control/receipt/policy wording requirements.
2. Add focused multi-turn cases: reference three or more exchanges back, intake side question,
   topic switch/return, corrected historical contact, ambiguous antecedent, language switch,
   unsupported topic then FAQ, and provider failure after a staged operation.
3. Retain EN/MS/ZH and paired real versus hypothetical/quoted/negated safety/fraud/complaint/
   human-request/prohibited-action cases. False urgency/notifications count as mutations.
4. Count inbound turns, actual model attempts, tools, successful conversations/tickets, fallbacks,
   usage, cost and latency separately. Make --max-cost apply to SMART and aggregate across language
   workers with an in-flight reserve. Existing per-language checks and call-based projections do not
   cover multi-call turns. Reserve conservatively before calls; stop before exceeding the allowed cap.
5. Store new reports as docs/evaluation/langchain-*.json. Preserve historical SMART reports.
   Include revision, dependency/model versions, limits and denominators. Do not transfer historical
   semantic-review scores to new answers.
6. Run deterministic/outage checks, full suite and representative PostgreSQL burst first. Then run
   authorized synthetic hosted evaluation with current verified rates and the allowed spend.
   Missing access means a specific pending gate, not abandoning independent offline work.
7. Measure whole-turn/queue latency and project 10,000 messages from actual usage. Keep model spend
   within USD 15 and response p95 within 30 seconds.
8. Fix demonstrated shared causes and repeat affected checks. Do not automatically add a vector
   store, extra agents, another provider or a larger budget when evaluation fails.

**Acceptance:**

- At least 95% expected disposition and correct grounded relevant answers overall and per launch
  language; at least 95% answerable-paraphrase deflection.
- Every mandatory intake/handoff case passes.
- Zero unauthorized consent, creations, reopenings, urgent upgrades or staff notifications.
- Zero prohibited provider-input values in capture tests; no lost data/duplicate mutation under faults.
- Outage handling remains usable and preserves drafts.
- Semantic quality is reviewed against actual new answers, not source presence or API success.
- Existing applicable release checks remain. Pending live/semantic checks cannot be called a pass.

**Exit:** offline evidence complete; authorized live evidence recorded or specifically pending;
measured cost/latency assessed against unchanged launch limits.

### Wave 6 repository-wide audit — complete

The twelve requested audit items are implemented. Historical reports remain immutable; the earlier
347/348 hosted result is not acceptance evidence for these changes. Fresh outage evidence is
[`langchain-outage-wave6-audit-20260915.json`](docs/evaluation/langchain-outage-wave6-audit-20260915.json):
348/348 dispositions, 30/30 intakes, 18/18 handoffs, 9/9 focused behaviors, no unwanted offers,
no unintended mutations and no provider calls. The fresh full hosted run also passes all automatic
behavior, latency and budget gates: 348/348 dispositions, 30/30 intakes, 18/18 handoffs, 9/9 focused
behaviors, zero unwanted offers/mutations and 22.444-second turn p95. Cze Yik's corrected human
review passes correctness and grounded relevance for all 300 FAQ and nine focused answers,
including all 60 held-out paraphrases. No Wave 6 acceptance items remain pending.

| Item | Implemented change | Validation |
| --- | --- | --- |
| Factual-negative offers | Shared EN/MS/ZH offer/draft guard rejects factual negation while retaining actual incident/human requests. | Domain and native final/tool regressions; fresh outage and full hosted run have zero offers, including hosted `zh-063`. |
| Native final repair | Expected field updates and valid successor prompts are checked inside ToolStrategy repair and again before commit. A consumed field prompt cannot remain active after completion. | Actual four-call repair regression preserves the original staged input and returns a valid details prompt. |
| Exact controls | Exact authorized consent and unambiguous dedicated email/phone are local terminals. Mixed contact corrections retain agent execution; only validated changed existing email/phone are preapplied. | Zero-call/retrieval tests; mixed questions and names remain agent-owned. |
| Tool filtering | Native middleware recomputes available tools after each action; existing tool guards remain. | Same-turn update/review and owned-case flows; serialized schemas including final response: 3,440 characters unfiltered, 1,098 FAQ, 2,176 active intake, 2,599 complete intake, 1,351 case lookup. |
| Retrieval reuse | Terminal controls skip retrieval; exact initial sanitized query seeds per-turn reuse. Mixed corrections retain excerpts and search access. | Initial/changed/context-resolved query and source-ID regressions. |
| Spend accounting | Actual reservations precede dispatch/checkpoint markers; partial usage is retained, unknown usage retains a full-turn reserve, settlement is idempotent. Failed calls remain unknown even when a retry reports tokens; beta projections use conservative cost. | 26 evaluator tests pass. Full hosted cost: USD 0.231998 observed, USD 0.457734 conservatively bounded; five unknown-usage events retain reserves. |
| Durable evaluation | Writability preflight, atomic fsync/replace, per-turn checkpoints, case selection and manifests. Resume verifies code, settings, matrix and corpus fingerprints. | Completed cases replay without cost duplication; subsets cannot pass full coverage. Uncertain interrupted cases fail closed. |
| Migration cutover | Pull/validate before stopping writers, drain leases/transactions, fresh backup, preflight/migrate/check, start only new writers. Consent conversion requires evidence from the current draft boundary. | Cutover success/failure shell tests; populated PostgreSQL migration preserves corrected contacts, evidence and queued submission. |
| Dispatch fence | Persist agent attempt immediately before first dispatch. Known pre-dispatch deadline expiry clears only the same owned claim's marker. | Pre-dispatch crash remains eligible; post-dispatch retry uses fallback; lease/deadline tests. The unavoidable commit/network crash gap remains conservative. |
| Remote-operation recovery | Outbound attempts become durable uncertain records before network I/O. Signed Meta status correlation reconciles acceptance; unknown sends stay uncertain. SMTP uses stable Message-ID; media uses conditional object creation. | Crash/replay, callback ordering/ownership, ambiguous rejection and S3 version tests; manual audited SMTP reconciliation documented. |
| Idle polling | Probe for ready inbox work before creating a thread pool. | Idle check: one query, no threads. PostgreSQL eight-sender/four-worker burst: queue p95 0.079 seconds, zero duplicates. |
| Active documentation | README, security, implementation, validation and platform docs describe current tools, limits, cutover and recovery; historical results remain dated. | Active limits: 15,000 input characters, 300 output tokens, five provider requests, ten native tools including final response, 30s/call, 60s/run, 90s lease. |

Schema evidence: [`langchain-tool-schema-wave6-audit-20260915.json`](docs/evaluation/langchain-tool-schema-wave6-audit-20260915.json).
Queue evidence: [`langchain-burst-wave6-audit-20260915.json`](docs/evaluation/langchain-burst-wave6-audit-20260915.json).
No speedup is inferred from schema size alone. The working create_agent/ToolStrategy/ToolRuntime path,
opaque references, consent/ownership checks and atomic business commits remain the architecture.

Final hosted evidence: [accounted report](docs/evaluation/langchain-live-wave6-audit-accounted-20260915.json).
The [original report](docs/evaluation/langchain-live-wave6-audit-20260915.json) and durable checkpoint
remain unchanged. The [accounting provenance](docs/evaluation/langchain-live-wave6-audit-accounting-20260915.json)
records the five added full-turn reserves and source hashes; no provider calls or answer changes
were made during reconciliation. The conservative projection is USD 8.101 per 10,000 inbound messages.

#### Wave 6 closure — 15 September 2026

The authoritative corrected [human submission](docs/evaluation/langchain-wave6-human-review-submitted-20260915.json)
matches all 309 evaluated question/answer pairs exactly. Submission SHA-256:
`54207fbe3ad5c5559e910bd72e07272d9c4a8b38823cc997f597f03404078856`.
It was imported into the [reviewed report](docs/evaluation/langchain-live-wave6-reviewed-20260915.json)
without provider calls or changes to answers, measured usage, cost or latency. Reviewed-report SHA-256:
`507040394710097cb971c663520ef9e2d837dc9b6204d633cdb6f82ac6a4fe89`.

| Semantic gate | Final result |
| --- | --- |
| FAQ correctness and grounded relevance | PASS: 300/300 for each score; EN/MS/ZH each 100/100 |
| Focused dialogue correctness and grounded relevance | PASS: 9/9 for each score |
| Held-out paraphrases | PASS: 60/60; EN/MS/ZH each 20/20 |
| Complete human review | PASS: all 309 answers have both literal Boolean scores |
| Combined Wave 6 acceptance | PASS: evaluator `rollout_ready=true`; all automatic and semantic gates pass |

Wave 6 was closed at the user-requested session boundary. The user separately resumed Wave 7
on 15 September 2026; current release progress is recorded in the ledger below.

Verified LangChain references: [unit testing](https://docs.langchain.com/oss/python/langchain/test/unit-testing),
[structured output](https://docs.langchain.com/oss/python/langchain/structured-output),
[selecting tools](https://docs.langchain.com/oss/python/langchain/context-engineering#selecting-tools),
[testing](https://docs.langchain.com/oss/python/langchain/test), and
[custom middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom).

### Wave 7 — Cleanup and release preparation

**Read:** final diffs, README/requirements, docs/smart-implementation.md, release/platform/privacy
docs, infra/production/deploy.sh, compose.yml, .github/workflows/release.yml, CI configuration
and ledger. Do not repeat unchanged paid evaluations without cause.

**Steps:**

1. Use rg over app/scripts/tests/docs for awaiting_* routing, intake_data writes, old provider
   protocols, one-call assumptions and no-tool claims. Remove runtime legacy-state references;
   migration fixtures and historical evidence may retain them.
2. Remove temporary compatibility paths and unused direct dependencies. Keep dormant legacy columns
   for this release; later removal is out of scope. No engine-selection flag. Keep existing
   LLM/context switches and deterministic outage behavior.
3. Update README, requirements, implementation/data-flow and release docs to describe actual tools,
   state, limits and commands. Fix documentation drift without rewriting historical evidence.
4. Implement and document the release sequence in the existing deployment script. At the reference
   revision it runs migrations before replacing running containers; that is insufficient for this
   state conversion. Validate configuration and pull images before downtime. Quiesce/drain old API
   and combined-worker writers (including media/retention), verify no active writer/claim remains,
   take a fresh recoverable backup, run preflight/apply the tested migration, then start only new
   writers. Verify readiness and draft/case/media integrity. Keep PostgreSQL/data volumes intact.
   Preserve queued events; webhook arrivals during maintenance must receive a retryable failure
   rather than a successful acknowledgment for an unpersisted event. Prevent mixed old/new writers.
5. Document rollback: after new-format turns commit, an old image cannot safely use dormant legacy
   snapshots. Use the new runtime with LLM disabled or a compatible forward fix. Old-image rollback
   requires no new writes or a separately tested reverse conversion preserving all new records.
   Never restore an old database snapshot over new customer records.
6. Run required final tests, dependency integrity, Docker build and schema drift checks. Assess
   security/dependency CI effects from the added framework and record actual results. Add one focused
   runnable deployment check proving stop/backup/preflight/migration/start ordering and failure
   handling. A failed migration must not restart an incompatible old writer or erase the backup.
7. Mark local implementation complete only when true. Record pending live/release gates, then
   continue to wave 8. Local completion is not completion of the production deployment scope.

**Exit:** one production dialogue implementation, reproducible dependencies, complete local checks,
accurate docs and a tested migration/deployment/recovery procedure ready for wave 8.

### Wave 8 — Publish, validate in staging and deploy to production

**Read:** wave 6 evidence and wave 7 checkpoint, .github/workflows/release.yml, ci.yml, security.yml,
infra/production/deploy.sh and compose.yml, docs/production-platform.md, docs/release-validation.md,
docs/launch-contract.md. Discover current GitHub/AWS state read-only before making changes; historical
SHAs, image digests, secret versions, approvals and deployment results are not this release's evidence.

**Entry:** waves 1–7 local work complete. Before production dispatch, actual provider compatibility,
new hosted/semantic evaluation, staging and candidate CI/security gates must pass; GitHub/AWS access
and applicable platform approvals must be available. Steps below establish those release gates.
Do not reuse the previous SMART release's one-off staging waiver or expired exceptions. If a gate
is pending, finish available preparation and record the specific requirement.

**Steps:**

1. Commit the focused refactor and push its branch. Open/update the release PR into dev, the integration
   branch used by the recorded release, following current repository protections. Include behavior,
   migration/recovery and verification results. Resolve actual CI/security failures and merge through
   the normal permitted workflow. Do not force-push protected branches or bypass required approvals.
2. Record the full 40-character merged candidate SHA. Require CI and Security success on the candidate
   actually being released. Keep the release ref fixed during staging; subsequent source changes need
   their own checks and staging. A documentation-only evidence commit is distinct from the image SHA.
3. Inspect AWS account/region and the existing dudu-support-foundation, dudu-support-staging and
   dudu-support-production stacks. Resolve instances and ECR from stack outputs, not copied IDs.
   Keep ap-southeast-5 and existing infrastructure/budget limits. Record current production image pair,
   migration head and relevant feature-switch values without printing runtime secrets.
4. Dispatch .github/workflows/release.yml with environment=staging on the ref for that exact candidate.
   The current staging workflow builds its checked-out GITHUB_SHA, not the release_sha input; verify
   the run's actual SHA matches the candidate. It builds/publishes ARM64 images and resolves immutable
   app and ClamAV digests. Wait for workflow and SSM completion; record run ID, SHA and both digests.
5. In isolated staging, exercise the populated legacy-to-new migration, pending/paused/review drafts,
   evidence ownership, inbox recovery, duplicate events, model outage and the new fallback recovery
   procedure. Check database, ClamAV, storage and worker health. Complete required end-to-end channel
   checks with authorized test recipients/windows; do not send to arbitrary customers or staff.
   If a fix is necessary, return to the affected wave and stage the new candidate before promotion.
6. Schedule production within the documented 02:00–04:00 Asia/Kuala_Lumpur maintenance window unless
   an existing session instruction authorizes a different window. Confirm recoverable backup capacity,
   completed staging results and applicable budget/contract dates. Do not extend the 10–14 September
   beta or expired waivers automatically. A dark deployment may still proceed if permitted; customer
   activation must remain within the applicable contract.
7. Record the predeployment META_SEND_ENABLED, NOTIFICATION_SEND_ENABLED, LLM_ENABLED and
   LLM_CUSTOMER_CONTEXT_ENABLED values. The deployment script requires META_SEND_ENABLED=false.
   Temporarily disable sending when necessary using the established secret/configuration path; avoid
   unrelated secret edits. Keep previously disabled switches disabled. Confirm the wave 7 deployment
   procedure will quiesce writers, back up and preflight after image/configuration validation;
   the production dispatch in step 8 executes that procedure once.
8. Dispatch the same release workflow with environment=production and release_sha set to the exact
   staging-tested 40-character candidate. Use the tested workflow ref. Promote the identical app and
   ClamAV digest pair; do not rebuild for production. Monitor the workflow/SSM result through completion.
   Do not report deployment from a workflow dispatch acknowledgment alone.
9. Verify the public https://support.duducaradmin.com/ready response, running API/worker image digests,
   applied migration head, container health and new schema. Confirm converted draft/case/media counts,
   absence of mixed-version writers, and inbox/outbox recovery. Use minimized aggregate evidence;
   do not copy customer records into release documentation. Exercise a bounded authorized smoke test
   proving the running release invokes LangChain and preserves ticket/confirmation behavior.
10. Restore only pre-existing authorized traffic/notification settings after checks pass, or apply a
    newer explicit session instruction. Keep LLM/context settings consistent with the approved model
    evaluation and provider-data boundary. The last recorded production state had Meta sending off;
    deployment success must not silently enable it. Record deployed and customer-sending status
    separately. Previously disabled customer sending does not prevent completing a dark deployment.
11. Observe production for 60 minutes after the final intended configuration. Monitor public readiness,
    alarms/resources, provider/tool errors, queue age, duplicates/dead letters, mutation errors,
    latency, traffic and spend using existing monitoring. Preserve the launch-contract thresholds.
    Immediately stop outbound for security/data-loss/unauthorized-action triggers; on other threshold
    breaches, use the tested fallback/forward-fix procedure. After new-state writes, do not blindly
    run /opt/dudu/rollback-images with an old incompatible application. Observation with no customer
    traffic is deployment-health evidence, not a claim of live customer-service quality.
12. Record the actual source SHA, workflow/SSM IDs, immutable digests, migration, staging results,
    backup reference, production checks, restored switch states and observation start/end/outcome in
    docs/release-validation.md. Update the SMART ledger and commit/push the release evidence through
    normal repository policy without rebuilding the already validated artifact. Report the exact
    deployed version and whether customer sending is enabled. If a protection gate or credential is
    missing, report its concrete requirement; never label an attempted deployment successful.

**Checks:** required candidate CI/security, staging migration and recovery, identical digest promotion,
successful production workflow/SSM, public/internal readiness, correct running code/schema, intact
customer state and durable queues, and a completed 60-minute observation without unresolved triggers.

**Exit:** the LangChain refactor is running in production on the tested immutable images, its migration
and operational checks pass, intended authorized settings are in place, observation is recorded,
and release evidence is published. No additional deployment permission question is needed solely
because this wave reaches production; honor actual platform gates and unresolved material decisions.

## 5. Commands and references

Run inside Python 3.11. If the host lacks python, use the repository container workflow;
do not assume historical virtualenvs exist.

~~~bash
python -m pip install --require-hashes -r requirements.txt
python -m pip check
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
docker build --target test -t dudu-support:langchain-check .
git diff --check
~~~

During waves 1–7, migrations and alembic check run only against disposable databases.
Set DATABASE_URL and TEST_POSTGRES_URL to disposable test databases using the existing setup;
the latter enables real concurrency checks. Never use staging/production or a valuable personal
database for tests. Reuse PostgreSQL/pg_trgm prerequisites documented in README. Wave 8 explicitly
applies the tested release migration to staging and production through the deployment workflow;
never point fixture-driven/destructive tests at production.

~~~bash
alembic upgrade head
alembic check
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_smart_integrity.py
python scripts/smart_burst.py
python scripts/release_eval.py --suite smart --mode outage
~~~

Adapt the live CLI in wave 6; pass verified rates and a session-authorized spend cap.
Do not paste historical rates or API keys into commands.

Confirm APIs for the dependency version pinned in wave 1:

- [Agents](https://docs.langchain.com/oss/python/langchain/agents)
- [Tools/runtime context](https://docs.langchain.com/oss/python/langchain/tools)
- [Short-term memory](https://docs.langchain.com/oss/python/langchain/short-term-memory)
- [Middleware](https://docs.langchain.com/oss/python/langchain/middleware/overview)
- [Structured output](https://docs.langchain.com/oss/python/langchain/structured-output)

Do not redesign fixed decisions in later waves. Resolve version-specific syntax through official
docs and tests. If evidence makes a requirement impossible, record the concrete failing check
and smallest proposed change; continue independent work and surface only the decision needing
the owner. Do not invent working provider behavior or migrate unsafe data.

## 6. Progress ledger — update at every wave boundary

| Wave | Status | Evidence / next action |
| --- | --- | --- |
| 1. Baseline/provider | Complete | LangChain/Z.AI agent compatibility proven offline and live; see checkpoint |
| 2. Domain rules | Complete | Typed pure draft/control/case rules extracted; baseline preserved |
| 3. State/memory | Complete | Typed storage, migration, bounded context and lifecycle checks pass |
| 4. Agent/tools | Complete | Bounded create_agent loop and seven turn-local tools pass synthetic conversations |
| 5. Handler cutover | Complete | Staged execution, atomic commit, transport recovery and typed fallback pass |
| 6. Evaluation | Complete | Automatic acceptance passes; corrected user review passes all 300 FAQ and nine focused answers, including 60 held-out paraphrases. Reviewed report has `rollout_ready=true`. |
| 7. Release preparation | Complete | One dialogue owner; tested cutover, recovery and staging-success gates; 554 PostgreSQL tests pass. [Local evidence](docs/evaluation/langchain-wave7-local-20260915.json). |
| 8. Production deployment | Preparation in progress | Publish the candidate, pass exact-SHA CI/Security, recreate and validate isolated staging, then deploy dark within the maintenance window and observe. |

### Latest checkpoint

- Waves 1–7 are complete. Wave 8 preparation is in progress; no LangChain candidate has been published, staged or deployed yet.
- Original branch `smart-release-20260911`, starting revision `52c6247571d460c5f8d0a3b3d5eb47ce1fed33ed`; prior uncommitted work and the local AGENTS.md deletion remain preserved.
- Publication uses isolated branch `langchain-release-20260915` from current `origin/dev` (`7f8f13f74161aa6ba9064e558ad00ae9a6486ead`) at `/tmp/dudu-langchain-release-20260915`.
- Wave 6 [reviewed acceptance](docs/evaluation/langchain-live-wave6-reviewed-20260915.json) remains authoritative: 348/348 dispositions, 30/30 intakes, 18/18 handoffs, 9/9 focused; all 309 answers and 60 held-out paraphrases pass semantic review; `rollout_ready=true`.
- No hosted model calls were made in Wave 7. Conservative tracked spend remains approximately USD 4.27; full-run conservative projection remains USD 8.101 per 10,000 messages, with p95 22.444 seconds.
- Active limits remain input 15,000/output 300, five model requests, ten native tools including final response, 30s/call, 60s/run and 90s lease.
- Runtime cleanup found one dialogue owner and no obsolete adapter or unused direct dependency. Dormant legacy columns are only cleared for privacy retention; the regression now includes old phone/account/trip values.
- Cutover validates candidate settings before downtime, stops/drains all writers, preserves backup references on retry failure, compares all table counts and validates typed dialogue before retention/start. Startup waits for API, ClamAV and a complete worker cycle.
- Production workflow requires exact-SHA successful staging and an actually immutable ECR repository. Apply the foundation role's new repository-scoped `ecr:DescribeRepositories` permission first.
- Final Python 3.11/PostgreSQL suite: **554 passed, 2 skipped**. Fresh migration to `e5c1a2b3d4f6`, `alembic check`, hash install, dependency integrity and shell checks pass. Final workflow gate tests: **9 passed**.
- Docker AMD64 and ARM64 test targets each pass **545 tests, 8 skipped**; the three later ECR cases pass in the final PostgreSQL suite. ARM64 ClamAV build/version checks pass.
- Local security passes: pip-audit has zero advisories, Bandit high/high has zero findings, Gitleaks history has zero leaks, Trivy source/container has zero high/critical findings, ZAP has zero high-risk alerts.
- Cze Yik renewed AWS-0104/AWS-0136 through 17 September 2026 for staging/dark deployment only, with customer sending disabled. Trivy uses the conservative `exp:2026-09-17` cutoff. The public beta and notification waiver are not extended.
- Runtime verification image: `dudu-support:wave7-runtime`, index `sha256:e4c5e58a4a56b94bcac9402ec7c7bbc0d3aabea9ab3a48b7f241d87df7e6ea9f`. [Wave 7 evidence and source hashes](docs/evaluation/langchain-wave7-local-20260915.json).
- AWS authentication works via profile `dudu-production` in account `173454940059`, region `ap-southeast-5`. Foundation and production exist; staging is absent. GitHub access works; `dev` requires successful `test` and an up-to-date branch, with no review requirement.
- Current production secret reports Meta sending true, notifications false, LLM true and customer context true; runtime container values remain to be checked. Preparation has not changed production. Staging/dark deployment must disable sending.
- Next: copy the focused candidate and referenced evidence into the publication worktree, commit/push and open its PR; complete candidate CI/Security, isolated staging and all Wave 8 gates. Keep the 02:00–04:00 Asia/Kuala_Lumpur production window.

### Remaining work

Wave 7 local implementation is complete. Continue Wave 8 from the checkpoint above. Preserve the
accepted Wave 6 evidence and do not repeat unchanged paid evaluations or human review. Source
publication, staging validation, production deployment and observation remain required.
