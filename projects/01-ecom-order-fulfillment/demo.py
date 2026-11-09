import json
from pathlib import Path
import fulfillment as f

data = json.loads((Path(__file__).parent / "sample_data.json").read_text())
con = f.connect()
f.load_inventory(con, data["inventory"])

print("== Processing orders ==")
for order in data["orders"]:
    print(f"{order.get('order_id'):6} -> {f.process_order(con, order)}")

print("\n== Exception queue (needs a human) ==")
for r in con.execute("SELECT order_id, code, detail FROM exceptions ORDER BY id"):
    print(f"{r['order_id']:6} {r['code']:20} {r['detail']}")

print("\n== Ship A1001, then track it ==")
f.ship(con, "A1001")
for step in f.track(con, "A1001"):
    print(f"  {step['old_status']} -> {step['new_status']} ({step['note']})")

print("\n== Discrepancy check (clean) ==", f.inventory_discrepancies(con))
con.execute("UPDATE inventory SET reserved=reserved+4 WHERE sku='MUG-01'")  # simulate drift
print("== After simulated drift ==", f.inventory_discrepancies(con))
