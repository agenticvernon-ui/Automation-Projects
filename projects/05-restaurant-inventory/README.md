# Project 5: Restaurant Inventory & Reorder Automation

**Problem:** a kitchen runs out of key ingredients, or over-orders and wastes them, because stock is checked by eye.
This prototype turns sales into estimated ingredient usage, warns before stock runs out, drafts purchase requests for
a manager to approve, and flags counts that do not match what the records say should be on the shelf.

**Run it:** `python3 demo.py` (sample run) and `python3 -m unittest -v` (18 tests). No installs needed.
Staff and managers: read [STAFF_GUIDE.md](STAFF_GUIDE.md).

## Honest scope
- Sales, recipes, costs and suppliers are simulated. Costs are illustrative PHP figures.
- The system **never places an order**. It drafts; a manager approves; a person orders from the supplier and then marks it ordered.
- Google Sheets is supported through CSV export/import only. A live Sheets or n8n connection is described, not built.
- Usage is *theoretical* (sales times recipe quantities). It cannot see over-portioning, theft or spoilage unless
  staff record waste or a count disagrees. That disagreement is exactly what the variance report surfaces.
- No supplier prices, minimum order values, delivery days or backorders are modelled.

## How stock is calculated
Stock = the latest physical count (taken at close of business) + deliveries after it - theoretical usage after it - recorded waste after it.
Everything is stored as a ledger of entries, so any number can be traced to a sale, delivery, waste entry or count.

## Workflow logic
1. **Import sales** (`post_sales`): each sale is exploded through its recipe into ingredient usage. Re-importing the same `sale_id` does nothing. Unknown dishes and bad quantities are returned for fixing, not guessed.
2. **Record deliveries and waste** as they happen.
3. **Count** (`record_count`): compare the physical count with expected stock. Outside tolerance (5% of usage since the last count, or a tenth of a pack, whichever is larger) is flagged `SHORTAGE` or `SURPLUS`. The count then becomes the new baseline.
4. **Alert** (`alerts`): reorder point = max(minimum threshold, average daily use x (lead time + safety days)). `LOW` means at or below it; `CRITICAL` means stock would run out before a new delivery could arrive.
5. **Draft** (`generate_reorders`): one DRAFT purchase request per supplier. Quantity covers lead time plus 7 days of average use, rounded up to whole packs.
6. **Approve** (`approve_pr`): a named manager can approve up to PHP 8,000; larger requests need the owner. Managers can reduce quantities first (`adjust_line`).
7. **Order and receive** (`mark_ordered`, `receive_delivery`): short or over deliveries are flagged and stock is booked at what actually arrived.
8. **Report**: stock levels (with quantity on order), estimated usage and cost, waste, and discrepancies; CSV export for Google Sheets.

Averages are per **trading day** (days with sales), so a closed day does not understate usage.

## Safeguards
| Risk | Safeguard |
|---|---|
| Ordering the same thing twice | No new draft while an open request (draft, approved or ordered) exists for that ingredient |
| Ordering on bad data | Fewer than 3 trading days of sales: sized from the minimum threshold and flagged `LOW_DATA` |
| Ordering into a promotion or one-off | Recent use 40% above the earlier average adds a `USAGE_SPIKE` flag for the approver |
| A runaway quantity | Lines are capped at 20 packs and flagged `CAPPED` |
| Records that cannot be true | Negative stock is flagged `NEGATIVE_STOCK`, sized from zero, and negative counts are rejected |
| Unapproved spending | Only named managers or the owner approve; limits by role; only DRAFT requests can be edited or approved |
| Hidden short deliveries | Received quantities are compared with ordered quantities (5% tolerance) |
| Double booking | A delivery can only be received once; sale and waste entries are keyed so they cannot repeat |

## Exception handling
| Situation | What happens | Who acts |
|---|---|---|
| `SHORTAGE` at count | Logged in the variance report | Manager checks waste not recorded, portions, recipe accuracy, recount |
| `SURPLUS` at count | Logged | Manager checks a missed delivery entry, recipe overstated, miscount |
| `USAGE_SPIKE` on a draft | Flag on the line | Approver confirms it will continue, or reduces the quantity |
| `SHORT` delivery | Flag at receiving | Manager contacts the supplier |
| Unknown dish in sales import | Returned, not posted | Add the recipe, then re-import |

A discrepancy is a reason to look, not proof of theft. Most are counting, waste-logging or recipe problems.

## Google Sheets and n8n
- **Today:** `to_csv()` writes reports to CSV; in Sheets use File > Import. Sales can be exported from a Sheet as CSV and loaded with the same shape as `sample_data.json` sales rows.
- **Later (not built):** an n8n Google Sheets node reads new sales rows and calls `post_sales`; a Schedule node calls `generate_reorders` each morning and posts the drafts to a manager for approval.

## Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| No reorder drafted for a low item | An open request already exists | Check its status; reject it if stale, then regenerate |
| Stock looks far too low | A delivery was never entered, or a sale imported twice under different IDs | Enter the delivery; check sale IDs; recount |
| Stock is negative | Missing opening count or deliveries | Take a physical count; that resets the baseline |
| Many `SHORTAGE` flags | Waste not logged, or recipe quantities too low | Review waste entries and recipe portions |
| Sales rows ignored | Dish name does not match a recipe | Fix the name or add the recipe |

## Next steps (not built yet)
Supplier price lists and minimum orders, partial deliveries and backorders, expiry dates, a simple approval screen, live Sheets or n8n connection.
