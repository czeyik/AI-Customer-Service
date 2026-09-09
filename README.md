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

## What Works In This MVP

- `POST /api/chat` accepts support messages and returns multilingual answers.
- The bot uses approved knowledge chunks before answering.
- If the bot is uncertain, it offers ticket creation instead of guessing.
- Complaints and safety issues enter a consent-first ticket flow.
- Prompt-injection attempts and account-changing requests are refused.
- Sensitive uploads and risky secrets are rejected or redacted.
- Named CCO sessions can draft, publish, remove, and roll back versioned knowledge through
  `/api/knowledge/documents`; inactive versions never reach customer answers.
- `/webhooks/meta` strictly verifies WhatsApp webhooks, deduplicates Meta message IDs, and commits
  each Wave 3 state transition with a durable outbound queue row.
- `/admin` provides the named-administrator ticket inbox, assignment, status, and internal-note
  workflow after password + individual 2FA login.

## Remaining Work Against The Approved Requirements

The current code predates the consolidated requirements. Before launch it still needs:

- DUDU-specific trilingual release evaluation of the hosted model and outage fallback.
- Real image/video upload, scanning, storage, and ticket retrieval rather than attachment metadata
  alone.
- Automated deletion or anonymization after 90 days for chats and 36 months for tickets and
  ticket attachments.

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

The importer accepts only HTTPS pages on `duducar.co`, reads at most 100 sitemap URLs and 2 MB per
page, and removes scripts, navigation, forms, and footers. Jane must review and activate each
language version through her authenticated session.

Knowledge management uses Jane's normal named-admin session, not a shared API key. A CCO client
can obtain its CSRF token from `GET /api/knowledge/session`, list the bounded version history at
`GET /api/knowledge/documents`, and use the documented publish, activate, remove, and rollback
endpoints. Every mutation records Jane's account, source URI, version, and replacement details.
Customer retrieval queries only effective `active` versions and uses the indexed PostgreSQL
trigram candidate search before bounded in-process ranking.

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

The webhook never calls Meta inline. It stores the unique inbound message ID, Wave 3 state change,
and reply together; the worker sends queued replies with bounded retries and moves permanent or
exhausted failures to a dead-letter state. Raw HTTP access logging is disabled because Meta's
verification request includes the private verify token in its query string.

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

Create a complaint ticket after consent:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "web",
    "external_user_id": "demo-user-1",
    "text": "I want to complain because I was overcharged for my trip",
    "user_role": "rider",
    "name": "Demo Rider",
    "email": "demo@example.com",
    "account_id": "DUDU123",
    "trip_id": "TRIP456",
    "consent_to_ticket": true
  }'
```

## Hosted LLM Direction

Production uses the hosted model through a provider-neutral adapter:

1. GLM-5.3-Flash as the approved hosted model.
2. The deterministic approved-knowledge responder during provider outages or rejected output.
3. DeepSeek V4 Flash retained only as an evaluated alternative, not a pilot provider.

Set `LLM_ENABLED=true` and provision `ZAI_API_KEY` outside Git. Calls have an eight-second timeout,
bounded input/output, no tools, and receive approved knowledge rather than customer messages.
Invalid, unsafe, ungrounded, or failed responses use the deterministic approved-knowledge path.

## Safety Gate

Before any real rider, driver, or business-partner pilot, complete
[`docs/security-launch-checklist.md`](docs/security-launch-checklist.md).

The current code is an MVP foundation, not a final production contact-center platform.
