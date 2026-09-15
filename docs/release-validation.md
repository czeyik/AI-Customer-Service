# Release status and validation

Last verified deployment: **15 September 2026**. The LangChain refactor completed all eight
waves. Customer replies were activated at **21:39 Malaysia time**, with the beta ending
**30 September 2026, Asia/Kuala_Lumpur**. Notification sending remains disabled.

This is the release record. Use [architecture](architecture.md) for design decisions,
[production operations](production-platform.md) for deployment and recovery, and the
[beta contract](launch-contract.md) for operating limits. Runtime settings and deployed
revisions below are dated observations, not a live status endpoint.

## Deployed source and configuration

| Item | Verified value |
| --- | --- |
| Application source | `5122f81a9b9d1ee234fd05581196c0b94d118baf` |
| Fixed release ref | `customer-replies-20260915` |
| Publication | [PR #12](https://github.com/czeyik/AI-Customer-Service/pull/12), merged into `dev` as `48e6c40bce2328ba3ba5f443086fe9261e6ae98c` |
| Application image | `sha256:ffd2a765cd745b7d5f1e56ea221712623b202b0965248b312c0197d20d834fdb` |
| ClamAV image | `sha256:0c8ba5a56dbcfbf37bb3b90e46d2e7154eb54ee54c80f33ce583840264659ccd` |
| Applied migration | `e5c1a2b3d4f6` |
| Customer replies | `META_SEND_ENABLED=true`, `PUBLIC_BETA_ENABLED=true`, `PUBLIC_BETA_END_DATE=2026-09-30` |
| Model and context | `LLM_ENABLED=true`, `LLM_CUSTOMER_CONTEXT_ENABLED=true` |
| Notifications | `NOTIFICATION_SEND_ENABLED=false` |
| Model limits | 15,000 input characters and 300 output tokens per request; 30 seconds per request, five requests and ten native tool calls within 60 seconds per agent execution |
| Production | `https://support.duducaradmin.com`, instance `i-0ed4d24f663161b3f` |
| Staging | `https://staging-support.duducaradmin.com`, instance `i-0abae1e4a9f94df81` |
| AWS | Account `173454940059`, region `ap-southeast-5`, CLI profile `dudu-production` |
| Runtime secrets | `dudu-support/production/runtime-env` and `dudu-support/staging/runtime-env` in Secrets Manager |

Production uses the exact staging-tested image pair, rather than tracking a moving branch.
The deployed source has the same tree as the recorded `dev` merge above. A documentation
commit, a local migration, or a newer branch head is not itself a deployment.

## Refactor completion

| Wave | Completion evidence |
| --- | --- |
| 1. Provider and dependencies | Pinned LangChain 1.4.0 / langchain-openai 1.6.2; native tools and structured output exercised by compatibility tests and the hosted acceptance run |
| 2. Domain rules | Typed draft/control validation in `app/services/ticket_drafts.py`; consent, required fields, ownership, completion and correction checks retained |
| 3. State and memory | Typed dialogue JSON/revisions, bounded sanitized history and populated conversion through `e5c1a2b3d4f6`; migration and lifecycle checks pass |
| 4. Agent and tools | One `create_agent` owner with seven turn-local tools; synthetic and hosted conversations exercise bounded execution and staged operations |
| 5. Handler cutover | Atomic commit, revision/lease checks, durable inbox/outbox and deterministic recovery covered by handler and transport tests |
| 6. Acceptance | [Reviewed hosted report](evaluation/langchain-live-wave6-reviewed-20260915.json): `rollout_ready=true`, complete human review, mandatory behavior and cost/latency gates pass |
| 7. Release preparation | [Local release record](evaluation/langchain-wave7-local-20260915.json): 554 PostgreSQL tests pass, two skips; dependency/security, migration, ARM64 packaging and deployment/recovery checks pass |
| 8. Production deployment | [Deployment record](evaluation/langchain-production-wave8-20260915.json): staging validation, production cutover and full 60-minute observation pass; [activation record](evaluation/customer-sending-activation-20260915.json) records the subsequent customer-reply enablement |

The Wave 7 record's `pending_wave8` field describes its checkpoint time; Wave 8 subsequently
completed those steps. The remaining maintenance items below are outside the completed
refactor. Reusable decisions and procedures now live in the architecture and operations docs.

## Behavioral acceptance

The retained [evaluation index](evaluation/README.md) identifies the final reports and their
review/accounting provenance. Cze Yik's submitted review covers the exact generated answers;
importing it made no new provider calls.

| Gate | Final Wave 6 result |
| --- | --- |
| Coverage | 357/357 scenarios; 348/348 expected dispositions, including 300 FAQ cases |
| Mandatory behavior | 30/30 intakes, 18/18 handoffs, 9/9 focused dialogues; zero unintended mutations or unwanted offers |
| Human semantic review | All 300 FAQ and nine focused answers pass correctness and grounded relevance; EN/MS/ZH each 100/100 FAQ and 60/60 held-out paraphrases pass |
| Hosted reliability | 733 provider calls, 728 successes, four timeouts; five failure fallbacks across 419 agent executions |
| Latency | Turn p95 22.444 seconds, within the 30-second requirement |
| Cost | USD 0.231998 measured; USD 0.457734 conservatively bounded under the USD 1 run cap; five incomplete-usage events retain full-turn reserves |
| Beta projection | USD 8.101 per 10,000 inbound messages, using 565 measured inbound turns; below the USD 15 allowance |
| Outage | 348/348 dispositions, 30/30 intakes, 18/18 handoffs and 9/9 focused dialogues; zero provider calls or unintended mutations |
| PostgreSQL burst | Eight senders/four workers, queue p95 0.079 seconds, zero duplicates |

The reviewed report's SHA-256 is
`507040394710097cb971c663520ef9e2d837dc9b6204d633cdb6f82ac6a4fe89`.
Its recorded rates were USD 0.15/million input tokens and USD 0.50/million output tokens,
checked on 15 September 2026. These rates explain that measurement; verify rates again for
future paid evaluations. Tracked conservative hosted spend through Wave 6 was approximately
USD 4.27, including historical reserves. Release smoke usage is recorded separately.

## Deployment and activation evidence

The refactor first deployed with source `2227b650101384d35ac8d713851d584cae46e4f9`, fixed ref
`langchain-20260915-r3`. Its exact candidate CI/Security passed with 558 PostgreSQL tests
(two skips) and 552 ARM64 tests (eight skips).

Staging validated populated paused/pending/review drafts, consent conversion, evidence ownership,
private media, signed-webhook processing and deduplication. An empty-target backup restore and
migration completed in 14 seconds. Twenty-five deployed checks and nine additional isolated
failure/recovery checks passed. Disabling the LLM and restoring it on the same image pair
preserved data and idempotent ticket creation. Details, digests and workflow/SSM IDs remain in
the [Wave 8 record](evaluation/langchain-production-wave8-20260915.json).

Cze Yik authorized using the existing Meta configuration and omitted the dedicated test
app/phone and recipient/window setup. Read-only phone-ID access and signed public-webhook
processing passed. Live WhatsApp delivery was omitted; the staging and cutover checks sent
zero external test messages. Immediate production deployment was separately authorized on
15 September, overriding the usual 02:00–04:00 maintenance window for that release.

Production Release [34956214933](https://github.com/czeyik/AI-Customer-Service/actions/runs/34956214933)
and SSM `daafc6f1-566f-44d1-bd84-caab4cd60915` succeeded. Migration preserved all 14 table
counts at cutover, including five conversations, two tickets and six media records. Typed
state, media ownership and all six private objects passed checks. The actual hosted FAQ
smoke used two provider calls/one tool in 11.725 seconds; a deterministic submission/replay
check created exactly one synthetic ticket.

The full dark observation ran **18:14:03–19:14:11 Malaysia time**, lasting 3,607.906 seconds:
118/118 readiness samples, 60/60 five-alarm samples and 13/13 host checks passed. There were
zero restarts, processing errors, duplicate rows or new customer inbound events. CloudWatch
memory peaked at 78.109% against the 85% alarm threshold, and CPU at 8.313%. This was
deployment-health observation with customer sending disabled.

The later activation used the source and images in the first table:

| Gate | Evidence |
| --- | --- |
| CI/Security | `34973791316` / `34973791326`: 562 PostgreSQL tests pass (two skips), 556 ARM64 pass (eight skips) |
| Staging | Release `34974446260`, deployment `6459784323`; SSM `575cc6dc-a8c8-4b37-8c8c-2a3f486d92e1` succeeds |
| Staging checks | `2b87cc50-4d67-4edb-a340-bfa7c3ec84a6`: counter continuity/cap, signed-webhook deduplication and hosted answer pass |
| Production promotion | [Release 34975560923](https://github.com/czeyik/AI-Customer-Service/actions/runs/34975560923), SSM `4b804448-ebeb-44f1-828a-0655c195f49d` succeeds on the identical staging-tested pair |
| Activation | `bbb80a14-67ac-4147-ab95-760a6fbd02e1`: API/worker healthy; Meta/beta on through 30 September, LLM/context on, notifications off |
| Final verification | `51797ae3-73ae-4130-97cc-b1c66534845a`: normal processing admission, migration `e5c1a2b3d4f6`, empty inbox and preserved count after a rolled-back probe; all five alarms OK |

Activation restored the expired cumulative counter to 61 durable inbound messages, with expiry
at 1 October midnight Malaysia time. One never-attempted obsolete beta-ended notice was cancelled.
The 10,000 total, 2,000 daily and 200 per-user daily caps remain in force. Exact secret-version
changes and aggregate queue checks are in the [activation record](evaluation/customer-sending-activation-20260915.json).

Recorded production backups include `backups/postgresql/2026/09/15/100905.dump` at cutover
and `backups/postgresql/2026/09/15/110940.dump` after new-runtime writes. These are historical
references subject to the 35-day backup lifecycle; verify a current backup before a new release.

## Knowledge activation carried forward

Jane approved the 18 website snapshots below, effective `2026-09-11T23:00:00+08:00`.
The 12 September activation recorded 18 active version-1 documents, zero remaining website
drafts, content hashes and publication audit entries under `jane`. Changed snapshots still
require CCO review; this list is not approval of future page changes.

`WEBSITE_KNOWLEDGE_URLS` defaults to an empty JSON list. The approved runtime allowlist is:

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

Use source scope when resolving conflicts: `/about-us` governs human support hours;
`/dudu-later` governs its service-specific claims; `/car-types` covers vehicle/fare information.
Website snapshots and the [approved seed corpus](knowledge-corpus.md) have equal authority
within their scopes. Do not silently combine contradictory claims.

## Outstanding maintenance and approval boundaries

- The later repository date cleanup adds migration `f7b2c3d4e5a6` to rename the cumulative beta
  bucket without resetting usage. It preserves count, window and expiry and rejects collisions
  atomically. It and the updated defaults/templates have not been deployed; production remains
  at the applied migration above. Its local checks passed: 336 focused tests (four PostgreSQL-only
  skips in the container run) plus populated PostgreSQL preservation/cap/round-trip/collision checks.
- AWS-0104 (outbound TCP 443/465) and AWS-0136 (AWS-managed SNS encryption) were renewed through
  **17 September 2026 for staging and dark deployment only**. Infrastructure annotations use
  `exp:2026-09-17`. The customer-reply end date does not extend those exceptions or broaden their
  scope; remediation or a new explicit exception is required for a later applicable release.
- Notification sending remains disabled under the recorded owner instruction; staff monitor the
  inbox. Enabling notifications requires an explicit decision and configured delivery checks.
- Privacy-copy publication was deferred by the owner, who separately authorized minimized
  customer-context transmission. The [privacy addendum](privacy-notice-chatbot-addendum.md)
  retains the publication decisions; deployment does not publish it.
- Future releases require their own exact-source CI/security and staging evidence. Keep the
  launch thresholds and use compatible-runtime recovery after new dialogue writes; old snapshots
  do not recover new-format customer changes.
