# DUDU Car AI Support Chatbot Requirements Summary

This project is a customer-facing support chatbot for DUDU Car riders and drivers.

## Launch Shape

- Channels: WhatsApp and Instagram first, through Meta webhooks.
- Users: riders and drivers.
- Languages: English, Bahasa Malaysia, and simplified Chinese, matching the user's message.
- Primary goal: deflect repetitive FAQs and create better support tickets.
- Scope: answer approved support questions, collect structured complaint details, and create tickets.
- Out of scope at launch: refunds, cancellations, account changes, driver approval, bans, private account data lookup, and other account-changing actions.

## Support Behavior

- Use approved company knowledge plus cautious common-sense support phrasing.
- If uncertain, say so and offer ticket creation.
- Complaints should receive empathetic acknowledgement and a ticket flow.
- Safety incidents should advise local emergency services when appropriate and create urgent tickets once consent is confirmed.
- Ticket acknowledgement includes ticket ID, urgency, and expectation of human review.

## Security And Privacy

- Any real customer/driver pilot must require explicit ticket consent, admin 2FA, redaction of risky secrets, audit logs, rate limits, and secure storage.
- The chatbot must defend against prompt injection, sensitive data leakage, excessive agency, unsafe output handling, and abuse.
- The bot may sound warm and natural, but must not claim to be a human agent.
- Chat logs default to 90-day retention; tickets may be retained longer by policy.

## Technical Direction

- Python/FastAPI backend.
- PostgreSQL with pgvector-ready Docker image.
- Docker Compose for local/on-prem deployment.
- Open-source local LLM through Ollama adapter, disabled by default until model hosting is ready.
- Knowledge ingestion from existing files, docs, and website content after cleanup.
- Simple single-admin inbox for the first version.

