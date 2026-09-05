# CRM visual audit — 5 September 2026

Each checked route was freshly rendered at 390 × 844 and 1440 × 1000 after its latest template/CSS change. Screenshots are local QA artefacts and are not committed because rendered CRM pages can contain customer information.

## Phase 1 — primary work routes

| Route | Phone | Desktop | Overflow | Manual visual result |
| --- | --- | --- | --- | --- |
| `/dashboard` | checked | checked | none (390/390, 1440/1440) | Warm workspace, compact empty state, balanced 2×2 Quick Actions |
| `/customers` | checked | checked | none (390/390, 1440/1440) | Photo/dark hero removed; high-contrast heading and balanced actions |
| `/send-contact-form` | checked | checked | none (390/390, 1440/1440) | Compact ivory two-step customer-details sender |
| `/jobs` | checked | checked | none (390/390, 1440/1440) | High-contrast heading; paired primary/secondary mobile actions; compact filters |
| `/calendar` | checked | checked | none (390/390, 1440/1440) | Compact schedule heading and controls; month grid remains readable |
| `/intake-forms` | checked | checked | none (390/390, 1440/1440) | High-contrast heading; paired booking/form actions; clear empty state |

Phase screenshots: `audit-g1-*-phone-final2.png` and `audit-g1-*-desktop-final2.png` in the local temporary QA directory.

## Remaining phases

- Quotes, invoices and communications/forms
- More-menu operational pages
- Settings and configuration subpages
- Available create/edit/detail views using safe local records or isolated test data
