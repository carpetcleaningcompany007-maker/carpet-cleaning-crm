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
| `/business-goals` | checked | checked | none (390/390, 1440/1440) | Legacy dark hero removed; ivory goal workspace, compact target card and readable metric/action flow |
| `/quotes` | checked | checked | none (390/390, 1440/1440) | Ivory library heading, compact three-stat row and balanced paired actions |
| `/invoices` | checked | checked | none (390/390, 1440/1440) | Ivory library heading, compact stats and intentional primary-plus-secondary action grid |
| `/communications` | checked | checked | none (390/390, 1440/1440) | Ivory hub heading, compact stats/shortcuts and readable one-off send workflow |
| `/communication-automation` | checked | checked | none (390/390, 1440/1440) | Compact settings heading and full rule controls retained without broken mobile actions |
| `/notifications` | checked | checked | none (390/390, 1440/1440) | Clear phone setup steps, alert controls and action-centre state |
| `/settings` | checked | checked | none (390/390, 1440/1440) | High-contrast settings heading and readable grouped configuration cards |
| `/message-settings` | checked | checked | none (390/390, 1440/1440) | High-contrast message heading and intact editable template cards |
| `/feedback` | checked | checked | none (390/390, 1440/1440) | Purpose-built mobile feedback cards replace the desktop table on phones |

Phase screenshots: `audit-g1-*-phone-final2.png`, `audit-g1-*-desktop-final2.png` and `audit-goals-{phone,desktop}.png` in the local temporary QA directory.

## Remaining phases

- Remaining communications forms and social pages
- More-menu operational pages
- Settings and configuration subpages
- Available create/edit/detail views using safe local records or isolated test data
