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

## Deployment status and staging

This source package is ready for staging; it is not deployed to Render. Render
sign-in is still unavailable in the browser. No live records have been copied.
A separate Render blueprint is provided in render.simplified.yaml. It uses its own
disk/database and disables background automation. Review Render's resource price
before creating the service. Do not point two active background workers at the
production CRM database. Do not copy live messaging credentials into staging.

Live CRM settings include credentials stored in the settings database, not only
environment variables. A production database copy must therefore be sanitised
before any staging worker starts. Establish the staged administrator login yourself
and keep customer data and credentials private. Do not publish a default-credential
CRM or a database backup to a public repository.

When staging is approved, choose a deliberate cutover/data migration. A separate
staging database is not automatically synchronised with live changes. Keep the old
CRM running until the replacement's data, email delivery and reminders are verified.

## Verification

- 7 simple-workspace/integration tests passed, including authentication, original
  message links/CSRF, report filters and duplicate-job revenue prevention.
- Original 829 app.py function definitions are unchanged; app.py is byte-for-byte
  the upstream version.
- Customer-conversation tests retain the same pre-existing 1 failure (8 pass).
- Enquiry dashboard tests previously retain 3 pre-existing failures (12 pass).
- Carpet-refresh reminder tests retain the same pre-existing logo assertion
  failure (4 pass). These five failures also occur in the untouched source copy.
- No real emails/texts were sent during verification. Local browser review uses
  synthetic customers with background automation and delivery disabled.

No provider-backed email/SMS delivery, production-data migration or call recording
has been tested or enabled. Those remain staging/deployment work.
