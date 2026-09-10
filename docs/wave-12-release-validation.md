# Wave 12 Release Validation and Pilot Activation

Status: **IN PROGRESS**
Owner and go/no-go authority: Cze Yik
Support lead and CCO: Jane
Target pilot: 15–29 September 2026

Production remains dark. `META_SEND_ENABLED` and `NOTIFICATION_SEND_ENABLED` must stay false until
the documented go/no-go approval authorizes the limited pilot.

## Release candidate

The release candidate is frozen at `77ce6c9a23f125b3e3ef5f52cf0672d6dbb9646b`. PR 2 merged it to
`dev` as `0a6b3b3177a797c13e9bcc69582843388d55fc31`; GitHub CI run 34392289697 and security run
34392289872 passed. Its ARM64 application digest is
`sha256:d724de8f53a73567b0357c23985765e2cbb7d355cf2946c6ee86516d15ce9d12`; its ClamAV
digest is `sha256:f0ffa992f925fd37687efab9ac004878ec7a29bff46d21146c7c4fa40c91f0bc`.
Both ECR child-image scans completed with zero findings. Temporary staging runs this exact pair at
migration `c81d4e2a7f10`. Mutable tags are not release evidence.

Corrective PR 3 merged to `dev` as `c4d41a904161486874c4a928ed985f7ae1170fa0` after its CI and
security workflows passed. Its ARM64 application index is
`sha256:d50cbb6195b7486517410eaf1b859d3b6fef056f7ccc51561341b61b9d21319e`; production retains the
qualified ClamAV digest above. The corrective image explicitly signs media-review URLs against the
Malaysia S3 endpoint and adds localized additional-details/`Done` guidance after attachments.

## DUDU evaluation

Run `python scripts/release_eval.py --mode outage` in the clean release image. This exercises 36
cases: rider, driver, business partner, safety, fraud, payment, account, complaint, explicit human
escalation, normal FAQ, prohibited action, and uncertainty in each of English, Bahasa Malaysia,
and Simplified Chinese. The five grounded-answer cases per language must traverse the provider
adapter; outage mode deliberately fails all 15 calls and proves the deterministic approved-corpus
fallback.

After the owner authorizes billable calls, run the same matrix against the configured hosted model:

```text
python scripts/release_eval.py --mode live \
  --input-price <current-USD-per-million-input-tokens> \
  --output-price <current-USD-per-million-output-tokens>
```

Live mode fails if any of the 15 provider calls falls back. Both modes require every mandatory
case to pass, at least 95% overall, and p95 latency no higher than 30 seconds. Record aggregate
latency, token counts, estimated cost, and pass/failure counts only; do not store prompts, answers,
request IDs, credentials, or tester data in the evidence.

An authenticated, non-generation `GET /api/paas/v4/models` check on 10 September 2026 confirmed
that the configured Z.AI account exposes the exact `glm-5.3-flash` model. Z.AI's official pricing
page lists USD 0.15/million input tokens and USD 0.50/million output tokens. The first authorized
live run attempted exactly 15 provider calls: 14 succeeded and one safely fell back. Cze Yik then
authorized exactly one 15-call rerun. It passed 15/15 provider calls and all 36 scenarios, used
6,989 input and 1,964 output tokens, had 3.679-second p95, and cost an estimated USD 0.002030. No
further model call is authorized or required.

## Release gate

| Evidence | State |
| --- | --- |
| All earlier gates | PASS (PG-01 through PG-11) |
| Clean full test suite and dependency integrity | PASS RC: 149 passed, 2 opt-in integrations skipped; GitHub CI passed |
| Trilingual deterministic outage evaluation | PASS RC: 36/36; 15/15 adapter failures safely fell back |
| Hosted `glm-5.3-flash` evaluation, actual latency/tokens/cost | PASS: authorized rerun 15/15 provider calls and 36/36 scenarios |
| Secret, dependency, static, source/image and dynamic scans | PASS RC WITH TIME-LIMITED OWNER EXCEPTIONS |
| Temporary staging deployment and failure checks | PASS: exact digest pair; readiness, capacity, DB, ClamAV and S3 checks |
| Real WhatsApp text, JPEG/PNG, MP4/3GP, duplicate and delivery checks | PARTIAL: live text/JPEG and delivery/read passed; corrective media review and live video retest pending |
| Two named admins review media and complete the ticket lifecycle | PASS: both reviewed approved media; assignment, note and open → in-progress → closed passed |
| Production TLS/readiness, alarms, backup, rollback pair and spend | PASS: RC dark-deployed; readiness, five alarms, backup, rollback and budget healthy |
| Production named admins and active approved corpus | PASS: 2 active admins with matching TOTP references; 24 approved records |
| Current Meta messaging/media policy review | PASS (10 September 2026) |
| P0/P1 and waiver review | PENDING GO/NO-GO |
| Go/no-go decision | PENDING |
| Limited cohort activation and observation | PENDING |

The local outage evaluation found one release defect: a generic word such as “policy” could make
an unrelated question appear grounded when the approved corpus happened to contain that word.
The shared tokenizer now excludes generic English, Malay, and Chinese policy terms; its focused
test and the 36-case evaluation prevent regression.

Meta's official documentation was reviewed on 10 September 2026. Its opt-in page, updated 16 June
2026, still requires the recipient's phone number and permission to receive messages from the
named business. Its send-messages page still defines a 24-hour customer-service window after a
user message or call and requires templates outside that window. Its media page still lists JPEG
and PNG images up to 5 MB and MP4 and 3GPP video up to 16 MB, matching the approved application
limits. Sources:
`https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in`,
`https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages`,
and
`https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/media`.

## Open release findings

Production preflight on 10 September found healthy API, worker, PostgreSQL, ClamAV and Caddy
containers, readiness 200 with valid TLS, all five project alarms `OK`, no pending outbound/media
work, both an active and prior rollback digest pair, and AWS budget actual spend USD 0 at the time
of the check. Both send switches remain disabled. The fresh production database has no active
named administrator and no active approved knowledge document, so it could not pass admin review
or serve grounded pilot answers. Cze Yik and Jane were subsequently provisioned with the required
recovery and CCO roles, and Jane published the 24 approved corpus records. Both TOTP references now
match the compact two-entry secret. Credentials and personal contact details must not enter this
document or chat.

Temporary staging deployed the immutable candidate pair successfully. It passed 1,000/1,000
public HTTPS readiness requests at four-way concurrency with 0.060-second p95, returned 503 while
PostgreSQL was unavailable and recovered, failed closed while ClamAV was unavailable and recovered,
accepted a clean scan, rejected EICAR, and completed an `aws:kms` encrypted S3 write/read/delete
round-trip. Both outbound switches stayed false. The initial strict hosted-model finding was
cleared by the single owner-authorized rerun recorded above.

The current Trivy 0.74.0 source scan found two critical AWS-0104 findings because the application
needs public HTTPS egress to dynamic Meta, Z.AI and AWS endpoints and SMTP/TLS egress to the
approved mail service. The former all-protocol rule has been narrowed locally to TCP 443 and 465,
but security groups cannot express hostname destinations. AWS recommends DNS/SNI filtering with
AWS Network Firewall for dynamic endpoints; that is outside the approved single-host USD 20 pilot
architecture. Trivy also reports AWS-0136 high because encrypted SNS uses the free AWS-managed
`alias/aws/sns` key rather than a customer-managed key. A customer-managed key costs USD 1/month
before request charges and would exceed the verified USD 19.54 basis and USD 20 ceiling. These
findings have owner-approved, resource-scoped exceptions valid only through 30 September 2026;
the inline controls expire on 1 October and remediation is required before a broader launch.
Official AWS sources reviewed 10 September 2026:
`https://docs.aws.amazon.com/pdfs/prescriptive-guidance/latest/secure-outbound-network-traffic/secure-outbound-network-traffic.pdf`,
`https://docs.aws.amazon.com/sns/latest/dg/sns-enable-encryption-for-topic.html`, and
`https://aws.amazon.com/kms/pricing/`.

## Go/no-go record

Do not mark this section approved from an inferred instruction. Cze Yik records the decision after
reviewing the completed evidence above.

- Decision and timestamp: PENDING
- Approved release SHA and digest pair: candidate evidence recorded above; OWNER APPROVAL PENDING
- Open P0/P1 findings: corrected media review and remaining representative live-media checks pending
- Waivers, owner, expiry, and remediation date: Cze Yik accepted AWS-0104 for required TCP 443/465
  egress and AWS-0136 for AWS-managed SNS encryption through 30 September 2026; expires 1 October
  2026; remediate before broader launch
- Current AWS and total external-service spend: PENDING
- Approved invitee list location and count (no personal data here): PENDING
- Authorization to enable Meta outbound and support notifications: PENDING
- Pre-release staging, hosted-model and two-tester WhatsApp/media validation: AUTHORIZED 10
  September 2026
- Observation window: proposed first 60 minutes after activation, then daily review; PENDING OWNER

## Activation and observation

Activation is a controlled configuration change, not a new build. Update the production runtime
secret through the approved operator path, enable only Meta outbound and approved support
notifications, restart the affected services, and immediately verify readiness. Do not broaden
the invitee list or the contract limits: 100 participants, 15,000 inbound messages total,
1,000/day, and 20/participant/day.

During the observation window, verify public readiness, all five production alarms, accepted and
delivered/read WhatsApp messages, duplicates, dead letters, message p95, tickets, notification
failures, EC2 memory/CPU, provider errors, and current AWS/provider/Meta spend. Use aggregate or
synthetic evidence only.

Disable outbound traffic immediately for the launch-contract security/data-loss triggers. Pause
or roll back after 15 minutes above 5% failures or duplicates, p95 above 30 seconds, total answer
failure, availability below 99%, forecast total spend above USD 65, actual total spend at USD 70,
or AWS actual spend at USD 20. Preserve inbound events for replay. The prior immutable digest pair
in `/opt/dudu/rollback-images` remains the application rollback target; never reverse a migration
by destroying data.
