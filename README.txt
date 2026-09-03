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
