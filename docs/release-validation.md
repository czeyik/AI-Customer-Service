# Release Validation and Public-Beta Activation

Status: **LangChain Waves 1–8 complete. Customer replies are enabled through 30 September 2026, Malaysia time. Notification sending remains disabled — see [SMART.md](../SMART.md).**

## Customer sending activation — 15 September 2026

Cze Yik explicitly authorized customer sending and selected **30 September 2026,
Asia/Kuala_Lumpur** as the end date. Activation completed at **21:39 MY**. The runtime now
accepts the configured end date instead of pinning it to 14 September; existing message limits
and the separate notification switch remain in place.

| Gate | Evidence |
| --- | --- |
| Published source | `5122f81a9b9d1ee234fd05581196c0b94d118baf`, fixed ref `customer-replies-20260915`; PR #12 merged into `dev` as `48e6c40bce2328ba3ba5f443086fe9261e6ae98c` |
| CI and Security | Runs `34973791316` / `34973791326` pass; 562 PostgreSQL tests and 556 ARM64 tests pass, with two/eight expected skips; 65 focused tests pass locally |
| Staging | Release `34974446260`, deployment `6459784323`, SSM `575cc6dc-a8c8-4b37-8c8c-2a3f486d92e1` pass. Check `2b87cc50-4d67-4edb-a340-bfa7c3ec84a6` verifies the expired-counter restoration/cap in a rolled-back transaction, signed webhook processing/deduplication and a hosted agent answer |
| Production promotion | Release `34975560923`, SSM `4b804448-ebeb-44f1-828a-0655c195f49d` pass; identical staging-tested app and ClamAV digests promoted without rebuilding |
| Activation | SSM `bbb80a14-67ac-4147-ab95-760a6fbd02e1` succeeds. API and worker are healthy with `META_SEND_ENABLED=true`, `PUBLIC_BETA_ENABLED=true`, `PUBLIC_BETA_END_DATE=2026-09-30`, LLM/context on and notifications off. Meta phone access verifies |
| Limit continuity | Restored the expired cumulative counter to 61 durable inbound messages, expiring at 1 October midnight MY. Cancelled one never-attempted obsolete capacity notice from the closed window |
| Production verification | SSM `51797ae3-73ae-4130-97cc-b1c66534845a` confirms normal processing admission, unchanged migration, empty inbound queues and preserved counter after rolling back the probe. Public readiness passes; all five production alarms are OK |

The [activation record](evaluation/customer-sending-activation-20260915.json) contains exact image
digests, timestamps, secret-version changes and aggregate checks. The dark deployment evidence below
records the earlier Wave 8 state before this separately authorized activation.
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
acceptance gates. The user resumed Wave 7 on 15 September 2026; source publication and deployment progress is recorded below.

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
waiver. Trivy uses `exp:2026-09-17`, a conservative date-only cutoff; the candidate Security workflow passed with this expiry.

Read-only AWS discovery used the `dudu-production` profile in account `173454940059`, region
`ap-southeast-5`. Foundation and production stacks exist; isolated staging was recreated successfully.
Before cutover, read-only SSM verified that production containers reported `META_SEND_ENABLED=true`,
`NOTIFICATION_SEND_ENABLED=false`, `LLM_ENABLED=true`, and `LLM_CUSTOMER_CONTEXT_ENABLED=true`.
Production input/timeout were 8,000/8s before cutover; the authorized dark deployment applied the
approved 15,000/30s limits and disabled sending.

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
### Published candidate and staging

Protected PRs [#7](https://github.com/czeyik/AI-Customer-Service/pull/7) and
[#8](https://github.com/czeyik/AI-Customer-Service/pull/8) published the refactor into `dev` and
registered Release on default `main`. Production configuration now accepts `LLM_ENABLED=false`
for compatible-runtime recovery. Two staging failures exposed operational defects:

- Run `34946722828` failed before deployment because ECR manifest publication required
  repository-scoped `ecr:BatchGetImage`. Foundation now grants that and `ecr:DescribeRepositories`.
  [PR #9](https://github.com/czeyik/AI-Customer-Service/pull/9) also extends the SSM waiter through
  nonterminal states, with a 20-minute host bound and 25-minute workflow bound. Twelve shell
  workflow checks pass, including delayed success, paced cancellation and terminal failure.
- Run `34949944201` / SSM `6b2d5929-9dd5-4398-a699-d7e7b0b24150` published the r2 images, backed
  up and stopped before migration because the direct inventory script could not import the app.
  [PR #10](https://github.com/czeyik/AI-Customer-Service/pull/10) applies the existing repository-root
  bootstrap; its regression removes `PYTHONPATH` and invokes the packaged entry point from outside
  the repository. All 25 platform checks pass; independent review found no other entry-point issue.

The final candidate is **`2227b650101384d35ac8d713851d584cae46e4f9`**, fixed by tag
**`langchain-20260915-r3`**. PR #10 CI/Security pass (558 PostgreSQL tests, two skips;
552 ARM64 tests, eight skips). Exact merged-SHA Security `34952053066` and CI `34952053006` both pass with the same test totals.

| Final staging artifact | Result |
| --- | --- |
| Release | [34952070500](https://github.com/czeyik/AI-Customer-Service/actions/runs/34952070500), success on the exact candidate |
| Host deployment | SSM `94a3ef1d-5038-494e-9fd5-31af1f717c1d`, success |
| Application | `sha256:9feab254a8dd1b8926e55f0a4ea5f4f64c7b29aefb4ec72bb3ae8a55878cc9f0` |
| ClamAV | `sha256:eace8421cd3d4910c454df520a5551cc15fc0a0efb40c122b56ab06bf6e7be42` |
| Migration | `e5c1a2b3d4f6`; all 14 application-table counts preserved |
| Fresh backup | `backups/postgresql/2026/09/15/092756.dump` in `dudu-support-staging-173454940059` |
| Runtime | Public readiness, API/worker matching app digest, database/ClamAV health pass; zero API/worker restarts |
| Configuration | Meta/notification sending off, beta off, LLM/context on, input 15,000/output 300, timeout 30 seconds |
| Deployed-artifact checks | SSM `6486f6fb-1c2e-4f01-877e-65cb382f3699`: 25 pass; paused/pending/review and owned evidence, private S3, clean/EICAR, provider-failure discard |

Staging is isolated at `staging-support.duducaradmin.com`, instance `i-0abae1e4a9f94df81`.
Its legacy baseline contained two synthetic admin accounts, 24 existing seed knowledge records,
three paused/pending/review conversations, six messages, one media row and one completed inbox row.
The earlier baseline backup `backups/postgresql/2026/09/15/090326.dump` restored into an isolated
empty database and migrated in **14 seconds**, with exact counts (SSM
`6f91c7c2-78b2-4969-b00c-49ef2290d91a`). Fixture checks use the separate database
`wave8_checks_20260915`; no production customer data was copied.

Cze Yik authorized the existing Meta configuration in Secrets Manager and waived the dedicated
test app/phone, recipient and window. Read-only phone-ID access succeeds with zero sends (SSM
`6015ffb0-3670-464c-b1d5-6da91597cc41`). Live WhatsApp delivery is omitted; sending remains off.
The bounded hosted/signed-webhook smoke succeeds (SSM `ea58e628-8f49-4d35-be6e-c9631cd6ceb6`):
one measured agent execution, three provider calls, one tool, 4,518 input and 329 completion tokens,
24 reported reasoning tokens, **21.250 seconds**, no timeout/fallback. The public signed webhook
rejects an invalid signature, deduplicates the event exactly once, and the actual worker completes
it with one unsent outbox row. External messages sent: **zero**.

Same-image recovery with `LLM_ENABLED=false` succeeds: Release `34953325146` / SSM
`904c3ec2-812c-4037-97fa-3aae76606fe5`. Fallback SSM `e51576c4-ad39-4a65-8c2d-498f50969f07`
confirms both actual containers use the same digest with LLM off and sending off. FAQ, consent-first
ticket creation and replay pass with zero model calls and exactly one ticket. Signed public webhook
processing and deduplication pass through the actual fallback worker, with zero sends. Recovery
backup: `backups/postgresql/2026/09/15/093751.dump`. LLM-on roll-forward succeeds on the same tag: Release `34953739867` / SSM
`0ae1f794-883f-49fd-85a3-387d0013eb54`. Final verification SSM
`3fe2e842-8081-4d62-a814-8b428fc488af` confirms the identical app/ClamAV pair, health, zero restarts,
LLM/context on, input 15,000/timeout 30, sending off, and preserved table counts. Final backup:
`backups/postgresql/2026/09/15/094203.dump`. Nine additional isolated DB/worker/Meta/SMTP failure
checks pass (SSM `057b9e18-2be0-467a-bc61-619d0b35d422`), covering unavailable DB, retry bounds,
uncertain delivery and signed reconciliation. No network sends are used by these failure checks.

**Pre-cutover baseline:** production read-only conversion preflight passed all five conversations (four without drafts,
one active draft), with zero writes or restarts (SSM `8ed02633-346a-4504-84de-c3ca42d9e307`).
The quiesced deployment repeated preflight against the final cutover state. All five production
alarms were OK at 17:36 MY. The project-tagged AWS budget reports USD 0.096 against USD 30, last
updated 14:40 MY; this is delayed tagged spend, not a whole-account total. Production, its secret
and sending state remained unchanged during preparation. Both reviewed images were downloaded without a
restart (SSM `6526d6b3-079a-41c1-b630-18524793b208`); pre-pull free disk was 11.06GB and the latest
hourly S3 backup was `backups/postgresql/2026/09/15/093602.dump`. The authorized cutover and post-cutover observation are recorded below.
The production monitor was uploaded and validated read-only (SSM
`591e1082-7534-4eba-8354-491e0fc76b21`): no errors, restarts, duplicate rows or new inbound traffic;
host memory 51.72%. The existing two pending notifications were left unsent. These results are predeployment baseline evidence; the post-cutover observation is recorded below.

### Production cutover — 15 September 2026

Cze Yik instructed “deploy now and start wave 8” at approximately 18:06 MY, authorizing the
exception to the usual 02:00–04:00 window. Current candidate/staging gates remain successful.
The production runtime secret moved with a compare-and-swap from
`7d4ec156-5a17-4562-8d04-f5c10fa9897f` to `19d61621-f311-4e18-87fb-3beb75d1b19c`.
Only `META_SEND_ENABLED=false`, `LLM_MAX_INPUT_CHARS=15000`, and `LLM_TIMEOUT_SECONDS=30`
changed; notifications remain off, LLM/context remain on, and beta dates were not extended.
Production Release [34956214933](https://github.com/czeyik/AI-Customer-Service/actions/runs/34956214933)
and SSM `daafc6f1-566f-44d1-bd84-caab4cd60915` succeed on the staging-tested
`langchain-20260915-r3` ref and exact candidate SHA. The identical app and ClamAV digest pair is
running; API and worker are healthy with zero restarts. Verification SSM
`e8f30db9-b66e-4643-9efc-57a4faaecea6` confirms migration `e5c1a2b3d4f6`, all 14 table counts
preserved at cutover (five conversations, two tickets, six media), valid typed dialogue, no media
ownership mismatch, and all six approved private S3 objects present with matching byte lengths.
Fresh backup: `backups/postgresql/2026/09/15/100905.dump` in the production bucket.
Retention removed zero records. Actual API/worker settings: Meta/notification sending
off, LLM/context on, input 15,000 and timeout 30 seconds.

Production smoke SSM `6562da07-cfd7-4632-a201-a4d33dbbd732` passes: the actual running API invokes
LangChain for a sourced FAQ answer in **11.725 seconds**, using two provider calls, one tool,
3,691 input and 117 completion tokens. A separate deterministic control check creates exactly one
consented synthetic ticket and returns the same receipt on replay, with zero provider calls and
zero sends. One notification remains queued. The first smoke harness (`97cedc65-38e9-4fea-b02f-d64df9a93637`)
passed its hosted assertions but used an unrecognized synthetic fallback phrase; the correction
reused the staging-tested phrase and changed no application code. Both smoke attempts are synthetic;
post-smoke totals are nine conversations, three tickets, six media, 126 messages, and 15 notifications.

The full observation began **18:14:03 MY / 10:14:03 UTC**, after the final configuration and smoke.
The observation ended **19:14:11 MY / 11:14:11 UTC**, after **3,607.906 seconds**, and passed.
The [aggregate evidence](evaluation/langchain-production-wave8-20260915.json) contains timestamps,
all samples, source and deployment provenance, smoke results and minimized table counts.

| Production observation gate | Result |
| --- | --- |
| Public readiness | 118/118 pass; longest sample gap 37.899 seconds |
| Existing alarms | All five OK in all 60 samples |
| Host, queues and logs | 13/13 checks pass; zero restarts, OOM events, processing errors, failed/uncertain audit events or duplicate rows |
| Traffic and sending | Zero new customer inbound rows; existing inbox/outbox complete; three notifications remain pending and unsent, including the synthetic smoke notification |
| Resources | CloudWatch memory peak 78.109% (85% threshold), CPU peak 8.313% |
| Backup | New-runtime hourly backup `backups/postgresql/2026/09/15/110940.dump`, 173,917 bytes |
| Budget | Project-tagged USD 0.096 against USD 30 at both checks; reporting timestamp 14:40 MY |
| Final integrity | SSM `ca27f2c2-b084-42d9-b60f-fdfd2d22ea14` passes exact images/flags, health, typed dialogue, media ownership and all six private media objects |

The separate host snapshot calculates memory from `MemAvailable` and peaks at 87.39%; the alarm
uses CloudWatch `mem_used_percent`, whose peak is 78.109%. The alarm metric remained below its
threshold. With no new customer traffic, this observation establishes dark deployment health.
No configuration recovery or new application build was required. Customer and notification sending
remain disabled, with LLM/context enabled and the evaluated model limits applied.

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
