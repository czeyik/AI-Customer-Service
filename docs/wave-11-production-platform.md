# Wave 11 Production Platform and Operations

Status: **PASS**  
Owner: Cze Yik  
Region: AWS Malaysia (`ap-southeast-5`)  
Production domain: `support.duducaradmin.com`

## Approved platform

- Use one AWS account with separately named and tagged staging and production stacks. They do not
  share instances, VPCs, roles, secrets, buckets, volumes, or PostgreSQL data.
- Start production on one Graviton `t4g.small` with 2 vCPUs, 2 GB RAM, 2 GB encrypted swap, and a
  16 GB encrypted gp3 root volume. Promotion to `t4g.medium` requires Cze Yik's explicit approval
  after the capacity test fails. Standard CPU-credit mode prevents unexpected unlimited-burst
  charges.
- Keep the monthly AWS ceiling at USD 20. Alert at USD 10 forecast, USD 15 actual, and USD 18
  actual. At USD 18, remove staging and freeze expansion. At USD 20, disable Meta outbound and stop
  nonessential resources after a successful backup. Billing data is delayed, so Cze Yik checks it
  daily during the pilot.
- Use an EC2 instance role, AWS Secrets Manager, Systems Manager Session Manager, private encrypted
  S3, ECR, CloudWatch, AWS Backup, Route 53, and Caddy-managed TLS. Do not create AWS access keys,
  expose SSH, add a load balancer, or add a NAT Gateway.
- GitHub Actions assumes a repository- and environment-scoped role through OIDC. Staging builds
  ARM64 images; production accepts only the same commit SHA and ECR digests that staging tested.

The 2 GB host is a deliberate pilot ceiling. Container limits, bounded logs, one combined worker,
and swap keep the footprint small. The gate cannot pass until measured capacity proves 1,000
messages/day, approved media scanning, p95 response below 30 seconds, and no sustained swapping or
memory alarm.

The 730-hour steady-state price basis checked against AWS's 10 September 2026 price catalog is
USD 13.94 for `t4g.small` (`$0.0191/hour`), USD 3.65 for one public IPv4 address, USD 1.38 for
16 GB gp3 (`$0.0864/GB-month`), and USD 0.40 for one runtime secret. The first production snapshot
contains 3,855,613,952 billed bytes, about USD 0.16 at `$0.045/GB-month`, for an observed recurring
basis of about **USD 19.54/month** before negligible request and S3 usage. Five alarms, four custom
metrics, and expected log volume fit the published CloudWatch free tier. Temporary staging is
deleted after validation; its bucket versions, secret, logs, recovery point, vault, and detached
volume are also deleted so they cannot become recurring spend. Billing alerts remain mandatory
because snapshot, log, transfer, and request usage can grow.

## Service and data flow

```text
Meta -> Route 53 -> Elastic IP -> Caddy TLS -> FastAPI -> PostgreSQL
                                      |          |
                                      |          +-> combined queue/lifecycle/backup worker
                                      |                       |       |       |
                                      |                     Meta    ClamAV    S3
                                      |
GitHub -> OIDC -> ECR digest -> SSM deployment -> EC2 instance role
                                                  |     |      |
                                           Secrets  CloudWatch  AWS Backup
```

Only ports 80 and 443 are public. HTTP exists only for ACME and redirects to HTTPS. PostgreSQL,
ClamAV, workers, and the API container have no host port. Caddy access logs are disabled so URLs,
IP addresses, admin paths, and webhook queries do not enter CloudWatch. Application logs rotate on
the host and expire from CloudWatch after 30 days.

## Availability and alerting

- SLO: 99% pilot availability, excluding evidenced Meta outages.
- Performance: at least 95% of valid messages receive exactly one accepted response within 30
  seconds.
- Cze Yik owns critical on-call, infrastructure, releases, rollback, and incidents. Jane receives
  customer-support impact through the approved notification workflow.
- CloudWatch alerts on two failed EC2 status checks, sustained CPU above 80%, sustained memory
  above 85%, missing/failing public HTTPS readiness, and application error log patterns.
- Maintenance window: 2:00–4:00 AM Malaysia time. Customer-impacting maintenance outside that
  window follows the launch-contract rollback rules.

## Backup and restore

- The combined worker creates a PostgreSQL custom-format backup every hour, without putting the
  password in process arguments, and writes it to `backups/postgresql/` in the environment's S3
  bucket. S3 lifecycle and the worker delete backups older than 35 days.
- AWS Backup captures the encrypted EC2 instance daily at 2:00 AM Malaysia time and deletes
  recovery points after 35 days. S3 versioning protects approved media; application lifecycle
  deletion removes every object version before database deletion. Daily reconciliation deletes
  objects older than 24 hours that have no approved database row and alerts on cleanup failure.
- RPO is one hour and RTO is four hours. A restore is never pointed at production DNS until it has
  run migrations, `python -m app.workers.retention --once`, `/ready`, attachment reconciliation,
  and the smoke suite successfully.

Restore proof must use a new isolated environment: restore the latest instance recovery point,
restore the latest PostgreSQL dump into an empty database with `python -m app.workers.backup
--restore-key <key> --confirm-empty-target`, run migrations and the post-restore gate, measure RPO
and RTO, then remove the temporary resources. The restore command independently verifies that the
target has no tables and rejects keys outside the backup prefix. Never overwrite the only
production volume during a test.

## Deployment and rollback

1. Deploy `infra/aws/budget.yml` in `us-east-1` (AWS billing resources are global and unavailable
   to CloudFormation in Malaysia), confirm the USD 20 budget subscription, then deploy
   `infra/aws/foundation.yml` in `ap-southeast-5`.
2. Deploy `infra/aws/environment.yml` as `dudu-support-staging`; provision its runtime secret
   directly from a local copy of `infra/production/runtime.env.example` with
   `aws secretsmanager put-secret-value --secret-string file://...`. Never paste or commit the
   populated file.
3. Configure protected GitHub `staging` and `production` environments with only
   `AWS_DEPLOY_ROLE_ARN`. Dispatch `.github/workflows/release.yml` to staging.
4. The workflow builds ARM64 images, resolves their digests, and uses Systems Manager to extract
   the versioned production bundle. `deploy.sh` rejects mutable images and an enabled Meta send
   flag, migrates, reapplies retention, starts containers, and requires public TLS readiness.
5. After staging tests pass, dispatch production with the exact tested 40-character commit SHA.
   Keep `META_SEND_ENABLED=false`; Wave 11 is a dark deployment.

For application rollback, retrieve `/opt/dudu/rollback-images` through Session Manager and run
that prior digest pair through the same deployment command. A successful deployment atomically
records its digest pair in `/opt/dudu/last-good-images` after first copying the former pair to
`/opt/dudu/rollback-images`. Do not reverse a data migration. If the prior application is
incompatible with the migrated schema, keep traffic dark and forward-fix. For host loss, use the
isolated restore procedure above and change Route 53 only after the restore gate passes.

## Dependency failure exercises

The staging evidence must cover PostgreSQL unavailable (`/ready` returns 503), ClamAV unavailable
(media remains inaccessible and retries), S3 unavailable (metadata remains for retry and an alert
fires), hosted LLM unavailable (deterministic fallback), Meta unavailable (bounded retries/dead
letter), and notification SMTP unavailable (durable retry). Restore PostgreSQL, ClamAV, and S3 in
turn and prove the queued work completes once.

## Verification evidence

- The final ARM64 image passed all 146 tests with two opt-in integration tests skipped. Bash,
  Compose, Actionlint, CloudFormation linting, drift detection, and `git diff --check` passed.
  Production drift is `IN_SYNC` with zero drifted resources.
- ECR scan-on-push reports no findings for final application manifest
  `sha256:017d7673322d...` or final ClamAV manifest `sha256:d4f6ee803c7c...`. An earlier ClamAV
  image was rejected after ECR found OpenSSL 3.5.7 issues; rebuilding with Alpine's fixed 3.5.8
  cleared every finding before the final pair was activated.
- The exact final digest pair passed isolated staging: HTTPS readiness, 1,000 of 1,000 successful
  requests at four-way concurrency, 0.796-second p95, and about 22% peak EC2 CPU. The temporary
  staging stack and every retained billable artifact were then removed.
- `support.duducaradmin.com` resolves to the production Elastic IP, negotiates a valid public TLS
  certificate, returns readiness 200, and sends HSTS, CSP, permissions, referrer, MIME, and frame
  headers. Only TCP 80/443 and UDP 443 are public; SSH is absent.
- Production runs the final staging-tested application digest
  `sha256:2cb353621641...` and scanner digest `sha256:1bf373b34d1d...`; PostgreSQL, ClamAV, API,
  worker, and Caddy are healthy. Runtime proof reports `META_SEND_ENABLED=false` and
  `NOTIFICATION_SEND_ENABLED=false`.
- An encrypted hourly PostgreSQL dump restored into an empty isolated target, migrated to
  `c81d4e2a7f10`, reran retention with zero failures, and completed in 13 seconds. The AWS Backup
  production recovery point completed encrypted and expires on 15 October 2026, satisfying the
  one-hour RPO, four-hour RTO, and 35-day limit.
- Staging exercises proved database loss returns readiness 503 then recovers, ClamAV fails closed
  then recovers, a missing S3 target fails safely, and 26 ARM64 LLM/Meta/SMTP/media retry tests pass.
  Digest-pair deployment, rollback, and roll-forward passed in both staging and dark production.
- All five production alarms are `OK` and target the email-only SNS topic. Cze Yik confirmed the
  `support@duducar.co` subscription. A CloudWatch `OK` → `ALARM` routing test at 02:28 Malaysia
  time recorded a successful SNS action to that confirmed endpoint, and the test alarm was reset
  to `OK`. PG-11 is `PASS`; production remains dark pending Wave 12.

Official references reviewed 10 September 2026:

- EC2 T4g characteristics and current free-trial notice:
  `https://aws.amazon.com/ec2/instance-types/t4/`
- Public IPv4 pricing: `https://aws.amazon.com/vpc/pricing/`
- EC2/EBS prices: `https://aws.amazon.com/ec2/pricing/on-demand/` and
  `https://aws.amazon.com/ebs/pricing/`
- CloudWatch free tier and pricing: `https://aws.amazon.com/cloudwatch/pricing/`
- Secrets Manager pricing: `https://aws.amazon.com/secrets-manager/pricing/`
- GitHub OIDC federation:
  `https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-idp_oidc.html`
- EC2 IAM roles:
  `https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-roles-for-amazon-ec2.html`
- AWS Backup plans:
  `https://docs.aws.amazon.com/aws-backup/latest/devguide/creating-a-backup-plan.html`
