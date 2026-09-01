# DUDU Car AI Support Chatbot — Consolidated Requirements

Last updated: 2 September 2026

This document is the authoritative requirements baseline for the project. It consolidates the
requirements review and supersedes earlier assumptions about an Instagram launch, a single
administrator, network/VPN-restricted administration, and unconfirmed support or retention
periods.

## R1 — Purpose and Audiences

- R1.1: Provide a customer-facing support chatbot for DUDU Car riders and drivers.
- R1.2: Also serve business partners and organizations interested in collaborating with DUDU
  Car. The bot may explain approved partnership information, collect an inquiry, and create a
  support ticket, but it must not make commitments or enter agreements on DUDU Car's behalf.
- The primary goals are to answer repetitive questions from approved knowledge, reduce routine
  support load, and improve the quality of information captured for human follow-up.

## R2 — Launch Channel

- R2.1: Launch on WhatsApp first through Meta's WhatsApp webhook/API.
- Instagram and additional messaging channels are future options and are not launch
  dependencies.
- Web and administrator test endpoints may remain available for internal testing, but WhatsApp
  is the only public launch channel.

## R3 — Languages

- Support English, Bahasa Malaysia, and Simplified Chinese.
- Respond in the language used by the user unless the user selects another supported language.
- Preserve the user's language and conversation context when creating a ticket for human
  follow-up.

## R4 — Chatbot Behaviour and Human Escalation

- Answer from knowledge approved by DUDU Car and use cautious, clearly qualified wording.
- Do not invent DUDU Car policies, prices, commitments, support availability, or account facts.
- If an answer cannot be confirmed, say so and offer the ticket flow rather than guessing.
- Acknowledge complaints empathetically and offer the appropriate ticket flow.
- For an immediate safety risk, advise the user to contact local emergency services first and
  offer an urgent ticket after consent.
- The chatbot must identify itself as an automated DUDU Car assistant and must not imply that it
  is a human agent.
- R4.11: When a user explicitly asks for a human, agent, representative, escalation, or human
  follow-up, immediately offer the human-escalation ticket flow. Do not claim that a human is
  immediately available or that human support operates 24/7.
- Defined complaint, safety, uncertainty, and other ticket flows may still offer escalation even
  when the user has not used the word "human."

## R5 — Informational Scope

- The bot remains informational for the current phase. It may answer approved questions and
  perform support intake by creating a ticket after consent.
- It must not execute refunds, cancellations, bookings, account changes, driver approvals,
  suspensions or bans, payments, private account lookups, contractual commitments, or any other
  transaction that changes business or account state.
- The LLM must not be given tools that can perform those prohibited actions.
- When a user requests an out-of-scope action, explain the limitation and offer a support ticket
  where appropriate.

## R6 — Tickets, Response Targets, and Attachments

- Obtain explicit consent before storing issue details in a support ticket.
- Collect the user's name and email address before creating a ticket. Collect other identifiers,
  such as trip or account ID, only when relevant and avoid unnecessary sensitive information.
- Classify tickets as normal, high, or urgent and communicate the expected time to the first
  human response:
  - Normal: 3–5 days.
  - High: 1–3 days.
  - Urgent: within 24 hours.
- These are first-response targets, not promises that the issue will be resolved in that period.
- A ticket acknowledgement must include the public ticket ID, priority, applicable response
  target, and the confirmed human-support hours.
- Support picture and video attachments at launch. Validate file type and size, scan uploads,
  store them securely outside the relational database, and associate them with the ticket.
- Continue rejecting payment cards, passwords, OTPs, identity documents, and other unnecessary
  sensitive uploads.
- Video must always be available to the human reviewer. Automated video analysis is optional and
  may be enabled only where the selected model and privacy configuration support it.

## R7 — Knowledge Governance

- The Chief Communication Officer (CCO) is authorized to approve, publish, correct, and remove
  chatbot knowledge.
- A CCO knowledge change does not require second-person review.
- Every knowledge change must still be authenticated and audited with the actor, timestamp,
  source, action, and version or replacement details.
- Only approved and currently active knowledge may be used for customer answers. Previous
  versions must be recoverable for rollback or audit.

## R8 — Hosted LLM Direction

- Use hosted LLM API calls for production.
- Primary candidate: **GLM-5.3-Flash**, selected for its current intelligence-to-cost ratio and
  native text, image, video, and file inputs.
- Production-safe fallback: **GPT-5.6 Luna**, selected for its speed, concise responses,
  controllable reasoning, mature API surface, and clearer production data controls.
- Evaluated alternative: **DeepSeek V4 Flash**. Retain it in comparative tests, but do not make it
  the launch default unless DUDU-specific evaluation shows a material advantage.
- Access models through a provider-neutral application adapter so the primary and fallback can
  be changed through configuration without rewriting chatbot or ticket logic.
- Ground generated answers in DUDU-approved retrieved knowledge. Provider-hosted web search must
  not replace the approved knowledge base for DUDU policies.
- Minimize personal data sent to an LLM. Keep names, email addresses, attachments, and ticket
  records in DUDU-controlled storage unless a specific model task requires the data.
- If the hosted model is unavailable or returns an unsafe or ungrounded result, fall back to a
  deterministic approved-knowledge response and offer a ticket.
- Run a DUDU-specific evaluation in all launch languages before production. Grounded accuracy,
  hallucination avoidance, escalation behaviour, latency, and actual token cost are release
  criteria.

### R8 Planning Cost Baseline

At the planning assumption of 2,000 input tokens and 300 total output tokens per reply, excluding
media and extra reasoning tokens:

| Model | Planning cost per 1,000 replies | Planning treatment |
| --- | ---: | --- |
| GLM-5.3-Flash | $0.45 at list price | Use list price; do not budget from temporary promotions. |
| GPT-5.6 Luna | $0.76 | Use as the stable fallback baseline. |
| DeepSeek V4 Flash | $0.64 off-peak / $1.28 peak | Budget peak pricing for Malaysia daytime traffic. |

Prices and model behaviour are external dependencies and must be rechecked before procurement
and launch.

## R9 — Administration

- Launch with multiple named administrator accounts.
- Do not use a shared administrator login. Each administrator must have individual credentials,
  individual 2FA, active/disabled status, and attributable audit activity.
- A single Administrator role is sufficient for launch; complex role hierarchies are not
  required. The CCO's knowledge authority must nevertheless be attributable to the CCO's named
  account.

## R10 — Security and Access

- Require HTTPS, secure secret storage, Meta webhook-signature validation, individual admin 2FA,
  secure session handling, rate limits, audit logs, input validation, and encryption in transit
  and at rest.
- Defend against prompt injection, sensitive-data leakage, unsafe output handling, excessive
  agency, malicious uploads, and abusive traffic.
- Redact payment cards, passwords, OTPs, API keys, and identity numbers before storing or sending
  conversational content to an LLM.
- R10.9: Do not require network allowlisting or VPN access for the administrator interface. The
  administrator interface may be internet-accessible when the other authentication, session,
  monitoring, and application security controls are in place.

## R11 — Retention

- Retain chat messages for 90 days, then automatically delete or irreversibly anonymize them.
- Retain tickets and their associated ticket attachments for 24 months, then delete or
  irreversibly anonymize them according to the approved deletion procedure.
- Retention jobs must cover primary storage, attachment storage, indexes, and applicable backups.
- Legal holds or statutory requirements may override normal deletion only when documented and
  authorized.

## R12 — Availability and Support Coverage

- The automated chatbot may operate continuously when the service is available.
- R12.9: Confirmed human coverage is 9:00 AM–6:00 PM, seven days a week, in Malaysia time
  (Asia/Kuala_Lumpur).
- Do not advertise 24/7 human coverage.
- Outside confirmed coverage hours, tell the user that the ticket is queued for the next coverage
  window. The urgent first-response target remains within 24 hours.

## Launch Success and Acceptance

- The bot consistently answers only from approved knowledge or clearly states uncertainty.
- It serves riders, drivers, and business-partnership inquiries in all three launch languages.
- It remains informational and cannot perform prohibited business actions.
- Explicit human requests reliably enter the consent-first ticket flow.
- Ticket contact fields, priorities, response targets, coverage wording, and media attachments
  work end to end.
- Multiple administrators and the CCO knowledge workflow are attributable and audited.
- Automated retention jobs enforce the 90-day and 24-month periods.
- The hosted primary/fallback model configuration passes a representative DUDU evaluation before
  WhatsApp production traffic is enabled.

## Current MVP Gap Notice

The requirements above describe the approved target, not the current implementation state. The
current MVP already provides basic chat, retrieval, tickets, guardrails, Meta webhooks, and an
administrator inbox, but it still requires implementation work for hosted GLM/Luna adapters,
explicit natural-language human escalation, required ticket contact fields, real media storage,
per-ticket response wording, multiple-admin provisioning and per-admin 2FA, CCO knowledge
versioning, and automated retention enforcement.
