# Simplified CRM + enquiry tracker

## One package

Launch with `gunicorn unified_app:app`. The new entrypoint imports the unchanged
original CRM engine and adds the simple home, customer library/profile and native
enquiry report. All existing routes and provider functions remain available.
`SIMPLE_CRM_HOME=1` (default in this entrypoint) opens the simple home from `/`.
`gunicorn app:app` remains the legacy entrypoint.

The navigation has Home, Customers, Messages, Diary, Reminders, Enquiry tracker and
Website visits. Quotes, invoices, settings and the detailed dashboard are under
Other tools. The message composer uses the original forms, CSRF tokens, provider
functions and conversation history. Optional AI writing tools are collapsed.

The WhatConverts-style tracker is now native to this Flask CRM. It reads existing
intake_submissions, customers, jobs, quotes and invoices from one database. It does
not import Pedro's private WhatConverts data or call the vendor API. The separate
Sites dashboard is unchanged; it can remain available during review.

The report includes all-time/30-day/90-day and source/search filters, campaigns,
matched keywords, linked customers and job financial values. It excludes test
leads, attributes each linked job only to its first non-test enquiry, and keeps
unknown financial values distinct from zero. Sales here mean linked invoices
marked Paid in this CRM, not confirmed bank receipts or profit. Missing ad spend
and call recording are explicitly marked unconnected. Website intake and analytics
endpoints, public forms and reminder sending logic are unchanged.

## Existing Render service: two views, one CRM

The approved deployment uses `gunicorn shared_app:app --bind 0.0.0.0:$PORT`
on the existing carpet-cleaning-crm service. It reuses the existing environment,
login, disk and database. Do not change CRM_DB_PATH, credentials, provider settings,
background automation configuration, compute plan or disk size. No second service
or recurring hosting subscription is required for this deployment.

The existing dashboard is the default. `/simple` enables the simple navigation for
that signed-in browser; `/full` returns to the original interface. The original
conversation template is retained byte-for-byte as legacy_conversation.html and
used in full mode. Customers, messages and reminders are shared; edits in either
view affect the same live records. There is one original automation runner.

This is an additional interface, not an isolated test database. The original
app.py is unchanged and no schema migrations are added. To roll back, restore the
previous Render branch `main` at 1535da9518700a8767c16edc52462b889ee2f85a and start
command `gunicorn app:app --bind 0.0.0.0:$PORT`. Keep the same disk/environment.

The optional separate staging blueprint remains available for future use, but
is not part of the approved shared-hosting deployment. Never start another
background worker against the live database or commit customer backups/secrets.

## Verification

- 9 simple-workspace/integration/shared-mode tests passed, including authentication, original
  message links/CSRF, report filters and duplicate-job revenue prevention.
- Original 829 app.py function definitions are unchanged; app.py is byte-for-byte
  the upstream version.
- Customer-conversation tests retain the same pre-existing 1 failure (8 pass).
- Enquiry dashboard tests previously retain 3 pre-existing failures (12 pass).
- Carpet-refresh reminder tests retain the same pre-existing logo assertion
  failure (4 pass). These five failures also occur in the untouched source copy.
- No real emails/texts were sent during verification. Local browser review uses
  synthetic customers with background automation and delivery disabled.

No real email/SMS delivery was exercised by tests. Existing provider and reminder
functions are unchanged. Call recording and ad spend integration remain unconnected.
