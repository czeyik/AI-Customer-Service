# SMART implementation and release evidence

## Current LangChain candidate — 15 September 2026

[SMART.md](../SMART.md) records Wave 6 acceptance and deployment status. The LangChain
candidate has not replaced the historical production release described below. Historical
semantic scores and deployment waivers do not apply to this candidate.

## Conversation and intake

One LangChain `create_agent` agent owns ordinary dialogue, clarification, tool choice and
missing-field order. Seven scoped tools search knowledge, update or pause drafts, prepare
review/submission, and look up or update an owned case. They stage changes in memory; Python
validates consent, field references, ownership and priorities, and PostgreSQL commits the
validated operations with conversation, audit, notification and reply rows.

A ticket offer leaves intake idle; accepting it opens a separate consent question. Names,
email and contact numbers may arrive together or in any order. Side questions preserve fields;
corrections invalidate a prior review. Done/Skip paths remain available for complete consented
drafts. Case updates and reopenings need confirmation tied to the owned case.

API clients return the current `prompt_id` for `create_ticket` or `consent_to_ticket` controls.
WhatsApp additionally validates quoted reply IDs, event timestamps, claim ownership and leases.
Accumulated ride details are limited to 2,000 characters. Draft expiry defaults to 60 minutes
and is configurable with `INTAKE_EXPIRY_MINUTES` (5–1,440 minutes).

## Provider boundary

Input includes a minimized current question, up to 12 sanitized recent messages, bounded
state/field-presence metadata, opaque references and approved excerpts. Raw contacts, reviews,
case bodies and attachment contents remain local. Previous bot answers are never policy.

The agent permits five actual model requests and ten native tool calls, including structured
final output, within 60 seconds. Each call has a maximum 30-second timeout, 15,000 input
characters (including tools/history/results) and 300 output tokens. SDK retries are disabled.
Whole excerpts and older history are removed to fit. Citations must identify sources actually
supplied and still approved at commit. Local recovery preserves drafts on provider failure.

Logs contain attempt/tool counts, usage, outcome and timing without prompts, raw tool values
or hidden reasoning. Correctness and faithful translation use a separate human review of the
actual new answers.

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

The CCO activation endpoint accepts `{"effective_at": "2026-09-11T23:00:00+08:00"}`. Activation remains
authenticated and audited. Content hashes exclude the approval schedule, so activating an unchanged
snapshot does not make the next crawl produce a duplicate draft. Changed content remains inactive
until approved. As recorded on 12 September 2026, production contained 18 active version-1 website documents
and no remaining website drafts. All are approved under `jane` with effective instant
`2026-09-11T23:00:00+08:00` and audited publication events.

## Durability and evidence ownership

Migration `e5c1a2b3d4f6` adds typed dialogue JSON and revisions and preflights all legacy drafts
before conversion. Imported consent must belong to the current draft’s start window; missing
or older evidence preserves the fields and requires fresh consent. The earlier `d9010a1b2c3d`
added conversation uniqueness, inbound claims and evidence groups. It stops
before changing the schema if duplicate conversation owners exist; reconcile those records without
losing histories or case ownership before retrying. Existing processed inbound rows remain `done`.
Existing unassigned media is not guessed into a case by timestamp. Dormant legacy columns are
never read for dialogue; retention still clears them so old private snapshots expire with the chats.

Webhooks persist events and beta counters before acknowledging. The existing worker processes up to
four senders concurrently, serializes each sender, and records a provider-attempt marker before
the first model dispatch. A crash before that fence leaves the retry eligible; after the durable
dispatch marker, recovery uses local fallback. The marker and network request are not atomic;
a crash between them conservatively uses fallback.
Provider calls run outside database transactions. Ninety-second leases cover the 60-second
agent window and commit/recovery. State revision checks discard stale proposals.
The ticket, audit and outbound reply commit together before delivery. Outbound retries preserve
recipient order.

Each upload has an explicit evidence group. Ticket submission binds queued and approved uploads in
that group. Scanning changes availability, never ownership. Confirmed existing-case updates can bind
new evidence to that case. Rejected uploads and other groups remain excluded. Customer case updates
are visible in the staff ticket view.

## Executable evidence

### SMART local validation — 10 September 2026

The verified source is commit `3e6b81376cc36fedaae56d296bc59df2a07c2a6b`. The final-source
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
`rollout_ready` remains `false` until post-enable observation and the final GO record are complete. These synthetic results do not constitute production observation
evidence. The direct hosted-model report predates the final equal-timestamp outbox transport
assertion; the final-source PostgreSQL run covers that transport change.

The owner-approved fresh independent matrix is
`data/evaluation/smart-independent-holdout.tsv` (20 scenarios in each of EN/MS/ZH, 60 cases).
Its outage and hosted-model runs both recorded **60/60** non-escalating cases, zero unexpected
offers, and zero unintended mutations. The hosted run used 299 provider calls / 297 successes,
had a 3.265-second p95, and measured USD **0.071649**. The generated answers and sources are in
`docs/evaluation/smart-independent-holdout.json`; the separate CCO review artifact is
`docs/evaluation/smart-independent-holdout-review.json` with 60/60 `answer_correct` and
`grounded_relevant` scores, including 20/20 in each language. Semantic and corpus review are complete;
the owner-authorized staging bypass was used for direct production deployment, privacy publication is
deferred by owner instruction, and context enablement is complete. Observation and live GO remain
required.

- `python -m pytest -q`: application and regression checks. Set `TEST_POSTGRES_URL` to a fresh
  disposable PostgreSQL database for concurrency checks; install `pg_trgm` and run migrations first.
- `python scripts/release_eval.py --suite smart --mode outage --input-price 0.15 --output-price 0.50`:
  100 distinct non-escalation scenarios in English, Malay and Chinese, 18 paired legitimate handoff
  sessions and 30 intake sessions covering ten intake variants.
- The same command with `--mode live` uses synthetic inputs only. Concurrent language workers share an in-flight worst-case spend reserve; latency and usage are recorded. Semantic review fields remain unscored until reviewed.
- Add `--intake-only` to the SMART evaluation command for the 18 handoff and 30 intake diagnostics.
  This does not exercise the non-escalation matrix.
- `python scripts/smart_burst.py`: eight synthetic senders through the PostgreSQL inbox with four
  workers in deterministic outage mode. `smart-burst.json` records reply-queue latency,
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
assumed to be zero. These are measured-sample projections, not a hard maximum for the 10,000-message
beta. Current LangChain projections count inbound messages, including multi-call turns. The existing USD 15 model allowance and beta message caps remain unchanged.

The 18 website snapshots are active under the approved effective date. The owner explicitly deferred
privacy-copy publication and authorized context enablement; the production API and worker now carry
`LLM_CUSTOMER_CONTEXT_ENABLED=true` while `META_SEND_ENABLED=false`. Record the post-enable
observation and final GO before restoring outbound Meta settings.

For the LangChain candidate, use the new runtime with `LLM_ENABLED=false` or a compatible forward
fix after new-format turns have committed. Preserve the durable inbox. Dormant legacy snapshots
cannot recover new dialogue changes, so `/opt/dudu/rollback-images` is safe only before new writes
or after a separately tested reverse conversion. Never downgrade destructively or restore an old
snapshot over new customer records. Follow the [deployment and recovery runbook](production-platform.md).
