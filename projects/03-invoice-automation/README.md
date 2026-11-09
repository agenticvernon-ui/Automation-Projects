# Project 3: Accounting & Invoice Automation

**Problem:** invoices built by hand from approved orders are slow and error-prone: wrong totals, missing customer
details, duplicate bills, and nobody chasing late payers. This prototype drafts invoices from approved orders using
fixed billing rules, sends anything doubtful to a human queue, posts approved invoices to the accounting system, and
drafts polite payment follow-ups for overdue ones.

**Run it:** `python3 demo.py` (sample run) and `python3 -m unittest -v` (37 tests). No installs needed.

## Honest scope
- The accounting system is a **stand-in** (`FakeAccounting`). No QuickBooks Online or Xero sandbox is connected. A real
  client only has to implement `create_invoice`, `record_payment` and `void_invoice`.
- Billing and tax rules (12% VAT, a VAT-exempt flag, discount cap, approval limits) are **simulated settings**. Real tax
  treatment, official invoice and receipt formats, and tax filing are not modelled; confirm them with an accountant.
- Nothing is actually emailed: follow-ups use a `send_fn` hook where an email provider call would go.
- Credit notes, partial credits, multi-currency, recurring invoices and backorders are not built.

## Assumptions
- Orders arrive **already approved** by a named person; the system only invoices approved orders.
- One order produces one invoice (enforced by the database). To re-bill after a void, submit a corrected order under a new order id.
- Customer details (name, email, billing address, terms, credit limit) come from the customer master.
- Money is calculated with exact decimals and stored as integer centavos, rounded half-up per line.
- The aging report covers invoices that are posted and unpaid; approved-but-unposted invoices are not in the books yet.

## Workflow
```
approved order -> process_order() -> checks pass -> DRAFT invoice
                       |                              |
                       +-> exception queue            +-> approve_invoice() (number assigned, due date set)
                           (fix or release)               -> post_approved() -> accounting system
                                                          -> record_payment() -> PARTIALLY_PAID / PAID
overdue -> generate_followups() -> DRAFT reminder -> approve_followup() -> send_followups()
```

## Billing rules
Line net = quantity x unit price. Discount = percent of subtotal (capped at 20%). Shipping is added and taxed.
VAT = 12% of the taxable amount unless the customer is VAT-exempt. Terms come from the customer (for example 30 days from the issue date).

## Validation and the exception queue
| Code | Type | Trigger | Resolution |
|---|---|---|---|
| `NOT_APPROVED` | Blocking | no approver name or date on the order | Get the order approved, then correct and reprocess |
| `UNKNOWN_CUSTOMER` | Blocking | customer not in master | Add the customer |
| `MISSING_CUSTOMER_INFO` | Blocking | no name, billing address, or valid email | Fix the master, then reprocess |
| `INVALID_LINE` | Blocking | no items, quantity not positive, negative or non-numeric price | Correct the order |
| `INVALID_AMOUNT` | Blocking | discount outside 0 to 20%, negative shipping, total not above zero | Correct the order |
| `WRONG_CURRENCY` | Blocking | currency is not PHP | Correct the order |
| `AMOUNT_MISMATCH` | Blocking | the order's stated total differs from the billing rules | Correct the order or the rules |
| `POSSIBLE_DUPLICATE` | Review | same customer and items as an invoice in the last 30 days | Accountant releases with a note, or rejects |
| `HIGH_VALUE_REVIEW` | Review | total at or above PHP 100,000 | Controller releases with a note |
| `CREDIT_LIMIT` | Review | unpaid invoices plus this one exceed the customer's limit | Controller releases with a note |
| `POSTING_FAILED` | Review | accounting system rejected 5 post attempts | Fix the connection, replay |
| `OVERDUE_ESCALATION` | Review | invoice more than 60 days overdue | A person contacts the customer |

Blocking problems can only be fixed (`correct_order`), never released. Review items can be released by the right role
with a written note, and every release is recorded in the audit log.

## Approval controls
- A **named approver** is required for every invoice, follow-up and release.
- **Separation of duties:** the person who approved the order cannot approve its invoice.
- **Role limits:** accountants approve invoices up to PHP 50,000; the controller approves above that.
- **Gap-free numbering:** numbers (`INV-2026-0001`...) are assigned at approval and never reused, even for voided invoices.
- **Audit log** of drafts, approvals, releases, postings, payments, voids and follow-ups.
- **Idempotent posting and payments:** the order id and bank reference make retries safe; a repeated payment reference is ignored and overpayments are rejected.

## Payment follow-ups
| Days overdue | Draft |
|---|---|
| 1 to 7 | `REMINDER_1`: friendly reminder |
| 8 to 30 | `REMINDER_2`: overdue notice asking for a payment date or any problem |
| 31 to 60 | `FINAL_REMINDER`: asks the customer to contact you this week |
| over 60 | No email; an `OVERDUE_ESCALATION` item for a person to call |

Safeguards: every message is a draft until a named person approves it; one draft per invoice, stage and balance; a
7-day cooldown after a reminder is sent; disputed invoices are skipped (`mark_disputed`); at send time the invoice is
re-checked and the message is blocked if it was paid, voided, disputed or the balance changed; wording check blocks
threatening phrases ("legal action", "blacklist", and similar). Message wording is plain and makes no threats.

## Reports
- `status_summary`: invoice counts and totals by status.
- `aging_report`: outstanding balances by customer in Current, 1-30, 31-60, 61-90 and 90+ day buckets, plus a list of overdue invoices.
- `exceptions_report`: everything waiting for a human, with the reason.
- `reconcile`: invoices here versus the accounting system (missing, extra, different amount, different payments).
- `followup_summary`: follow-ups by status.

## Integration notes
- **QuickBooks Online / Xero sandbox:** write a client with the three methods of `FakeAccounting`, mapping `reference` to the provider's idempotency or reference field. Check the provider's tax-code, rate-limit and authentication rules; none of that is built here.
- **n8n:** a Webhook or Schedule node reads approved orders and calls `ingest_order` then `process_order`; a review step (Sheet, Slack or email) feeds `approve_invoice` and `release_exception`; a Schedule node runs `post_approved`, `generate_followups` and `send_followups`; email events are not required by this project.
- **Privacy:** invoices hold customer names, emails and addresses. Keep real data out of the repo, limit who can see them, and follow the data-protection obligations that apply to the real business.

## Error handling and recovery
1. Accounting system down: `post_approved()` leaves invoices APPROVED and retries (up to 5 attempts, then a `POSTING_FAILED` item). The order id is the idempotency key, so a retry cannot create a second invoice.
2. A payment fails to reach accounting: nothing is saved locally, so retrying with the same reference is safe.
3. Books disagree: run `reconcile()`; each difference names the invoice and the kind of mismatch.

## Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| Order stuck in the queue | Open exception | Read it in `exceptions_report`; fix the data or release (review items only) |
| `cannot approve its invoice` | You approved the order | Ask a different accountant |
| Invoice stays APPROVED | Accounting system unavailable | Restore it; run `post_approved()` again |
| Payment rejected | Exceeds the balance, or a repeated reference | Check the amount; record any overpayment as a customer credit |
| No reminder created | Not yet due, disputed, paid, in cooldown, or over 60 days | See the `skipped` counts from `generate_followups` |
| Reconcile shows differences | Manual change in accounting, or a failed post | Correct the wrong side and reconcile again |

## Next steps (not built yet)
Real accounting-system client, credit notes, invoice PDF generation, email provider adapter, a review screen, recurring billing.
