# Wave 9 Application Security

Last reviewed: 9 September 2026

## Approved security contract

Cze Yik is the security and risk-acceptance owner. Production secrets use AWS Secrets Manager;
they are injected at deployment and are not committed or placed in a production environment file.
PostgreSQL is the shared abuse-control store. Its rate-limit rows contain only SHA-256 identity
digests, expire after their configured window, and cannot exceed `RATE_LIMIT_MAX_KEYS`.

Gitleaks, pip-audit, Bandit, Trivy, and OWASP ZAP run on pull requests, `main`/`dev` pushes, a
weekly schedule, and manual dispatch. Verified secrets, every pip-audit advisory, and high/critical
image or dynamic findings block release. Bandit blocks high-severity/high-confidence findings. Medium
findings require documented review. A high/critical exception requires Cze Yik's written,
time-limited acceptance with a remediation owner and date; there are no permanent silent
allowlists.

## Scope and trust boundaries

Protected assets are administrator credentials and sessions, Meta and provider credentials,
customer conversations and contact details, ticket and audit records, approved knowledge, and
private media. The public boundary is the Meta webhook. `/api/chat` is an internal test boundary;
the administrator boundary may be internet-accessible. PostgreSQL, the outbound workers, ClamAV,
private object storage, AWS Secrets Manager, Meta, and Z.AI are separate trust zones.

Threat actors include unauthenticated internet clients, abusive or compromised WhatsApp users,
credential attackers, malicious uploaders, prompt-injection authors, compromised dependencies,
and authenticated administrators exceeding their authority. AWS account compromise and host or
network hardening are platform threats owned by Wave 11.

## Threats, controls, and evidence

| Threat | Application control | Repeatable evidence |
| --- | --- | --- |
| Forged or replayed Meta input | HMAC verification precedes writes; provider message IDs are unique; webhook bodies are capped at 1 MB | `tests/test_whatsapp_transport.py`, `tests/test_media_pipeline.py` |
| Brute force and abusive chat traffic | Shared atomic PostgreSQL counters cover user/IP and admin IP/username; identities are hashed; expired rows are deleted; the active-key cap fails closed | `tests/test_application_security.py`, `tests/test_admin_ticket_operations.py` |
| Session theft, fixation, or cross-site mutation | Signed expiring sessions, individual auth-version revocation, HttpOnly/SameSite/production-Secure cookies, POST logout, and CSRF on every authenticated mutation | `tests/test_admin_ticket_operations.py`, `tests/test_knowledge_governance.py` |
| Broken authorization | Active named-admin dependency protects tickets/media; CCO authorization protects knowledge mutations | `tests/test_admin_ticket_operations.py`, `tests/test_media_pipeline.py`, `tests/test_knowledge_governance.py` |
| Injection or unsafe browser output | Pydantic bounds, parameterized ORM queries, Jinja auto-escaping, restrictive CSP, no framing/sniffing, narrow CORS, and ZAP | `tests/test_application_security.py`, `.github/workflows/security.yml` |
| Prompt injection or excessive agency | Customer text is not sent to the hosted model; injection phrases are refused; the model has no tools; account-changing requests enter support intake | `tests/test_ticket_intake.py`, `tests/test_chatbot_service.py`, `tests/test_llm.py` |
| Sensitive-data leakage | Cards, credentials, API secrets, Malaysian NRIC-like values, and labelled identity numbers are redacted before message storage; provider logs contain counts only | `tests/test_application_security.py`, `tests/test_language_pii_guardrails.py`, `tests/test_chatbot_service.py`, `tests/test_llm.py` |
| Malicious or unauthorized media | Authenticated bounded streaming, structural sniffing, integrity check, quarantine, ClamAV, private storage, active-admin review, and five-minute links | `tests/test_media_pipeline.py`, `tests/test_media_integrations.py` |
| Unsafe configuration or secret exposure | Production validation requires non-default secrets, AWS Secrets Manager, PostgreSQL, explicit hosts, fixed provider/media policy, and no wildcard CORS; docs are disabled and HTTPS redirect/HSTS are enabled | `tests/test_config.py`, `tests/test_application_security.py`, Gitleaks and Trivy jobs |
| Vulnerable code, dependencies, or image | Hash-locked runtime dependencies, non-root digest-pinned image, static/dependency/image scans, and weekly re-scan | `.github/workflows/ci.yml`, `.github/workflows/security.yml` |

Bandit's five medium findings were reviewed. The Z.AI and WhatsApp endpoints are fixed HTTPS
constants; Meta media download URLs are restricted to Facebook HTTPS hosts before use. Website
ingestion now validates the initial URL and every redirect before following it, caps responses at
2 MB, and rejects DTD/entity sitemap declarations before bounded ElementTree parsing. None gives
an attacker an accepted arbitrary scheme, host, or XML entity path, so no risk exception or broad
scanner suppression is recorded. The workflow prints medium findings for review and separately
enforces the approved high-severity/high-confidence gate.

ZAP reported no high-risk alert. Its lower-risk findings were limited to cacheability on
development docs/404 responses, the development Swagger UI's external JavaScript and missing
subresource integrity, modern-app detection, and a missing cross-origin embedder policy. API docs
and OpenAPI are disabled in production; administrator pages already use `no-store`, and the
application serves no cross-origin embedded resources in production. No waiver is required.

The initial dependency audit found 17 known advisories, all removed by updating the affected
runtime/test packages. The initial Debian-based image scan found 54 high/critical operating-system
findings and two vulnerable Python build tools. The runtime now uses the digest-pinned Alpine
variant, upgrades its fixed runtime package, and removes build-only packaging tools. Final
Gitleaks history, pip-audit, Bandit high/high, Trivy source/image high/critical, and ZAP high-risk
gates all report zero blocking findings. No Wave 9 risk waiver is open.

## Residual boundaries

The invitation-only pilot deliberately uses PostgreSQL rather than a new Redis service. A global
lock applies only while admitting a previously unseen limiter identity; existing identities lock
independently. If first-contact throughput outgrows the pilot, shard that admission lock or move
the same policy to a managed edge limiter.

TLS termination, AWS resource policies, runtime secret injection, host hardening, production
monitoring, and network controls remain Wave 11. Retention, legal holds, and backup lifecycle
remain Wave 10/11. Authenticated ZAP coverage of administrator workflows is repeated against the
dark production release candidate in Wave 12; Wave 9 covers the unauthenticated application
surface plus direct authorization and CSRF tests.

## Tool verification

Official project documentation and releases were checked on 9 September 2026. The workflow pins
Gitleaks 8.28.0, pip-audit 2.10.1, Bandit 1.9.4, Trivy Action 0.36.0 by commit running Trivy
0.74.0, and ZAP 2.17.0 by multi-architecture image digest. ZAP's baseline exit semantics reject
high-risk alerts while retaining lower-risk findings for review.

- https://github.com/gitleaks/gitleaks/releases/tag/v8.28.0
- https://github.com/pypa/pip-audit/releases/tag/v2.10.1
- https://github.com/PyCQA/bandit/releases/tag/1.9.4
- https://github.com/aquasecurity/trivy-action/releases/tag/v0.36.0
- https://www.zaproxy.org/docs/docker/baseline-scan/
