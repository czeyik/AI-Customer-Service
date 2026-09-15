# Release Validation and Public-Beta Activation

Status: **LangChain Waves 1–6 complete; Wave 7 complete; Wave 8 publication/staging preparation in progress — see [SMART.md](../SMART.md)**
Go/no-go owner: Cze Yik
Support lead and CCO: Jane
Beta: 10–14 September 2026

The approved beta release recorded below is historical. It is not the SMART release candidate and
does not prove the current candidate is deployed or approved.

## LangChain Wave 6 candidate — 15 September 2026

All twelve audit fixes are implemented. Cze Yik's corrected
[human submission](evaluation/langchain-wave6-human-review-submitted-20260915.json) matches the
exact final 300 FAQ and nine focused answers and passes both semantic scores for every answer.
The [reviewed report](evaluation/langchain-live-wave6-reviewed-20260915.json) passes all Wave 6
acceptance gates. The user resumed Wave 7 on 15 September 2026; the candidate has not yet been
published, staged or deployed.

| Gate | Current evidence |
| --- | --- |
| Full hosted coverage and dispositions | PASS: 357/357 scenarios; 348/348 expected dispositions, including 300/300 FAQ dispositions |
| Mandatory and focused behavior | PASS: 30/30 intakes, 18/18 handoffs, 9/9 focused dialogues; zero unwanted offers or unintended mutations |
| Hosted reliability and latency | PASS: 733 provider calls, 728 successes, four timeouts; five agent failure fallbacks across 419 executions; turn p95 22.444 seconds |
| Hosted spend | PASS: USD 0.231998 observed, USD 0.457734 conservative under the USD 1.00 run cap; five incomplete-usage events retain full-turn reserves |
| 10,000-message projection | PASS: USD 8.101 using conservative cost and 565 inbound turns, below USD 15 |
| Fresh outage | PASS: 348/348 dispositions, 30/30 intakes, 18/18 handoffs, 9/9 focused; zero unwanted offers, unintended mutations or provider calls |
| PostgreSQL burst | PASS: eight senders / four workers, queue p95 0.079 seconds, zero duplicates |
| Local validation | PASS: Python 3.11/PostgreSQL suite 528 passed, two skipped; final evaluator checks 26 passed; fresh migration, schema drift and dependency integrity checks pass |
| Docker test target | PASS: 529 passed, eight skipped; `dudu-support:wave6-audit-test`, index `sha256:31b3210abe74bd30c327d8454bcf8dae015e8a8d0755407091bcfba7710bb075` |
| Semantic acceptance | PASS: 300/300 FAQ and 9/9 focused answers for correctness and grounded relevance; EN/MS/ZH each 100/100 FAQ, held-out paraphrases 60/60 |
| Combined Wave 6 acceptance | PASS: complete human review of 309 exact answers; evaluator `rollout_ready=true` |

Evidence: [final accounted hosted report](evaluation/langchain-live-wave6-audit-accounted-20260915.json),
[outage report](evaluation/langchain-outage-wave6-audit-20260915.json),
[burst report](evaluation/langchain-burst-wave6-audit-20260915.json).
The [original hosted report](evaluation/langchain-live-wave6-audit-20260915.json) and checkpoint remain
unchanged. [Accounting provenance](evaluation/langchain-live-wave6-audit-accounting-20260915.json)
records source hashes and five reserve corrections without changing answers or making provider calls.
The focused nine-case hosted check cost USD 0.011548; tracked conservative hosted spend is approximately
USD 4.27 including historical reserves. Verified rates and active limits are recorded in SMART.md.
Importing the corrected human review made no provider calls and preserved the evaluated answers,
usage, cost and latency.

## LangChain release preparation — 15 September 2026

Cze Yik renewed the existing AWS-0104 exceptions for outbound TCP 443/465 and AWS-0136 for
AWS-managed SNS encryption through **17 September 2026**, exclusively for staging and dark
deployment with customer sending disabled. This does not extend the public beta or notification
waiver. Trivy uses `exp:2026-09-17`, a conservative date-only cutoff; recheck the gate before release.

Read-only AWS discovery used the `dudu-production` profile in account `173454940059`, region
`ap-southeast-5`. Foundation and production stacks exist; staging must be recreated for this
candidate. The current production secret reports `META_SEND_ENABLED=true`,
`NOTIFICATION_SEND_ENABLED=false`, `LLM_ENABLED=true`, and `LLM_CUSTOMER_CONTEXT_ENABLED=true`.
The runtime container values still require verification. Preparation preserves those settings;
staging and the authorized dark deployment use disabled sending.

Local release checks pass: **554 PostgreSQL tests, two skipped**; fresh migration to
`e5c1a2b3d4f6`, schema drift and dependency integrity pass. AMD64/ARM64 Docker test targets each
pass 545 tests with eight skips; the final nine workflow checks, including three later ECR cases,
pass in the full PostgreSQL run. ARM64 ClamAV build/version checks pass. Dependency, high-confidence
static, source/container and dynamic security gates pass with the current exceptions.

The [Wave 7 report](evaluation/langchain-wave7-local-20260915.json) records checks, source hashes and
the local runtime image. Cutover now validates configuration before downtime, preserves backup
references on failed retries, checks durable row counts and typed state, and waits for a complete
worker cycle. Production promotion requires successful staging for the exact SHA and an immutable
ECR repository. The [runbook](production-platform.md) covers deployment and compatible-runtime recovery.
Candidate publication, CI/Security, staging, production and observation remain pending.

## Historical SMART release — production state recorded 12 September 2026

Candidate source: `3e6b81376cc36fedaae56d296bc59df2a07c2a6b` (merged PR #4 into `dev`)
Local verification image: `dudu-support:smart-check` (`sha256:cc7dce73d738b0eae39c8df616fe71bddd8390da11f74dcbe4f137800ec4a419`)
Migration head: `d9010a1b2c3d`
Release status: **LIVE BEHAVIOR ENABLED / OBSERVATION PENDING** — the owner-authorized staging bypass
was used for a direct production deployment. Jane's explicit approval was applied to all 18 website
snapshots. Customer-context transmission is enabled; Meta sending remains disabled.

| Gate | Result |
| --- | --- |
| Final-source PostgreSQL suite | PASS: 210 passed, 2 skipped |
| Outage routing evaluation | PASS: 300/300 non-escalation; EN/MS/ZH each 100/100 |
| Outage handoff/intake/mutation checks | PASS: 18/18 handoffs, 30/30 intakes, 0 unintended mutations |
| Approved website extraction preflight | PASS: all 18 selected URLs extracted successfully; activation remains audited |
| Production website snapshot staging | PASS: 18/18 version-1 website drafts staged on 12 September 2026 |
| Production website snapshot activation | PASS: 18/18 active under `jane`, effective at `2026-09-11T23:00:00+08:00`; 0 remaining drafts |
| Hosted-model automated routing | PASS: 295/300 overall; EN 99/100, MS 97/100, ZH 99/100 |
| Hosted-model necessary handoffs and intake | PASS: 18/18 and 30/30 |
| Held-out routing cases | PASS: 60/60 development cases and 60/60 fresh independent release cases |
| Hosted-model usage | 531 calls / 527 successes; 602,547 input and 67,608 completion tokens; 9,290 reasoning tokens reported |
| Hosted-model cost and latency | USD 0.124186 measured; USD 2.339 sample projection per 10,000 calls; 3.413-second p95 |
| Consequential false mutations | PASS: 0 |
| Semantic answer correctness / grounded relevance | PASS: 300/300 development records and 60/60 fresh holdout records reviewed for each field; EN/MS/ZH fresh holdout 20/20 each |
| CI and Security on merged source | PASS: both workflows succeeded for `3e6b81376cc36fedaae56d296bc59df2a07c2a6b` |
| Staging end-to-end validation | WAIVED — direct production deployment without staging was explicitly authorized by the owner |
| Production dark deployment | PASS: SSM command `b715a6fd-4c68-4842-8ba8-778e5f3f19df`; `/ready` returned HTTP 200; API and ClamAV healthy |
| Production immutable images | PASS: app `sha256:3d93ab9fe07b2ee69ca6b381b5ea7ec41b17ee823e8c5df3f96cbc6246eb2305`; ClamAV `sha256:de1019ce578968df526f5c55486e74ed3cdb0c88b2ea8a1b0c73e51563b309bc` |
| Privacy-copy publication / context enablement | Owner-directed publication deferral recorded; context enablement explicitly authorized and applied; `LLM_CUSTOMER_CONTEXT_ENABLED=true` |
| CCO snapshot activation | PASS: all 18 snapshots activated and audited under `jane` with the approved effective date |
| Controlled context enablement | PASS: API and worker refreshed from Secrets Manager version `a6f2ea42-b08b-479e-871a-c3c48851f10e`; `META_SEND_ENABLED=false` |
| CCO review and rollout readiness | Semantic and corpus review complete; `rollout_ready=false` until post-enable observation and final GO record finish |

The hosted run had five non-mutating unexpected offers, with family failures corporate 1, fraud 3,
and safety 1; these are included in the reported 295/300 routing result. The outage artifact was
refreshed after the final-source checks. All evaluation results are synthetic/local evidence and
are not staging or production evidence.

Artifacts: [`smart-live-verified.json`](evaluation/smart-live-verified.json),
[`smart-outage.json`](evaluation/smart-outage.json), and [`smart-implementation.md`](smart-implementation.md).

### Fresh independent holdout — 12 September 2026

The owner-approved matrix is [`smart-independent-holdout.tsv`](../data/evaluation/smart-independent-holdout.tsv)
(20 new scenarios in EN/MS/ZH, 60 cases; SHA-256
`909b4b3122d88004039d05518fea7768d7645a40220eda1b2eed2fc3702d4bd7`). The outage report is
[`smart-independent-holdout-outage.json`](evaluation/smart-independent-holdout-outage.json).
The hosted-model report is [`smart-independent-holdout.json`](evaluation/smart-independent-holdout.json)
(SHA-256 `50d0c9e74727480ab759e8ade9b57dbebe44b50d4093f585f289dd679c2ca498`) and was run on the
immutable local image `sha256:cc7dce73d738b0eae39c8df616fe71bddd8390da11f74dcbe4f137800ec4a419`.

Automated results are **60/60 non-escalating** (EN/MS/ZH 20/20 each), zero unexpected offers,
zero unintended mutations, 299 provider calls / 297 successes, 3.265-second p95, and measured
cost USD 0.071649. The completed review is recorded in
[`smart-independent-holdout-review.json`](evaluation/smart-independent-holdout-review.json): all
60 `answer_correct` and `grounded_relevant` fields are true, with 20/20 in each language.

Approved website allowlist supplied by the owner on 11 September 2026:

```json
[
  "https://duducar.co/about-us",
  "https://duducar.co/sustainability",
  "https://duducar.co/drivers",
  "https://duducar.co/users",
  "https://duducar.co/car-types",
  "https://duducar.co/dudu-later",
  "https://duducar.co/dudu-now",
  "https://duducar.co/airport-transfer",
  "https://duducar.co/security-policy",
  "https://duducar.co/payment-and-penalty-policy",
  "https://duducar.co/booking-policy",
  "https://duducar.co/chargeback-and-dispute-handling-policy",
  "https://duducar.co/driver-code-of-conduct",
  "https://duducar.co/terms-of-service",
  "https://duducar.co/partnership",
  "https://duducar.co/policy",
  "https://duducar.co/services",
  "https://duducar.co/privacy-notice"
]
```

The list is recorded exactly as supplied. All 18 URLs have the approved effective date
`2026-09-11T23:00:00+08:00`. The current `/about-us` page governs human customer-service hours;
service-specific claims from `/dudu-later` are followed, and `/car-types` is included as an
approved source for vehicle and fare information. Conflicting facts must still be resolved by
source scope during snapshot review; the assistant will not silently combine them.

### Current production state — 12 September 2026

The owner-authorized direct deployment completed through Systems Manager on the merged source above.
The production release directory is pinned to the immutable app/ClamAV pair recorded in the gate table;
`https://support.duducaradmin.com/ready` returned HTTP 200 after deployment. The runtime secret has
`META_SEND_ENABLED=false`, `LLM_CUSTOMER_CONTEXT_ENABLED=true`, and the exact 18-URL allowlist. API
and worker were refreshed from the new secret version; the API reported healthy.

The 18 version-1 snapshots are active under `jane`, each with an extracted English snapshot, content
hash, audit entry and the approved effective instant. There are zero remaining website drafts. The
context flag is enabled per the owner's explicit instruction; Meta sending remains off.

The 60-minute post-enable observation is not yet recorded. Keep the existing launch-contract
thresholds and rollback procedure in force while observing readiness, provider outcomes, latency,
duplicates, errors, spend and host alarms.

## Historical approved release (not SMART)

- Commit: `579ac9efc85400c0e8dae55f9b57acb4cfbdb9c5`
- Application index: `sha256:c31c606bd0f2bc6a7d7ad5a0b16df6b26a25b00435dbdf655e4ada8c75c51bcb`
- ClamAV index: `sha256:f0ffa992f925fd37687efab9ac004878ec7a29bff46d21146c7c4fa40c91f0bc`
- Migration: `c81d4e2a7f10`

Mutable tags are not release evidence.

## Historical release checks

| Evidence | Result |
| --- | --- |
| Full suite and dependency integrity | PASS: 159 passed, 3 skipped |
| Trilingual outage evaluation | PASS: 36/36; all 15 model calls safely fell back |
| Hosted `glm-5.3-flash` evaluation | PASS: 36/36, 15/15 model calls, 3.679-second p95, USD 0.002030 estimated |
| Security scans | PASS with the time-limited exceptions below |
| Staging, dependency failure, capacity, restore, and rollback | PASS |
| WhatsApp text/media, deduplication, ticket/admin flow, and delivery | PASS |
| Named admins and approved corpus | PASS: two admins and 24 active records |
| Atomic minute/daily/global/total beta limits | PASS |
| Production readiness, TLS, alarms, backup, spend, and rollback pair | PASS |
| Owner decision and observation | GO; 60-minute observation passed without a trigger |

Run the repeatable evaluation with:

```bash
python scripts/release_eval.py --mode outage
python scripts/release_eval.py --mode live \
  --input-price <USD-per-million-input-tokens> \
  --output-price <USD-per-million-output-tokens>
```

Both modes require every mandatory case, at least 95% overall, and p95 no higher than 30 seconds.
Store only aggregate counts, latency, tokens, and cost.

## Historical exceptions and operation

Cze Yik accepted AWS-0104 for required TCP 443/465 egress and AWS-0136 for AWS-managed SNS
encryption through 14 September 2026. Remediate or obtain a new explicit decision before later
traffic. Support notifications are waived for the same period while both admins monitor the inbox.

Activation and any repeat deployment are controlled configuration changes. Immediately check public
readiness, five alarms, delivery/read states, duplicates, dead letters, p95, tickets, notification
failures, host resources, provider errors, spend, and volume warnings.

Disable outbound traffic for the launch-contract security/data-loss triggers. Pause or roll back
after 15 minutes above 5% failures/duplicates, p95 above 30 seconds, total answer failure,
availability below 99%, total spend forecast above USD 65/actual at USD 70, or AWS actual at USD 30.
Preserve inbound events and use `/opt/dudu/rollback-images`; never reverse a migration destructively.
