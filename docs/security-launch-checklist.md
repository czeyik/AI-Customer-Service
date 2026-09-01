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
- Configure hosted LLM access with GLM-5.3-Flash as primary and GPT-5.6 Luna as fallback. Confirm
  provider contracts, data handling, failover, timeout, and spend limits before enabling traffic.
- Confirm that names, email addresses, ticket records, and attachments remain in DUDU-controlled
  storage unless a specific approved model task requires them.
- Implement and test automatic chat deletion or anonymization at 90 days.
- Implement and test ticket and attachment deletion or anonymization at 24 months, including
  indexes and applicable backups.
- Confirm the system redacts payment cards, passwords, OTPs, API keys, and identity numbers before storage.
- Confirm the bot refuses account-changing actions.
- Confirm the bot accepts launch image/video types, enforces size limits, scans files, stores them
  securely, and refuses sensitive uploads such as payment cards and identity documents.
- Run prompt-injection tests against the chat endpoint.
- Test rider, driver, business-partner, safety, fraud, payment, account, complaint, explicit human
  escalation, and normal FAQ scenarios in all launch languages.
- Confirm tickets cannot be created until consent, name, and email have been collected.
- Confirm ticket acknowledgements state the correct first-response target: normal 3–5 days, high
  1–3 days, and urgent within 24 hours.
- Confirm customer wording states human coverage as 9:00 AM–6:00 PM every day in Malaysia time and
  never advertises 24/7 human coverage.
- Confirm the CCO can publish and update knowledge without second-person approval and that every
  change remains authenticated, versioned, attributable, and auditable.
- Review the current Meta WhatsApp messaging and media policies before production launch.
