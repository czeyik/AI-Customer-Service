# Release Validation and Public-Beta Activation

Status: **SMART local validation complete; release pending CCO review, staging validation, and GO**
Go/no-go owner: Cze Yik
Support lead and CCO: Jane
Beta: 10–14 September 2026

The approved beta release recorded below is historical. It is not the SMART release candidate and
does not prove the current candidate is deployed or approved.

## SMART release candidate — local validation pending release gates

Candidate source: `23ab970ee077027aa902b903f169affa136a22ce`
Local verification image: `dudu-support:smart-check` (`sha256:e699fc819517e390687e64301146b8e45e43ce6edc778c8dcd282c483590feea`)
Migration head: `d9010a1b2c3d`
Release status: **PENDING** — direct production deployment is authorized, but no production
deployment, knowledge snapshot activation, privacy-copy publication, or post-deploy observation is
recorded for this candidate.

| Gate | Result |
| --- | --- |
| Final-source PostgreSQL suite | PASS: 210 passed, 2 skipped |
| Outage routing evaluation | PASS: 300/300 non-escalation; EN/MS/ZH each 100/100 |
| Outage handoff/intake/mutation checks | PASS: 18/18 handoffs, 30/30 intakes, 0 unintended mutations |
| Approved website extraction preflight | PASS: all 18 selected URLs extracted successfully; activation remains audited |
| Hosted-model automated routing | PASS: 295/300 overall; EN 99/100, MS 97/100, ZH 99/100 |
| Hosted-model necessary handoffs and intake | PASS: 18/18 and 30/30 |
| Held-out routing cases | PASS: 60/60 development cases and 60/60 fresh independent release cases |
| Hosted-model usage | 531 calls / 527 successes; 602,547 input and 67,608 completion tokens; 9,290 reasoning tokens reported |
| Hosted-model cost and latency | USD 0.124186 measured; USD 2.339 sample projection per 10,000 calls; 3.413-second p95 |
| Consequential false mutations | PASS: 0 |
| Semantic answer correctness / grounded relevance | PASS: 300/300 development records reviewed for each field; fresh holdout review pending |
| CCO review and rollout readiness | Development review complete; fresh holdout review pending; `rollout_ready=false` until all production gates finish |

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
(SHA-256 `e3477d6a3ef3cf7e6eccc1936c823984c97487b5138ac5b6f41516c306e460c0`) and was run on the
immutable local image `sha256:cc7dce73d738b0eae39c8df616fe71bddd8390da11f74dcbe4f137800ec4a419`.

Automated results are **60/60 non-escalating** (EN/MS/ZH 20/20 each), zero unexpected offers,
zero unintended mutations, 299 provider calls / 297 successes, 3.265-second p95, and measured
cost USD 0.071649. Semantic review remains pending in
[`smart-independent-holdout-review.json`](evaluation/smart-independent-holdout-review.json);
all 60 `answer_correct` and `grounded_relevant` fields are intentionally unscored until the CCO
reviews the generated answers.

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
