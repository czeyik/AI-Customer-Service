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
- Hosted LLM APIs, with GLM-5.3-Flash as the primary candidate and GPT-5.6 Luna as the
  production-safe fallback.
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
- `POST /api/knowledge/documents` ingests approved FAQ/policy chunks.
- `/webhooks/meta` strictly verifies WhatsApp webhooks, deduplicates Meta message IDs, and commits
  each Wave 3 state transition with a durable outbound queue row.
- `/admin` shows the current MVP ticket inbox after password + 2FA login.

## Remaining Work Against The Approved Requirements

The current code predates the consolidated requirements. Before launch it still needs:

- Hosted GLM-5.3-Flash and GPT-5.6 Luna adapters with configurable failover.
- Real image/video upload, scanning, storage, and ticket retrieval rather than attachment metadata
  alone.
- Multiple-admin provisioning, individual administrator 2FA, and CCO-attributed knowledge
  governance.
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

4. In another terminal, seed starter support knowledge:

   ```bash
   docker compose exec api python scripts/ingest_seed.py
   ```

5. Open the API:

   - API docs: http://localhost:8000/docs
   - Health check: http://localhost:8000/health
   - Admin inbox: http://localhost:8000/admin

In development, if `ADMIN_TOTP_SECRET` is empty, the fallback 2FA code is `000000`.
Do not use that fallback for real customer data.

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

Production will use hosted API models through a provider-neutral adapter:

1. GLM-5.3-Flash as the primary candidate.
2. GPT-5.6 Luna as the production-safe fallback.
3. DeepSeek V4 Flash retained only as an evaluated alternative.

The hosted provider adapter has not yet been implemented. Until it is available, the application
uses a deterministic response path grounded in approved retrieved knowledge. Do not assume that
the hosted-model requirement is complete until the primary/fallback integration and evaluation
have passed.

## Safety Gate

Before any real rider, driver, or business-partner pilot, complete
[`docs/security-launch-checklist.md`](docs/security-launch-checklist.md).

The current code is an MVP foundation, not a final production contact-center platform.
