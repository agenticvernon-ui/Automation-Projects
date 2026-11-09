# Staff and Manager Guide: Inventory and Reorders

This guide is for the people who use the system day to day. You do not need to understand the code.

## The idea in one minute
The system knows what you sold, so it can estimate what ingredients you used. It compares that with what you
count on the shelf. When something is running low it prepares a **draft** order for a manager to approve.
It never orders anything by itself.

## Every day (kitchen and counter staff)
1. **Record every delivery** when it arrives: what, how much, today's date. Check it against the supplier's slip.
2. **Record all waste** the same day: spoiled, dropped, burned, staff meals. If it is not written down, it will look like missing stock.
3. **At closing, count the ingredients the manager has asked you to count.** Count the real amount on the shelf. Never copy the system's number.

## Weekly (manager)
1. Do a full count of every ingredient at close of business on the same day each week.
2. Read the **discrepancy report** (below).
3. Open the **alerts** and **draft purchase requests**.

## Reading the alerts
| Alert | Meaning | What to do |
|---|---|---|
| `LOW` | Stock is at or below the reorder point | A draft order will be prepared |
| `CRITICAL` | Stock may run out before a delivery can arrive | Order today; consider calling the supplier |
| `USAGE_SPIKE` | Use is much higher than usual (promo, event) | Decide whether the extra demand will continue before approving |
| `LOW_DATA` | Not enough sales history yet | Check the quantity by eye |
| `NEGATIVE_STOCK` | Records say less than zero, which cannot be true | A delivery or count is missing; do a physical count |

## Approving a purchase request (manager or owner)
1. Open the draft. Check each line against what you can see in the kitchen and storeroom.
2. Reduce any quantity that looks too high (set it to 0 to remove the line).
3. Approve with **your own name**. Managers can approve up to PHP 8,000; bigger requests need the owner.
4. **Place the order with the supplier yourself**, then mark the request as ordered.
5. When the delivery arrives, enter what actually came, not what was ordered. Short deliveries are flagged for you to follow up.

## Reading the discrepancy report
- **SHORTAGE:** you counted less than expected. Common causes: waste not recorded, bigger portions than the recipe, a miscount, a delivery that was short.
- **SURPLUS:** you counted more than expected. Common causes: a delivery not recorded, recipe quantity too high, a miscount.
- Recount first. Then check waste entries and recipes. One flag is a reason to look, not a reason to blame anyone.

## Rules that keep the numbers trustworthy
- Count at the **same time** each time (close of business).
- Enter deliveries and waste on the **day they happen**.
- Never edit the system to make a number look right. If the count is different, record the count.
- Update the recipe when a dish changes (a new portion size or ingredient).
- Do not approve a request without checking it. Approvals are recorded with your name.

## Who does what
| Task | Staff | Manager | Owner |
|---|---|---|---|
| Record deliveries and waste | Yes | Yes | Yes |
| Count stock | Yes | Yes | Yes |
| Edit and approve requests up to PHP 8,000 | No | Yes | Yes |
| Approve requests above PHP 8,000 | No | No | Yes |
| Change recipes, suppliers, thresholds | No | Yes | Yes |
