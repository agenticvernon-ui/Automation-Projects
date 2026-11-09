# Project 1: E-commerce Order-to-Fulfillment Automation

A working prototype that takes incoming orders, validates them, updates customer records, checks and reserves
stock, routes problems to a human exception queue, and tracks every status change.

**Run it:** `python3 demo.py` (sample run) and `python3 -m unittest -v` (7 tests). No installs needed.

**Honest scope:** SQLite stands in for PostgreSQL, and sample JSON stands in for a store's order feed.
n8n and live REST APIs are *not* wired up yet (see "Next steps"). Say this in your portfolio; it's a prototype.

## Requirements
- Accept an order (JSON) with order_id, customer (name, email, ship_address) and items (sku, qty).
- Never create a bad record silently: anything incomplete, duplicated or unfulfillable goes to the exception queue.
- Be safe to re-run: the same order delivered twice must not reserve stock twice.
- Give fulfillment staff a status trail for every order.

## Workflow logic
1. **Receive:** store the order as RECEIVED (the raw payload is kept for audit).
2. **Validate:** order_id, name, valid-looking email, shipping address, at least one line item, positive quantities.
   Failure → EXCEPTION `INVALID_ORDER`.
3. **Duplicate checks:**
   - Same order_id seen before → ignored, logged `DUPLICATE_ORDER_ID` (idempotent retry).
   - Same customer + same basket within 60 minutes under a different order_id → EXCEPTION `DUPLICATE_SUSPECTED`.
4. **Customer update:** upsert by lower-cased email; refresh name/address; increment order_count.
5. **Inventory check:** available = on_hand − reserved for every SKU. Any shortfall or unknown SKU →
   EXCEPTION `INVENTORY_SHORTFALL`, and *nothing* is reserved (all-or-nothing).
6. **Reserve:** reserve stock, status READY_TO_FULFILL.
7. **Ship / cancel:** `ship()` moves stock out and sets SHIPPED; `cancel()` releases any reservation.
   Illegal moves (e.g. shipping twice) raise an error.
8. **Reconcile:** `inventory_discrepancies()` compares stored reservations with what open orders imply.

Status flow: RECEIVED → READY_TO_FULFILL → SHIPPED → DELIVERED; problems branch to EXCEPTION → (READY_TO_FULFILL | CANCELLED).

## Integration notes
- **n8n:** a Webhook node receives the order, an Execute Command or HTTP Request node calls
  `process_order`, and an IF node on the returned status sends EXCEPTION cases to Slack/email for review.
- **PostgreSQL:** `schema.sql` is nearly portable; replace `INTEGER PRIMARY KEY AUTOINCREMENT` with
  `GENERATED ALWAYS AS IDENTITY`, use `%s`/psycopg placeholders, and row-lock inventory
  (`SELECT ... FOR UPDATE`) so two simultaneous orders can't both claim the last item.

## Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| Order stuck in EXCEPTION | Look up its row in `exceptions` | Fix the data, then resubmit under a new order_id or cancel |
| `DUPLICATE_SUSPECTED` on a genuine repeat purchase | Same basket within the window | Review, then lower `dup_window_min` or approve manually |
| `unknown SKU` | Catalog and inventory table out of sync | Add the SKU via `load_inventory` |
| Discrepancy report not empty | Manual stock edit or a failed run | Compare with open orders, correct `reserved`, rerun the check |
| `illegal transition` error | Action on an order in the wrong state | Check `track(order_id)` for its history |

## Next steps (not built yet)
Real store webhook, PostgreSQL deployment, n8n workflow export, email alert on new exceptions, a simple review UI.
