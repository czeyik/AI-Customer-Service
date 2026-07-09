# DUDU Car AI Customer Service Chatbot

Beginner-friendly MVP for a secure customer-support chatbot for DUDU Car riders and drivers.

The project is built around the requirements interview:

- WhatsApp + Instagram first.
- English, Bahasa Malaysia, and simplified Chinese.
- FAQ deflection as the main success metric.
- Complaint handling through structured ticket creation.
- Safety and high-risk issues routed to urgent/high-priority support tickets.
- Local/on-prem open-source LLM path through Ollama.
- PostgreSQL, Docker Compose, and a simple protected admin inbox.

## What Works In This MVP

- `POST /api/chat` accepts support messages and returns multilingual answers.
- The bot uses approved knowledge chunks before answering.
- If the bot is uncertain, it offers ticket creation instead of guessing.
- Complaints and safety issues enter a consent-first ticket flow.
- Prompt-injection attempts and account-changing requests are refused.
- Sensitive uploads and risky secrets are rejected or redacted.
- `POST /api/knowledge/documents` ingests approved FAQ/policy chunks.
- `/webhooks/meta` verifies and receives WhatsApp/Instagram-style webhook payloads.
- `/admin` shows a simple ticket inbox after password + 2FA login.

## Quick Start With Docker

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

## Local LLM

The app has an Ollama adapter but `LLM_ENABLED=false` by default. This lets the rest of the
support system work even before the GPU server is ready.

When Ollama and a model are available, update `.env`:

```env
LLM_ENABLED=true
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
```

Then start the optional Ollama service:

```bash
docker compose --profile llm up --build
```

For a 12-16GB VRAM server, start with a smaller quantized multilingual model and evaluate it
against real anonymized support chats before exposing it to customers.

## Safety Gate

Before any real customer or driver pilot, complete
[`docs/security-launch-checklist.md`](docs/security-launch-checklist.md).

The current code is an MVP foundation, not a final production contact-center platform.
