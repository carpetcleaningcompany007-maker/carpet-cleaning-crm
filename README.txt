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

Inbound customer Gmail capture
------------------------------

If the Gmail address and app password in CRM Settings are valid for Gmail IMAP, the existing background runner checks the INBOX read-only about every five minutes. It uses BODY.PEEK and never marks messages read, moves them, deletes them or replies. Exact sender-email matches link to the existing customer and the latest safely related enquiry/job; unmatched senders remain in Customer Email Inbox for manual review rather than being guessed.

Only clean plain text is retained. HTML is converted to text. JPEG, PNG, HEIC/HEIF and PDF attachments are accepted up to 8 MB each and 20 MB total per message, stored on the private Render disk and served only through logged-in CRM routes. Message-ID provides idempotent deduplication. Customer email content never appears in logs or push notification bodies.

If Gmail rejects the read-only inbox login, confirm IMAP access is enabled for the Gmail account and create a current Google App Password under the account's 2-Step Verification settings, then save it in CRM Settings. Do not place it in source control or a Render build log. The already configured AUTOMATION_SECRET can optionally authorize `POST /inbox/poll` with `Authorization: Bearer ...` from a Render Cron Job; the built-in runner means this is optional while the web service is awake.
SOCIAL POST STUDIO (SETUP-ONLY PHASE)
------------------------------------
- Open More > Social Post Studio to create private Facebook/Instagram-ready drafts.
- The studio supports Google review, before-and-after, offer and expert-tip concepts, with editable image wording, caption, phone preview, destination intent and preferred time.
- A post can be saved as Draft or Ready for approval. Customer/review material requires an explicit appropriateness confirmation, and the same review cannot enter the approval queue twice.
- Meta is deliberately disconnected in this phase. There are no OAuth tokens, provider API calls, background publishing jobs or development/test posts. "Approve & schedule" remains disabled.
- A later connection requires a Facebook Page administrator, a linked Instagram Professional account and Meta OAuth. Provider tokens must be encrypted at rest using the CRM encryption key and never returned to the browser or logs.
