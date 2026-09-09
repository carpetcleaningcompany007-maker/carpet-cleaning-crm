# Business rollout: first implementation boundary

Status: preparation only. The current live CRM is one business, not a shared multi-business service.

## First pilot architecture

Use one separately provisioned Render service and new persistent disk per business. Never clone Paul's database, uploads, environment secrets, inbox, AI writing examples, or sender accounts. Each business needs a unique hostname, session secret, owner login, email/SMS credentials and Xero authorisation. Render team members are hosting administrators, not CRM customers.

## Required before admitting a business

- Fresh database and file storage, verified empty of existing customer data.
- Neutral business branding and message templates: remove hard-coded Paul/name/phone/company references throughout the application before distribution.
- Unique owner login and MFA; no shared default password.
- Business-specific price book; don't seed Paul's rates as their agreed prices.
- Separate email and SMS connections, test sends only to that owner's verified number.
- Incoming mailbox connection and exact customer matching. Customer inbox defaults to matched replies; unmatched mail remains separately accessible.
- Separate backups with a tested restore and account shutdown/export procedure.
- Background automation disabled during onboarding; enable only after message previews and destinations are checked.
- Cross-installation checks for cookie/session secrets, database paths, uploads, form links and provider callbacks.

## Shared SaaS phase

Move to PostgreSQL and private object storage, then add organisations, memberships and roles. Enforce organisation scope on every record/query, upload/download, token, webhook, background job and provider credential. Test cross-organisation denial before inviting tenants. Add Gmail/Microsoft OAuth, token encryption/rotation, invitations, password recovery, billing and account lifecycle. Do not expose a registration page against the current shared database.

## Hosting constraints verified 9 September 2026

Live service: Starter, 512 MB RAM, 0.5 CPU, 1 GB persistent disk. Memory chart approximately 30% at inspection, not a capacity guarantee. Disk-backed services cannot run multiple instances; RAM is not a supported-user count. Load testing and storage growth measurements are needed before promising capacity.

Reference: https://render.com/docs/scaling

## Saved direction from Paul — 9 September 2026

Immediate priority: get the existing Carpet Cleaning Manager stable and finish the current usability and communication-delivery issues. Production rollout is paused, not cancelled. Do not enable public signup or migrate the live business as part of routine repairs.

Future goal: sell this CRM to multiple independent carpet-cleaning businesses. The intended production destination is a shared application with separate, securely isolated business workspaces, rather than indefinitely maintaining a separate application for every customer. Separate installations are an optional early pilot bridge, not a final architecture decision.

Each business must be able to manage its own branding, staff, customers, jobs, pricing by cleaning method, minimum charges, quotes/invoices, editable payment footer, AI writing examples, email/SMS accounts and Xero connection. Onboarding should use simple guided steps and connection checks. Incoming customer replies must be separated from unrelated mailbox messages. Provider acceptance, confirmed delivery, failure and missing receipts must remain distinct.

Before a production rollout: audit the existing code and data model; implement and test business isolation; migrate with backups and a tested rollback; test signup/invitations, permissions, message routing, background tasks and file access; measure load and storage; add monitoring, restore procedures and support/account lifecycle. Do not claim capacity from current idle RAM alone. Keep Paul's customer data and credentials out of demo or new-business environments.

Earlier planning estimates of 2–4 weeks for a limited pilot and 6–12+ weeks for a production offering are provisional, not commitments. Re-estimate after inspecting the implementation. Codex usage cannot be reliably predicted as a percentage or described as Render memory.
