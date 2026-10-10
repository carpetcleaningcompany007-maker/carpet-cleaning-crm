# Pedro’s profit workbook

Open https://carpet-cleaning-crm.onrender.com/profit and sign in with the supplied calculator credentials. This login grants access only to the profit workbook, not CRM records.

Figures save automatically on the existing Render persistent disk in `profit-workbook.db`. New periods are separate records; editing an existing period updates it without duplicating its contribution to the running total. Avoid overlapping periods. The progress chart shows profit or loss for each period, with an overall running total alongside it.

Use **Totals** for a quick estimate or **Jobs** to calculate from booked/completed jobs. Prices are required for counted jobs. Empty job hours use the period’s default hours. Advertising can be entered as the total for the period, a daily amount or a cost per lead. Provider fees can be switched off or entered for the exact period. Monthly fees use a four-week planning month. Earnings per hour are net profit divided by entered job hours. Figures are before tax and costs not entered; booked revenue is not necessarily cash collected.

Download backups from the footer. Restore replaces the workbook after confirmation. Multiple-device edits are protected by revision checks; reload before editing again when a conflict occurs.

## Website integration

Add this button to an owner/staff page on the website:

```html
<a href="https://carpet-cleaning-crm.onrender.com/profit" target="_blank" rel="noopener">Open profit workbook</a>
```

The calculator works directly on phones. In Safari use Share → Add to Home Screen for a shortcut. The existing CRM security policy blocks iframe embedding; use the link above for reliable login across browsers.

The example first period starts with 20 enquiries, six £140 jobs, £300 total advertising, £20 job expenses, £300/month advertising management and £500/month calls over two weeks: £840 income, £820 costs, £20 profit. Replace these example figures with actuals.
