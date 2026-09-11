# SMART implementation and release evidence

The revised conversation implementation is local and **not activated in production**.
`LLM_CUSTOMER_CONTEXT_ENABLED` defaults to `false`. The existing model setting alone does not
permit transmitting customer questions under the revised processing scope.

## Conversation and intake

The responder makes one structured interpretation request per eligible turn. Immediate safety,
standalone identity and standalone language commands retain local handling. Local code validates
contact fields, consent, submission, case ownership and priorities. A ticket offer leaves intake
idle; acceptance opens the separate consent question. Pause/cancel/resume proposals require matching
local customer intent. Explicit local handoff requests and Yes/No/Done/Skip/Submit controls
take precedence over model action proposals. Model proposals cannot override ordinary contact fields.
Side questions preserve the draft. Explicit
case updates require confirmation against the owned case before changing or reopening it.

API clients should return the latest response's `prompt_id` when submitting `create_ticket` or
`consent_to_ticket`. Missing or stale control tokens cannot authorize those actions. Ordinary text
replies continue to work. WhatsApp additionally checks quoted reply IDs and event timestamps.

Names, email and contact numbers can be supplied in any order. Corrections update local fields;
ambiguous email choices require clarification. Human requests collect the actual issue. Trip IDs
do not imply submission. Accumulated ride details cannot exceed 2,000 characters; an oversized
addition is rejected with a recovery prompt while earlier details remain. Local review precedes
submission when fields have been corrected. Draft expiry defaults to 60 minutes and is configurable
with `INTAKE_EXPIRY_MINUTES` (5–1,440 minutes).

## Provider boundary

Provider input contains approved excerpts, a minimized question of at most 1,600 characters,
a topic of at most 100 characters, a previous sanitized question of at most 400 characters,
a previous sanitized answer of at most 600 characters, up to four source keys, intake stage,
pending-offer status and field-presence/role indicators. Prior answers are never policy.
Raw local contact fields, account identifiers, ticket bodies, attachments and full history are
excluded. Local extraction runs before provider minimization. Uncertain identifying narratives
and oversized questions use local clarification; regex minimization is not a guarantee of anonymity
for arbitrary prose.

The total prompt remains capped at 8,000 characters. Ranked excerpts are removed whole when needed
to fit, and citations are validated only against excerpts actually sent. The output cap remains
300 tokens and timeout eight seconds. Logs expose outcomes, latency and reported token counts,
including reasoning-token details when supplied, without prompts or response bodies.
Citation and numerical checks are rejection guards, not proof of semantic entailment. Semantic
correctness and faithful translation require the separate reviewed evaluation denominator.

## Knowledge governance

Retrieval ranks the complete effective approved corpus up to 500 chunks, including titles and
multiword tags, and can retrieve across source languages. More than 500 eligible chunks requires
clarification until SQL ranking is introduced; it does not silently rank an arbitrary prefix.
Website and seed sources have equal authority within their stated scope. Unresolved conflicts
require clarification, not an inferred override.

`WEBSITE_KNOWLEDGE_URLS` is an explicit JSON list of exact CCO-selected HTTPS URLs. It defaults to
empty; the 18 approved production URLs and shared effective date are recorded in the release
validation record. Staging no longer crawls the sitemap automatically. Redirects stay within the
configured allowlist; nested lists, inline text, table conditions and links are retained. The
current `/about-us` page governs human customer-service hours, `/dudu-later` governs its own
service-specific claims, and `/car-types` is included for vehicle and fare information. Oversized
source blocks require review rather than silent splitting or truncation.

The CCO activation endpoint accepts `{"effective_at": "2026-09-10T00:00:00Z"}`. Activation remains
authenticated and audited. Content hashes exclude the approval schedule, so activating an unchanged
snapshot does not make the next crawl produce a duplicate draft. Changed content remains inactive
until approved. No website pages were published or assigned an invented approver.

## Durability and evidence ownership

Migration `d9010a1b2c3d` adds conversation uniqueness, inbound claims and evidence groups. It stops
before changing the schema if duplicate conversation owners exist; reconcile those records without
losing histories or case ownership before retrying. Existing processed inbound rows remain `done`.
Existing unassigned media is not guessed into a case by timestamp.

Webhooks persist events and beta counters before acknowledging. The existing worker processes up to
four senders concurrently, serializes each sender, and records a provider-attempt marker before
network work. A retry after a worker crash uses fallback rather than issuing another model request.
Provider calls run outside database transactions. State version checks discard stale proposals.
The ticket, audit and outbound reply commit together before delivery. Outbound retries preserve
recipient order.

Each upload has an explicit evidence group. Ticket submission binds queued and approved uploads in
that group. Scanning changes availability, never ownership. Confirmed existing-case updates can bind
new evidence to that case. Rejected uploads and other groups remain excluded. Customer case updates
are visible in the staff ticket view.

## Executable evidence

### SMART local validation — 10 September 2026

The verified source is commit `23ab970ee077027aa902b903f169affa136a22ce`. The final-source
PostgreSQL run used `dudu-support:smart-check` and passed **210 tests, 2 skipped**. The refreshed
outage report in `docs/evaluation/smart-outage.json` records **300/300** non-escalation sessions
(100/100 in EN, MS and ZH), **18/18** necessary handoffs, **30/30** cooperative intake sessions,
**60/60** held-out routing cases, zero unexpected offers, and zero unintended mutations. Its 519
provider-path calls all used the local outage fallback; p95 was 0.015 seconds.

The completed hosted-model report in `docs/evaluation/smart-live-verified.json` records **295/300**
automated non-escalation sessions overall: EN **99/100**, MS **97/100**, and ZH **99/100**. It
also records **18/18** necessary handoffs, **30/30** correct intake completions, zero unintended
mutations, and **60/60** held-out routing cases. There were five non-mutating unexpected offers;
the non-escalation family failures were corporate 1, fraud 3 and safety 1. The report measured
531 provider calls / 527 successes, 602,547 input tokens, 67,608 completion tokens and 9,290
reported reasoning tokens at USD **0.124186**; the sample projection is USD **2.339 per 10,000
calls**, with a 3.413-second non-escalation p95.

The automated routing, handoff, intake, held-out, mutation and semantic review gates pass for this
development slice: all 300 records have `answer_correct=true` and `grounded_relevant=true`, with
100/100 in each language. The report now records `cco_review_status=complete`, while
`rollout_ready` remains `false` until the independent release holdout, production deployment and
post-deploy controls are complete. These synthetic results do not constitute production observation
evidence. The direct hosted-model report predates the final equal-timestamp outbox transport
assertion; the final-source PostgreSQL run covers that transport change.

The owner-approved fresh independent matrix is
`data/evaluation/smart-independent-holdout.tsv` (20 scenarios in each of EN/MS/ZH, 60 cases).
Its outage and hosted-model runs both recorded **60/60** non-escalating cases, zero unexpected
offers, and zero unintended mutations. The hosted run used 299 provider calls / 297 successes,
had a 3.265-second p95, and measured USD **0.071649**. The generated answers and sources are in
`docs/evaluation/smart-independent-holdout.json`; the separate CCO review artifact is
`docs/evaluation/smart-independent-holdout-review.json` with 60/60 `answer_correct` and
`grounded_relevant` scores, including 20/20 in each language. Semantic review is complete; staging
WhatsApp validation, website/privacy gates, production readiness, and GO remain required.

- `python -m pytest -q`: application and regression checks. Set `TEST_POSTGRES_URL` to a fresh
  disposable PostgreSQL database for concurrency checks; install `pg_trgm` and run migrations first.
- `python scripts/release_eval.py --suite smart --mode outage --input-price 0.15 --output-price 0.50`:
  100 distinct non-escalation scenarios in English, Malay and Chinese, 18 paired legitimate handoff
  sessions and 30 intake sessions covering ten intake variants.
- The same command with `--mode live` uses synthetic inputs only. Each language has an observed-spend
  guard; latency and usage are recorded. Semantic review fields remain unscored until reviewed.
- Add `--intake-only` to the SMART evaluation command for the 18 handoff and 30 intake diagnostics.
  This does not exercise the non-escalation matrix.
- `python scripts/smart_burst.py`: eight synthetic senders through the PostgreSQL inbox with four
  workers and two-second simulated provider latency. `smart-burst.json` records reply-queue latency,
  which does not include Meta delivery time.

The matrix is in `data/evaluation/smart-non-escalation.tsv`; its 20 held-out scenarios are reported
separately from translations. These development scenarios have been rerun during fixes; the fresh
independent holdout is recorded above with its completed review. Staging WhatsApp end-to-end
validation and the remaining release gates are still required before eligibility. Successful API
calls are not counted as correct answers.

## Pricing and activation

The [official Z.AI pricing table](https://docs.z.ai/guides/overview/pricing), checked on
10 September 2026, lists GLM-5.3-Flash at USD 0.15 per million input tokens and USD 0.50 per million
output tokens (cached input USD 0.03). Evaluation projections use uncached input pricing and
reported aggregate completion usage. Missing reasoning breakdowns are recorded as missing, not
assumed to be zero. These are measured-sample projections, not a hard maximum for the 10,000-call
beta. The existing USD 15 model allowance and beta message caps remain unchanged.

Before activation: approve and publish the revised privacy copy/effective date, confirm provider
DPA coverage, select and review website snapshots, complete the CCO semantic evaluation, and pass
staging WhatsApp, concurrency, outage and production readiness controls using the same immutable
image. Then enable the context flag through the existing controlled release process.

For rollback, first disable `LLM_CUSTOMER_CONTEXT_ENABLED`, preserve the durable inbox and drain
or pause its worker deliberately, then use the existing `/opt/dudu/rollback-images` process.
Do not downgrade the data migration. An older image does not understand the new queued inbox:
check for queued/processing events and ensure they are handled before returning to that worker.
