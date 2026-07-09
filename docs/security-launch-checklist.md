# Security Launch Checklist

Do not use the system with real customers until these items are complete.

- Set a strong `SECRET_KEY`, `ADMIN_INITIAL_PASSWORD`, `ADMIN_API_KEY`, and `META_VERIFY_TOKEN`.
- Set `ADMIN_TOTP_SECRET` and verify admin login requires real 2FA.
- Set `META_APP_SECRET` so webhook signatures are verified.
- Run the API behind HTTPS.
- Restrict admin access by network/VPN if possible.
- Confirm chat and ticket data retention policy with the business owner.
- Confirm the system redacts payment cards, passwords, OTPs, API keys, and identity numbers before storage.
- Confirm the bot refuses account-changing actions.
- Confirm the bot refuses sensitive uploads such as payment cards and identity documents.
- Run prompt-injection tests against the chat endpoint.
- Test safety, fraud, payment, account, complaint, and normal FAQ scenarios in all launch languages.
- Confirm 24/7 urgent support exists before advertising urgent human follow-up.
- Review Meta WhatsApp and Instagram messaging policies before production launch.

