# DUDU Car AI Support Chatbot Requirements

Last updated: 15 September 2026

This is the authoritative product baseline.

## Scope

- Serve DUDU Car riders, drivers, and business inquiries on WhatsApp in English, Bahasa Malaysia,
  and Simplified Chinese.
- Answer only from active CCO-approved knowledge. State uncertainty and offer a support ticket
  instead of guessing.
- Identify as an automated assistant. Use warm routine wording, empathetic complaint wording, and
  calm, direct safety wording. For immediate danger, direct the user to emergency services first.
- Remain informational. Never perform refunds, cancellations, bookings, payments, account lookups
  or changes, driver decisions, bans, approvals, or contractual commitments.

## Tickets and media

- Explicit consent, name, email, WhatsApp number, and a brief issue description are required before
  ticket creation. Collect ride/account details only when relevant.
- First-human-response targets are normal 3–5 days, high 1–3 days, and urgent within 24 hours.
  Acknowledgements must state the public ticket ID, priority, target, and support hours.
- Human support operates 9:00 AM–6:00 PM daily in Malaysia time; never advertise 24/7 human support.
- Accept JPEG/PNG up to 5 MB and MP4/3GP up to 16 MB. Scan and privately store media outside
  PostgreSQL. Do not send media to the hosted model.
- Reject or redact payment cards, passwords, OTPs, API secrets, identity numbers/documents, and
  unnecessary sensitive data.

## Knowledge and model

- Only the named CCO may publish, replace, remove, or roll back knowledge. Every change is
  authenticated, versioned, attributable, and audited.
- The LangChain release candidate uses hosted `glm-5.3-flash` through one bounded `create_agent`.
  Current deployment status is recorded in [SMART.md](../SMART.md). It receives approved
  knowledge, the minimized current question, and bounded sanitized conversation context after the
  privacy activation gate is approved. Raw identifiers, ticket bodies, attachments and full history
  remain excluded. Seven scoped tools stage knowledge/draft/case operations; local code validates
  consent, ownership and all mutations before PostgreSQL commits them atomically.
- Unsafe, ungrounded, invalid, or unavailable model output uses the deterministic approved-knowledge
  fallback.

## Administration, security, and privacy

- Use multiple named administrators with individual credentials, 2FA, active/disabled state,
  session revocation, and attributable audit records. The admin interface may be internet-accessible.
- Require HTTPS, Meta signature validation and message deduplication, secure cookies and CSRF,
  shared rate limits, least privilege, encryption, safe logs, and managed secrets.
- Permanently delete chat copies after 90 days and closed tickets with attachments after 36 calendar
  months. Backups expire within 35 days. Only the privacy owner may apply a narrow, expiring,
  audited legal hold.

## Acceptance

- Required trilingual answer, refusal, safety, escalation, ticket, media, admin, retention, outage,
  security, restore, rollback, and traffic-limit checks pass.
- At least 95% of valid messages receive exactly one accepted response within 30 seconds; no
  unresolved P0/P1 or unapproved waiver remains.
- Real traffic is enabled only under the limits, budget, approvals, and rollback conditions in
  `docs/launch-contract.md` and `docs/release-validation.md`.
