# Security Launch Checklist

Do not use the system with real customers until these items are complete.

- Set strong application, administrator, provider API, and Meta webhook secrets in an approved
  secret manager; do not use launch secrets from an environment file committed to source.
- Provision multiple named administrator accounts with unique credentials, individual 2FA,
  active/disabled status, and attributable audit events. Do not use a shared administrator.
- Set `META_APP_SECRET` so webhook signatures are verified.
- Run the API and administrator interface behind HTTPS with secure cookies and production session
  settings.
- Do not make network allowlisting or VPN access a launch dependency; validate the
  internet-accessible administrator interface with the other required controls in place.
- Configure hosted LLM access with GLM-5.3-Flash and the deterministic approved-knowledge outage
  fallback. Confirm provider contracts, data handling, timeout, and spend limits before enabling
  traffic.
- Confirm that names, email addresses, ticket records, and attachments remain in DUDU-controlled
  storage unless a specific approved model task requires them.
- Implement and test automatic chat deletion or anonymization at 90 days.
- Implement and test ticket and attachment deletion or anonymization 36 months after ticket
  closure, including indexes and applicable backups.
- Confirm the system redacts payment cards, passwords, OTPs, API keys, and identity numbers before storage.
- Confirm the bot refuses account-changing actions.
- Confirm the bot accepts launch image/video types, enforces size limits, scans files, stores them
  securely, and refuses sensitive uploads such as payment cards and identity documents.
- Run prompt-injection tests against the chat endpoint.
- Test rider, driver, business-partner, safety, fraud, payment, account, complaint, explicit human
  escalation, and normal FAQ scenarios in all launch languages.
- Confirm tickets cannot be created until consent, name, email, WhatsApp contact number, and a
  brief issue description have been collected; collect ride details and supporting evidence only
  where applicable.
- Confirm ticket acknowledgements state the correct first-response target: normal 3–5 days, high
  1–3 days, and urgent within 24 hours.
- Confirm customer wording states human coverage as 9:00 AM–6:00 PM every day in Malaysia time and
  never advertises 24/7 human coverage.
- Confirm the CCO can publish and update knowledge without second-person approval and that every
  change remains authenticated, versioned, attributable, and auditable.
- Review the current Meta WhatsApp messaging and media policies before production launch.

## Repeatable Evidence Map

Status is scoped to the application gate. Deployment, lifecycle, and release checks remain open
for their assigned waves even when the application control is present.

| Checklist control | Status | Evidence |
| --- | --- | --- |
| Secret storage and strong application/provider/Meta secrets | APP_PASS; DEPLOY_OPEN_W11 | Production validation in `tests/test_config.py`; Gitleaks/Trivy in `.github/workflows/security.yml` |
| Named administrators, unique credentials, 2FA, disable/recovery, audit | PASS_W6 | `tests/test_admin_ticket_operations.py` |
| Meta application secret and webhook signatures | APP_PASS; DEPLOY_OPEN_W11 | `tests/test_config.py`, `tests/test_whatsapp_transport.py` |
| HTTPS, secure cookies, sessions, CSRF, and internet-accessible admin controls | APP_PASS; TLS_OPEN_W11 | `tests/test_application_security.py`, `tests/test_admin_ticket_operations.py`, `tests/test_knowledge_governance.py` |
| GLM configuration and deterministic outage fallback | PASS_W5 | `tests/test_config.py`, `tests/test_llm.py`, `tests/test_chatbot_service.py` |
| LLM data minimization | APP_PASS | `tests/test_chatbot_service.py`, `tests/test_llm.py` |
| 90-day chat lifecycle | PASS_W10 | `tests/test_data_lifecycle.py`, `docs/wave-10-privacy-data-lifecycle.md` |
| 36-month ticket/media lifecycle and backups | LIFECYCLE_PASS_W10; BACKUP_RESTORE_PASS_W11 | `tests/test_data_lifecycle.py`; `docs/wave-11-production-platform.md` |
| Payment-card, credential, API-secret, and identity-number redaction | APP_PASS | `tests/test_application_security.py`, `tests/test_language_pii_guardrails.py` |
| Account-changing action refusal | APP_PASS | `tests/test_ticket_intake.py` trilingual prohibited-action cases |
| Media type/size/scan/private storage/sensitive-upload controls | PASS_W8 | `tests/test_media_pipeline.py`, `tests/test_media_integrations.py` |
| Prompt-injection resistance at chat endpoint | APP_PASS | `tests/test_ticket_intake.py::test_chat_api_refuses_prompt_injection` |
| Trilingual rider/driver/partner/safety/fraud/payment/account/complaint/human/FAQ behaviour | APP_PASS; RC_OPEN_W12 | `tests/test_ticket_intake.py`, `tests/test_chatbot_service.py`, Wave 12 evaluation |
| Mandatory consent/contact/description ticket fields | PASS_W3 | `tests/test_ticket_intake.py` plus database constraints |
| Priority and first-response acknowledgement | PASS_W3 | `tests/test_ticket_intake.py::test_acknowledgement_has_priority_target_and_hours` |
| Human hours wording | PASS_W3 | `tests/test_ticket_intake.py::test_outside_hours_wording` and trilingual flow tests |
| CCO authenticated/versioned/audited knowledge changes | PASS_W7 | `tests/test_knowledge_governance.py` |
| Current Meta messaging/media policy review | PASS_W12 | `docs/wave-12-release-validation.md` |

The Wave 9 threat model, approved scan gates, residual boundary, and tool versions are in
`docs/wave-9-application-security.md`.
