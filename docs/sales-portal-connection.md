# Conversation Desk AI connection

The separate private Conversation Desk site can POST signed draft requests to
`/api/sales-portal/draft`. It uses the CRM's existing OpenAI key and configured
model. The key never leaves Render. The gateway cannot send SMS, read customer
records, or modify bookings. Successful requests appear in existing AI usage totals.

The pinned `sales_portal_public.pem` verifies RSA SHA-256 signatures. The private
PKCS8 key is stored only as a secret in the Conversation Desk hosting settings.
Signed bytes are `POST\n/api/sales-portal/draft\n<unix seconds>\n<nonce>\n<body>`.
Timestamps expire after 120 seconds. Nonces are claimed in SQLite and retained
for an hour; requests are limited to 120 per hour. Request bodies are limited
to 40 KB, output to 1800 tokens, and OpenAI storage and tools are disabled.

To revoke this integration, remove the route registration or replace its public
key and deploy. Rotate the corresponding private key in the portal when replacing
the public key. Never commit private keys. Existing CRM assistant intake tokens
and OpenAI credentials are not shared with the portal.
