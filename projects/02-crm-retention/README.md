# Project 2: E-commerce CRM & Customer Retention Automation

**Problem:** repeat customers are cheaper to keep than new ones to win, but follow-up emails sent without checks
annoy people, repeat themselves, or reach customers who opted out. This prototype automates *drafting* and *sending*
follow-ups while keeping a human approval step and hard safeguards in front of every message.

**Run it:** `python3 demo.py` (sample run) and `python3 -m unittest -v` (16 tests). No installs needed.

## Honest scope
- Nothing is emailed. `send_approved(con, now, send_fn)` takes a `send_fn`; that is where an email provider call would go.
- Data is simulated (SQLite, sample JSON). CRM integration and n8n workflows are described, not built.
- Message templates are plain text and deliberately make no discount or outcome promises; offers are a business decision.
- Time zone is fixed to the Philippines (UTC+8).

## Lifecycle workflow

| Stage | Trigger | Campaign |
|---|---|---|
| Post-purchase | an order placed 2 to 7 days ago | `POSTPURCHASE`: check-in and support offer |
| New customer, no second order | one order, placed 14 to 30 days ago | `NUDGE`: offer help or a reorder |
| Abandoned cart | cart open for 2 to 48 hours, no order since | `CART`: reminder that the cart is saved |
| Lapsed | last order over 90 days ago | `WINBACK`: ask what could be better |

## Segmentation
Each customer gets a lifecycle segment from purchase history and an engagement flag from opens and clicks in the last 30 days.

`PROSPECT` (no orders), `NEW` (1 order), `REPEAT` (2 orders), `VIP` (3+ orders), `AT_RISK` (last order 46 to 90 days ago),
`LAPSED` (over 90 days). Engagement is `ENGAGED` or `UNENGAGED`. Segments are reported today; only `LAPSED` triggers a campaign.

## Pipeline
```
triggers -> generate_drafts() -> DRAFT -> human approve()/edit_draft()/reject() -> APPROVED
         -> send_approved(): re-check every safeguard -> SENT -> record_event() -> report()
```

## Safeguards (what prevents bad outreach)
| Safeguard | Where | Behavior |
|---|---|---|
| Consent, unsubscribe, suppression, open complaint | draft time **and** send time | Never drafted; if the status changes after approval, blocked at send |
| Duplicate prevention | unique `dedupe_key` per trigger | Re-running a job cannot create or send a second copy |
| Human approval | `approve()` | Only `APPROVED` messages are ever sent; an approver name is required |
| Edits reset approval | `edit_draft()` | Any edit returns the message to `DRAFT` |
| Content check | approve, edit and send | Blocks banned claims (e.g. "guaranteed", "act now"), empty text, missing opt-out footer |
| Still relevant | send time | A reminder is blocked if the customer has bought since it was drafted |
| Quiet hours | send time | No sends outside 09:00 to 19:59 local; deferred, not lost |
| Frequency cap | send time | At most 2 marketing messages per customer per 7 days; extra ones wait |
| Expiry | send time | An approved message that cannot go out within 72 hours expires instead of arriving stale |
| Send failures | send time | Message stays `APPROVED` and is retried on the next run |

## Reporting
`report()` gives, per campaign: messages sent, opened, clicked, unsubscribed, and open and click rates. It also gives
the repeat-customer rate, the number of orders placed within 7 days after a send, and counts of blocked messages and
message statuses. **Orders after a send show timing, not proof the email caused them.** Treat them as a signal to test
(for example, with a holdout group), not as results to claim.

## Integration notes
- **n8n:** a Schedule node calls `generate_drafts` hourly; approved drafts come from a review step (a Google Sheet,
  Slack, or Telegram approval) that calls `approve`; a second Schedule node calls `send_approved`; an email provider
  webhook calls `record_event` for opens, clicks, unsubscribes and complaints.
- **Real CRM / email provider:** replace the SQLite loaders with CRM reads and pass a `send_fn` that calls the provider's send API.
- **Consent and privacy:** marketing email rules vary by country (for example the Data Privacy Act of 2012 in the
  Philippines, and the laws of wherever your customers live). Store proof of consent, honor opt-outs promptly, and have the
  real business confirm its obligations before sending anything.

## Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| No drafts created | Customers lack `marketing_opt_in`, or no trigger matched | Check `skipped` from `generate_drafts` for the reason |
| Messages stay `APPROVED` | Quiet hours, frequency cap, or provider errors | Read the `send_approved` result keys; they expire after 72 hours |
| `approve` raises an error | Content check failed or message is not a `DRAFT` | Read the error text; fix the copy with `edit_draft` |
| Customer got an email after opting out | Opt-out not recorded before the send job ran | Call `unsubscribe` from the opt-out link or STOP handler right away |
| Duplicate email in the real world | `send_fn` succeeded but the status update failed | Use the provider's idempotency key (see below) |

## Known limitations
- If the process crashes after the provider accepted a message but before the status update, a retry could send twice. A real
  build should pass `dedupe_key` to the provider as an idempotency key.
- Frequency caps count only messages sent by this system.
- Win-back and nudge copy is generic; real use needs copy reviewed by the business.

## Next steps (not built yet)
Real email-provider adapter, webhook receiver for events, holdout-group testing, per-customer send-time preferences, approval UI.
