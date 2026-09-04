# CRM security setup

## Required administrator action

Open `/security/setup` while signed in. Replace the factory username and password with a unique username and a password-manager-generated password of at least 14 characters. Saving signs the current browser out.

## Required Render secrets

Set these as private environment variables in Render. Never commit their values:

- `CRM_SECRET_KEY`: at least 32 random bytes (64 hexadecimal characters is suitable). Changing it signs out every browser and invalidates existing signed customer links.
- `CRM_COOKIE_SECURE`: leave this unset on Render; HTTPS in `CRM_PUBLIC_BASE_URL` enables secure cookies automatically. Set it to `0` only for local HTTP development.
- `AUTOMATION_SECRET`: a separate random value used by a Render Cron Job. Call `POST /automation/run-due` with the value in `X-Automation-Secret`; do not put it in the URL.
- `TWILIO_AUTH_TOKEN`: Twilio's real auth token. When present, inbound and status callbacks must have a valid `X-Twilio-Signature`.
- `CLICKSEND_WEBHOOK_SECRET`: optional shared secret for a gateway/proxy that can add `X-Webhook-Secret`. ClickSend accounts that cannot add this header should restrict callbacks at a trusted proxy instead.
- `CRM_ASSISTANT_API_TOKEN`: a separate high-entropy bearer token for the assistant customer-intake API.
- `MFA_BOOTSTRAP_TOKEN`: a separate password-manager-generated emergency value used only from an authorised Render Shell if the administrator loses both the authenticator and every recovery code.

`CUSTOMER_UPDATE_LINK_DAYS` controls customer update-link lifetime and defaults to 30 days.

## Credential rotation

After replacing the factory login, rotate Gmail app passwords, SMS API credentials, Xero and Google Calendar connections, and assistant/automation tokens. Reconnect Xero and Google Calendar through the CRM after revocation.

## Authenticator app and genuine lockout recovery

While signed in, open `/security/two-step`, scan the QR code, confirm one current code, then download the one-time recovery codes. The QR/setup key is shown only while enrollment is incomplete. Recovery codes are stored only as password hashes.

Normal disable/reset requires the current CRM password plus a valid authenticator or recovery code. If the phone and all recovery codes are genuinely lost, an authorised Render administrator may open a private Render Shell and run `flask --app app mfa-emergency-disable --confirm DISABLE-MFA`. The command asks privately for `MFA_BOOTSTRAP_TOKEN`, records the emergency reset in the security audit table, and never prints the token. Sign in and re-enrol immediately.

Because the authenticator secret is encrypted using `CRM_SECRET_KEY`, disable MFA before intentionally rotating that key. If an unplanned key loss makes MFA unreadable, use the audited Render recovery command.

## Residual storage risk

The SQLite database and ZIP backups are not yet encrypted by the application. They include customer records and stored integration tokens. Keep Render account access tightly restricted, download backups only to encrypted storage, and delete obsolete copies. Field/back-up encryption requires a separately planned migration and key-recovery procedure.
