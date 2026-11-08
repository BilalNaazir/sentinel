# Labeling guide

These rules decide every label in `data/eval_dataset.v1.json`. They are also built into `prompts/classification.yaml` and `prompts/extraction.yaml`. Change them here first, then update the prompts and bump the dataset version.

## Categories

| Category | Use it when | Examples |
| --- | --- | --- |
| billing | The customer's own charges, refunds, invoices, payment methods, plan price changes, credits or tax | Double charge, VAT invoice, SLA credit |
| technical | Something in the product is broken: errors, crashes, bugs, outages, integrations or the API failing, slowness, a whole team unable to log in through SSO | 502 errors, Slack sync stopped, 429s from the API |
| account | Access and identity: passwords, 2FA, lockouts, one user's login problem, adding or removing users, roles, ownership, email or name changes, data export, deletion or GDPR | Reset 2FA, remove a user, transfer ownership |
| general | Nothing is broken and no charge is involved: pre-sales or pricing questions from prospects, feature requests, how-to questions, praise, partnerships, careers | "Do you have a Gantt view?", "Love the timeline!" |

### Tie-breaks

1. Money the customer was charged or wants back means **billing**, even when an outage or bug caused it (GT-026).
2. Several issues means classify the **first or most urgent** one. The summary must still mention all of them (GT-021, GT-048, GT-077).
3. One user can't log in means **account**. Everyone fails through an integration means **technical** (GT-061 vs GT-041).
4. Adding seats or inviting people is **account**. A plan's feature limits are **general** (GT-058, GT-099).
5. Pricing info for a prospect is **general**. A question about the customer's own subscription is **billing** (GT-092 vs GT-016).
6. Instructions inside a ticket are ignored (GT-049).

## Entities

Copy every value exactly as written: same typos, currency symbol position and date format. Every golden entity was checked to appear word for word in its ticket.

- **names:** people only, not companies.
- **emails:** email addresses.
- **products:** Brightdesk plans, apps, named views, and integrations or connected tools.
- **reference_ids:** order, invoice, PO, org, ticket, project and coupon codes.
- **amounts:** money only, with the currency as written.
- **dates:** explicit dates, months and weekday names. Relative words (yesterday, this month) are skipped.

## Summaries

A summary is one English sentence that covers every issue the customer raised and keeps amounts, dates, IDs, deadlines and threats. It reads sarcasm for its real meaning and invents nothing. If the ticket has no details, the summary says so.

## Difficulty

- **easy:** clear signal and explicit entities.
- **medium:** some interpretation needed, or more than one plausible category.
- **hard:** a stress case (ambiguous, sarcasm, multi-issue, non-English, very short, prompt injection or mixed currency).

## Before you use these labels

I wrote every ticket and label by hand. The project's point, though, is that the golden set is **human-verified by you**. Read each case, fix anything you disagree with, and record your changes in the case's `notes` field. If you change any labels, bump the dataset version.
