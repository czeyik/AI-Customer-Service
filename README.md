# DUDU Car AI Customer Service Chatbot

MVP foundation for a secure, WhatsApp-first informational support chatbot for DUDU Car riders,
drivers, and organizations interested in business collaboration.

The approved project direction is:

- WhatsApp is the only public launch channel; Instagram is a future option.
- English, Bahasa Malaysia, and Simplified Chinese.
- FAQ deflection and accurate, approved information as the main success goals.
- Consent-first complaint, safety, human-escalation, and partnership ticket intake.
- A purely informational bot: no refunds, cancellations, account changes, approvals, payments, or
  other business-state changes.
- Hosted GLM-5.3-Flash, with a deterministic approved-knowledge outage fallback.
- PostgreSQL, Docker Compose, secure media storage, and multiple named administrator accounts in
  the launch target.

The complete, authoritative baseline is
[`docs/requirements-summary.md`](docs/requirements-summary.md).

## Current Capabilities

- `POST /api/chat` accepts support messages and returns multilingual answers.
- The bot uses approved knowledge chunks before answering.
- If the bot is uncertain, it offers ticket creation instead of guessing.
- Complaints and safety issues enter a consent-first ticket flow.
- Prompt-injection attempts and account-changing requests are refused.
- Sensitive uploads and risky secrets are rejected or redacted.
- Named CCO sessions can draft, publish, remove, and roll back versioned knowledge through
  `/api/knowledge/documents`; inactive versions never reach customer answers.
- `/webhooks/meta` strictly verifies WhatsApp webhooks, deduplicates Meta message IDs, and commits
  each conversation state transition with a durable outbound queue row.
- `/admin` provides the named-administrator ticket inbox, assignment, status, and internal-note
  workflow after password + individual 2FA login.
- Signed WhatsApp image/video events enter a quarantine queue; the media worker authenticates to
  Meta, enforces streaming limits and file signatures, scans with ClamAV, and stores only clean
  objects. Active administrators receive audited five-minute review links from the ticket page.
- PostgreSQL-backed hashed rate limits are shared across application processes and bounded by an
  expiring active-key cap; administrator sign-in has a stricter attempt window.
- A bounded lifecycle worker permanently deletes chats after 90 days and closed tickets plus
  media after 36 calendar months, while audited privacy-owner holds pause only their named record.
- Production enables HTTPS redirect, HSTS, secure session cookies, restrictive browser headers,
  explicit hosts/CORS, disabled API documentation, non-root containers, and fail-closed AWS
  Secrets Manager configuration.

## Quick Start With Docker

`docker-compose.yml` is development-only. Production deployment is intentionally separate and
will promote a tested image digest and migration set through the release workflow.

1. Create your local environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and change the obvious secrets.

3. Start the app and database:

   ```bash
   docker compose up --build
   ```

4. After provisioning Jane's named CCO account, publish the approved launch corpus:

   ```bash
   docker compose exec api python scripts/ingest_seed.py --cco-username jane
   ```

5. Open the API:

   - API docs: http://localhost:8000/docs
   - Health check: http://localhost:8000/health
   - Admin inbox: http://localhost:8000/admin

Administrator accounts are provisioned explicitly; there is no shared or fallback 2FA code.

Website pages can be extracted into inactive versions for CCO review. This command never
publishes them:

```bash
docker compose exec api python scripts/stage_website.py --cco-username jane
```

The importer accepts the exact HTTPS pages configured in `WEBSITE_KNOWLEDGE_URLS`, reads at most
2 MB per page, and removes scripts, navigation, forms, and footers. The allowlist defaults to empty.
Jane must review and activate each language version through her authenticated session.

Knowledge management uses Jane's normal named-admin session, not a shared API key. A CCO client
can obtain its CSRF token from `GET /api/knowledge/session`, list the bounded version history at
`GET /api/knowledge/documents`, and use the documented publish, activate, remove, and rollback
endpoints. Every mutation records Jane's account, source URI, version, and replacement details.
Customer retrieval ranks all effective `active` chunks up to the 500-chunk ceiling, including
titles and tags. A larger corpus requires clarification until SQL ranking is introduced.

## Reproducible Checks And Migrations

Python 3.11 is the supported runtime. `requirements.in` contains the direct dependencies and
`requirements.txt` is the generated, hash-locked install set. Install and verify it with:

```bash
python -m pip install --require-hashes -r requirements.txt
python -m pip check
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
```

Schema changes use Alembic. Apply all migrations before starting the application:

```bash
alembic upgrade head
```

The application does not create tables at startup. After changing SQLAlchemy models, add a
migration and run `alembic check` against an up-to-date database before opening a pull request.

The security workflow runs secret, dependency, static, source/configuration, container, and ZAP
dynamic scans with the approved release gates. The threat model, exact policy, versions, and
evidence map are in
[`docs/application-security.md`](docs/application-security.md).

## WhatsApp Transport

The approved Meta Graph API version is `v26.0`. Configure `META_APP_SECRET`,
`META_ACCESS_TOKEN`, `META_PHONE_NUMBER_ID`, and a private `META_VERIFY_TOKEN` outside Git. The
Meta callback URL is:

```text
https://<public-host>/webhooks/meta
```

Meta must be able to reach that URL over HTTPS; a localhost URL cannot be verified. Keep
`META_SEND_ENABLED=false` until an authorized test window. After migrations are current, run the
outbound worker separately:

```bash
python -m app.workers.whatsapp
```

The webhook stores each unique inbound event before acknowledging it. The worker processes that
durable inbox and commits conversation changes, tickets and queued replies together. It sends replies
with bounded retries for explicit rejections; uncertain sends await delivery reconciliation.
Permanent or exhausted failures enter a dead-letter state. Raw HTTP access logging is disabled because Meta's
verification request includes the private verify token in its query string.

## Secure media

Set `MEDIA_PROCESSING_ENABLED=true`, `MEDIA_BUCKET`, and the ClamAV connection values only after
the private bucket and scanner are ready. Production requires the AWS Malaysia region and rejects
an S3 endpoint override. Run the worker separately:

```bash
python -m app.workers.media
```

The application uses the EC2 instance role for S3 credentials; do not create access keys for
production. The bucket remains private and uses server-side encryption. Media is never sent to
the hosted LLM.

## Administration and ticket operations

Provision the first named administrator from a trusted operator shell after migrations. Omit
`--actor` only for this bootstrap account; every later action authenticates and audits the acting
administrator:

```bash
python scripts/manage_admin.py provision czeyik \
  --display-name "Cze Yik" --email <address> --phone-number <number> \
  --recovery-approver --notify-urgent
python scripts/manage_admin.py provision jane \
  --display-name "Jane" --email <address> --phone-number <number> --actor czeyik \
  --cco --notify-new --notify-urgent
```

The command prints each authenticator URI and TOTP mapping once. Store the mapping in AWS Secrets
Manager and inject it as `ADMIN_TOTP_SECRETS`; it is never stored in the database. Disable or
recover an account with `manage_admin.py disable` or `manage_admin.py recover` and an authenticated
`--actor`. Recovery is limited to the designated recovery approver, rotates both credentials, and
invalidates existing sessions.

Ticket changes create attributable audit events and durable notification records. Configure SMTP
and approved Meta template names, then enable `NOTIFICATION_SEND_ENABLED` and run:

```bash
python -m app.workers.notifications
```

WhatsApp delivery also obeys `META_SEND_ENABLED`; both switches remain off until an authorized
test or traffic window.

## Privacy and retention

Preview or run one bounded lifecycle pass with:

```bash
python -m app.workers.retention --dry-run
python -m app.workers.retention --once
```

Production runs the worker daily. Backups expire after at most 35 days, and every restore must
complete a zero-failure `--once` pass before traffic is enabled. Legal holds are managed only by
the named privacy owner through `scripts/manage_legal_hold.py`. The inventory and runbook are in
[`docs/privacy-data-lifecycle.md`](docs/privacy-data-lifecycle.md).

## Production platform

Production uses one AWS account with isolated staging and production CloudFormation stacks. The
initial production host is an ARM64 EC2 `t4g.small`; the public-beta AWS ceiling is USD 30 per month and any
promotion to `t4g.medium` requires explicit owner approval. Runtime access uses an EC2 role and
Systems Manager, secrets stay in Secrets Manager, and releases promote immutable ECR digests while
outbound traffic is governed by production feature switches. The architecture, deployment, restore,
rollback, alerting, and capacity gates are in
[`docs/production-platform.md`](docs/production-platform.md).

## Try The Chat API

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "web",
    "external_user_id": "demo-user-1",
    "text": "Why did my fare change?",
    "user_role": "rider"
  }'
```

Start a complaint and obtain the response's consent `prompt_id`:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "web",
    "external_user_id": "demo-user-1",
    "text": "I want to complain because I was overcharged for my trip",
    "user_role": "rider"
  }'
```

Reply with that `prompt_id`, consent and the required contact details to submit:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "web",
    "external_user_id": "demo-user-1",
    "text": "Yes, submit my overcharge complaint with these details.",
    "prompt_id": "<consent-prompt-id>",
    "name": "Demo Rider",
    "email": "demo@example.com",
    "phone_number": "+60123456789",
    "account_id": "DUDU123",
    "trip_id": "TRIP456",
    "consent_to_ticket": true,
    "create_ticket": true
  }'
```

## Hosted LLM Direction

The LangChain release candidate uses one bounded `create_agent` dialogue agent.
Current acceptance and deployment status: [SMART.md](SMART.md). Model choices:

1. GLM-5.3-Flash as the approved hosted model.
2. The deterministic approved-knowledge responder during provider outages or rejected output.
3. DeepSeek V4 Flash retained only as an evaluated alternative, not a pilot provider.

Set `LLM_ENABLED=true` and provision `ZAI_API_KEY` outside Git. With customer context enabled,
the agent receives approved knowledge, minimized messages and opaque field references. Seven
Python business tools stage validated changes; PostgreSQL commits them after authorization checks.
Each turn allows five model requests and ten native tool calls, including the structured final
response, within 60 seconds; each request is capped at 30 seconds, 15,000 input characters and
300 output tokens. Invalid, unsafe, ungrounded or failed output uses the local recovery path.

Run the repeatable trilingual outage evaluation with:

```bash
python scripts/release_eval.py --suite smart --mode outage --input-price 0.15 --output-price 0.50
```

The authorized hosted-model command, evidence fields, go/no-go record, and activation procedure
are in [`docs/release-validation.md`](docs/release-validation.md).

## Safety Gate

Before enabling real rider, driver, or business-partner traffic, complete
[`docs/security-launch-checklist.md`](docs/security-launch-checklist.md).
