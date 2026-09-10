# Application Security Policy

Last reviewed: 10 September 2026
Owner: Cze Yik

- Store production secrets in AWS Secrets Manager and inject them at deployment.
- Use PostgreSQL for shared atomic abuse controls. Store only SHA-256 identity digests, expire
  buckets, and fail closed at `RATE_LIMIT_MAX_KEYS`.
- Require Meta HMAC verification before writes and unique provider message IDs.
- Protect admin operations with named accounts, individual 2FA, expiring signed sessions, secure
  cookies, CSRF, revocation, and role checks.
- Bound and validate inputs; use parameterized ORM queries, escaped templates, restrictive browser
  headers/CORS, private scanned media, and redacted logs.
- Give the hosted model approved knowledge only, no customer data or tools. Reject prompt injection,
  prohibited actions, and unsafe or ungrounded output.
- Gitleaks, pip-audit, Bandit, Trivy, and OWASP ZAP run on pull requests, `main`/`dev`, weekly, and on
  demand. Secrets, dependency advisories, high/critical image or dynamic findings, and Bandit
  high-severity/high-confidence findings block release. Any exception must name an owner and expiry.

## Evidence

| Control | Evidence |
| --- | --- |
| Webhook authenticity and replay | `tests/test_whatsapp_transport.py`, `tests/test_media_pipeline.py` |
| Abuse limits and admin login | `tests/test_application_security.py`, `tests/test_admin_ticket_operations.py` |
| Sessions, CSRF, and authorization | `tests/test_admin_ticket_operations.py`, `tests/test_knowledge_governance.py` |
| Injection, output, and data leakage | `tests/test_application_security.py`, `tests/test_language_pii_guardrails.py`, `tests/test_llm.py` |
| Media isolation | `tests/test_media_pipeline.py`, `tests/test_media_integrations.py` |
| Configuration and scanners | `tests/test_config.py`, `.github/workflows/security.yml` |

The beta uses PostgreSQL rather than Redis. A global lock admits new limiter identities; existing
identities lock independently. Move this policy to a sharded or managed edge limiter if first-contact
throughput exceeds the approved beta load.

Release-specific exceptions are recorded in `docs/release-validation.md`.
