Open the CRM, log in, and use the built in How to Use This CRM page for workflow guidance. Keep a backup before major edits.

Authorised assistant customer intake
------------------------------------

POST /api/assistant/customers lets a trusted assistant integration create a CRM customer directly. It adds an audit timeline entry but does not send email/SMS, create a job, or sync anything to Xero.

Set CRM_ASSISTANT_API_TOKEN to a long random secret in the environment where the CRM runs. Do not put the token in this repository. Generate one with:

    python -c "import secrets; print(secrets.token_urlsafe(32))"

For Render, set CRM_ASSISTANT_API_TOKEN in the service's Environment settings. The render.yaml entry uses sync: false so the secret must be supplied privately. Put the same token in the authorised assistant integration's credential store.

Send JSON with Authorization: Bearer <token> and Content-Type: application/json. Required data is first_name plus last_name (or a multi-word name), and at least one valid UK phone number or valid email address. Optional fields are address, town, postcode, source, tags, and notes.

Example:

    curl -X POST https://carpet-cleaning-crm.onrender.com/api/assistant/customers \
      -H "Authorization: Bearer $CRM_ASSISTANT_API_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"first_name":"Jane","last_name":"Example","phone":"07802 563213","email":"jane@example.com","address":"1 High Street","town":"Ludlow","postcode":"SY8 1AA","source":"Assistant screenshot intake","notes":"Details transcribed from a customer-provided screenshot."}'

New records return HTTP 201 with created: true. A matching email, normalized phone, or matching full name plus postcode returns HTTP 200 with created: false, so retries do not create duplicates. Missing configuration returns 503, bad credentials return 401, and invalid data returns 400.

Installable phone CRM and owner push alerts
-------------------------------------------

The CRM is a Progressive Web App. On iPhone, open the live HTTPS CRM in Safari, tap Share, choose Add to Home Screen, then open Carpet Clean Pro from the new Home Screen icon. Apple permits web push on iPhone only from an installed Home Screen web app. Notification permission is requested only after the signed-in owner taps Enable phone alerts on the Notifications page.

Push delivery is disabled safely until all three Render environment variables are present:

    VAPID_PUBLIC_KEY     URL-safe public application-server key
    VAPID_PRIVATE_KEY    Matching URL-safe private key (secret)
    VAPID_SUBJECT        A contact URI such as mailto:owner@example.com

Generate a fresh VAPID pair with a trusted standards-compliant tool such as `npx web-push generate-vapid-keys`. Put the public and private values directly into Render Environment settings, mark the private value secret, and never commit either private key or a subscription to Git. VAPID_SUBJECT must be a monitored mailto: address or an HTTPS contact URL. Redeploy after adding the variables.

Subscriptions are created only for a logged-in, CSRF-protected owner action. Their endpoint and browser keys are encrypted in the CRM database using CRM_SECRET_KEY-derived encryption; only an endpoint hash is used for lookup. Owners can disable the current device or change categories from Notifications. Push content is deliberately generic and contains no customer name, address, phone, email, message body, bank detail, invoice amount, or other sensitive record data. Tapping an alert opens the relevant authenticated CRM screen.

The existing background runner checks the same live notification sources and deduplicates each alert per device. It covers genuine non-test enquiries, inbound customer SMS replies, due/overdue jobs, overdue invoices, due reminders, and Calendar/Xero sync failures. Push alerts are owner-only and never send or trigger a customer-facing message.
