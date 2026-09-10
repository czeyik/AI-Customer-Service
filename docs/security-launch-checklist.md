# Security Launch Checklist

Complete before enabling real-customer traffic.

| Control | Evidence |
| --- | --- |
| Managed secrets, production validation, HTTPS, secure sessions/CSRF | `tests/test_config.py`, `tests/test_application_security.py` |
| Named admins, individual 2FA, recovery, audit, CCO authorization | `tests/test_admin_ticket_operations.py`, `tests/test_knowledge_governance.py` |
| Meta signatures, deduplication, retries, and current messaging policy | `tests/test_whatsapp_transport.py`, `docs/release-validation.md` |
| Hosted model grounding, minimization, outage fallback | `tests/test_llm.py`, `tests/test_chatbot_service.py` |
| Redaction, prompt injection, prohibited actions | `tests/test_language_pii_guardrails.py`, `tests/test_ticket_intake.py` |
| Consent/contact fields, priorities, response targets, support hours | `tests/test_ticket_intake.py` |
| Trilingual rider, driver, partner, safety, complaint, human, and FAQ flows | `tests/test_ticket_intake.py`, `docs/release-validation.md` |
| Bounded scanned private media and sensitive-upload rejection | `tests/test_media_pipeline.py`, `tests/test_media_integrations.py` |
| 90-day chat and 36-month ticket/media deletion | `tests/test_data_lifecycle.py`, `docs/privacy-data-lifecycle.md` |
| Secret, dependency, static, image, configuration, and dynamic scans | `.github/workflows/security.yml`, `docs/application-security.md` |
| TLS, alarms, backup/restore, rollback, and capacity | `docs/production-platform.md` |

All controls are `PASS` for the approved beta subject to the expiring exceptions in
`docs/release-validation.md`.
