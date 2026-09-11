# Production Platform and Operations

Status: **PASS**
Owner: Cze Yik
Region: AWS Malaysia (`ap-southeast-5`)
Domain: `support.duducaradmin.com`

## Platform

- Isolate staging and production stacks, VPCs, instances, roles, secrets, buckets, volumes, and data.
- Production runs Caddy, FastAPI, PostgreSQL, ClamAV, and the combined worker on one encrypted ARM64
  EC2 `t4g.small` with bounded containers/logs and encrypted swap.
- Only ports 80/443 are public; HTTP serves ACME and redirects to HTTPS. Use Systems Manager instead
  of SSH. Internal services expose no host ports and Caddy access logs are disabled.
- Use an EC2 role, Secrets Manager, private encrypted S3, ECR, CloudWatch, AWS Backup, Route 53, and
  GitHub OIDC. Production accepts only staging-tested commit SHAs and immutable image digests.
- Public-beta AWS ceiling: USD 30. Alert at USD 20 forecast, USD 25 actual, and USD 28 actual. At
  USD 28 remove staging/freeze expansion; at USD 30 disable outbound and stop nonessential resources
  after backup. Review billing daily.

The approved single host provides 99% beta availability, not high availability. Promotion to
`t4g.medium` or a multi-AZ design requires owner approval.

## Monitoring and recovery

- At least 95% of valid messages must receive exactly one accepted response within 30 seconds.
- Alert on failed EC2 checks, CPU above 80%, memory above 85%, failed public readiness, and application
  error patterns. Cze Yik owns critical on-call; Jane receives support-impact notifications.
- Maintenance window: 2:00–4:00 AM Malaysia time.
- Create encrypted PostgreSQL backups hourly and EC2 recovery points daily. S3 lifecycle and workers
  expire backups within 35 days. Reconcile orphaned media daily.
- RPO: one hour. RTO: four hours. Restore only into an isolated empty target; run migrations,
  retention, readiness, reconciliation, and smoke checks before changing DNS or enabling traffic.

```bash
python -m app.workers.backup --restore-key <key> --confirm-empty-target
```

## Deployment and rollback

1. Deploy `infra/aws/budget.yml` in `us-east-1`, then `infra/aws/foundation.yml` and the environment
   stack in `ap-southeast-5`.
2. Provision the runtime secret directly from `infra/production/runtime.env.example`; never commit
   or paste the populated file.
3. Dispatch `.github/workflows/release.yml` to staging, validate the immutable digest pair, then
   dispatch production with the exact 40-character commit SHA. Keep Meta sending disabled during
   deployment; activation is separate.
4. Roll back with `/opt/dudu/rollback-images` through the same deployment command. Never reverse a
   migration by destroying data; forward-fix or restore if schemas are incompatible.

Staging must exercise PostgreSQL, ClamAV, S3, hosted-model, Meta, and SMTP failure/recovery plus
digest-pair deploy, rollback, and roll-forward.

## Evidence

- CloudFormation lint/drift, HTTPS/readiness/security headers, alarm routing, staging failure tests,
  and digest deploy/rollback passed.
- Capacity passed 1,000/1,000 requests at four-way concurrency and 0.796-second p95.
- An encrypted isolated restore, migration, and retention pass completed within RPO/RTO.
- Release-specific SHAs, digests, scans, and activation evidence: `docs/release-validation.md`.
