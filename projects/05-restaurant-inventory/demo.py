import json
from pathlib import Path
import inventory as inv
from simulate import generate_sales

D = json.loads((Path(__file__).parent / "sample_data.json").read_text())
today, con = D["today"], inv.connect()
inv.load_master(con, D["ingredients"], D["recipes"])

for ing, qty in D["opening_counts"].items():
    inv.record_count(con, ing, D["opening_day"], qty)
extra = {(e["day"], e["dish"]): e["extra"] for e in D["promo_extra_sales"]}
print("Sales import:", inv.post_sales(con, generate_sales(D["sales_start"], D["sales_days"], D["base_daily_sales"], extra=extra)))
for d in D["history_deliveries"]:
    inv.record_delivery(con, d["ingredient"], d["day"], d["qty"], d["ref"])
for w in D["history_waste"]:
    inv.record_waste(con, w["ingredient"], w["day"], w["qty"], w["reason"], w["waste_id"])

for ing, delta in D["mid_count_deltas"].items():
    inv.record_count(con, ing, D["mid_count_day"], round(inv.stock_at(con, ing, D["mid_count_day"]) + delta, 2))

print(f"\n== Close-of-day count, {today} (physical count vs what the records expect) ==")
for ing, delta in D["count_deltas_close_of_today"].items():
    expected = inv.stock_at(con, ing, today)
    r = inv.record_count(con, ing, today, round(expected + delta, 2))
    print(f"  {ing:8} expected {r['expected']:>7}  counted {r['counted']:>7}  variance {r['variance']:>6}  "
          f"(tolerance {r['tolerance']})  {r['flag']}")

print("\n== Reorder alerts ==")
for a in inv.alerts(con, today):
    cover = "n/a" if a["days_of_cover"] is None else f"{a['days_of_cover']} days"
    print(f"  {a['status']:8} {a['name']:11} stock {a['stock']:>6} {a['unit']:3} cover {cover:>9}  reorder point {a['reorder_point']}")

print("\n== Draft purchase requests ==")
ids, skipped = inv.generate_reorders(con, today)
for pr_id in ids:
    pr = inv.purchase_request(con, pr_id)
    print(f"  PR{pr_id} {pr['supplier']}  est. PHP {pr['est_total']:,.2f}  [{pr['status']}]")
    for l in pr["lines"]:
        print(f"     {l['name']:11} {l['packs']} pack(s) = {l['qty']:g} {l['unit']}" + (f"   !! {l['flags']}" if l["flags"] else ""))
print("Run again:", inv.generate_reorders(con, today)[1][:3], "... (nothing duplicated)")

print("\n== Approval (managers up to PHP 8,000; the owner above that) ==")
for i in ids:
    try:
        inv.approve_pr(con, i, "Shift Manager", "manager", today)
        print(f"  PR{i}: approved by the manager")
    except PermissionError as e:
        print(f"  PR{i}: manager blocked: {e}")
        inv.approve_pr(con, i, "Owner", "owner", today)
        print(f"  PR{i}: approved by the owner")
    inv.mark_ordered(con, i, today)
big = max(ids, key=lambda i: inv.purchase_request(con, i)["est_total"])

print("\n== Receiving (the chicken delivery arrives short) ==")
pr = inv.purchase_request(con, big)
got = {l["ingredient_id"]: l["qty"] for l in pr["lines"]}
if "chicken" in got:
    got["chicken"] = round(got["chicken"] * 0.8, 1)
print("  flags:", inv.receive_delivery(con, big, got, "2026-11-09"))

print("\n== Stock report after the delivery (2026-11-09) ==")
for r in inv.stock_report(con, "2026-11-09"):
    print(f"  {r['name']:11} {r['stock']:>7} {r['unit']:3} use/day {r['avg_daily_use']:>5}  on order {r['on_order']:>5}  {r['status']}")
print("\n== Usage, last 7 days ==")
for r in inv.usage_report(con, "2026-11-02", today):
    print(f"  {r['name']:11} used {r['theoretical_usage']:>6} {r['unit']:3} (PHP {r['usage_cost']:>8,.2f})  waste {r['waste_recorded']}")
print("\n== Discrepancies to review ==")
for v in inv.variance_report(con):
    print(f"  {v['day']} {v['name']:8} {v['flag']:9} variance {v['variance']:+.2f} {v['unit']} (tolerance {v['tolerance']:.2f})")

out = Path(__file__).parent / "out"
out.mkdir(exist_ok=True)
(out / "stock_report.csv").write_text(inv.to_csv(inv.stock_report(con, today),
    ["name", "unit", "stock", "avg_daily_use", "days_of_cover", "reorder_point", "status", "stock_value"]))
(out / "variances.csv").write_text(inv.to_csv(inv.variance_report(con), ["day", "name", "expected", "counted", "variance", "tolerance", "flag"]))
print("\nCSV files for Google Sheets written to out/")
