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
