import json
from pathlib import Path
import pos_crm as p

data = json.loads((Path(__file__).parent / "sample_data.json").read_text())
con, crm = p.connect(), p.FakeCRM()
for c in data["crm_seed"]:
    crm.create(c)

print("== Receive (webhook) ==")
for t in data["transactions"]:
    print(f"{t['txn_id']:3} -> {p.receive(con, t)}")

print("\n== Process ==", dict(p.process_pending(con, crm)))
for r in con.execute("SELECT txn_id, outcome, crm_id FROM txn_map ORDER BY txn_id"):
    print(f"  {r['txn_id']:3} {r['outcome']:14} {r['crm_id']}")

print("\n== Quarantine (needs a human) ==")
for q in p.summary(con)["open_quarantine"]:
    print(f"  {q['txn_id']:3} {q['code']:18} {q['detail']}")

print("\n== CRM outage during T8, then recovery ==")
p.receive(con, data["late_transaction"])
crm.fail_on["add_purchase"] = 1
print("first attempt :", dict(p.process_pending(con, crm)), "| profiles named Luz:",
      sum(c["name"] == "Luz Tan" for c in crm.customers.values()))
print("recovery run  :", dict(p.process_pending(con, crm)), "| profiles named Luz:",
      sum(c["name"] == "Luz Tan" for c in crm.customers.values()))

print("\n== Human fixes T4 (typo in email) and replays it ==")
fixed = dict(data["transactions"][3], customer={"name": "Ben Cruz", "email": "ben@example.com"})
print("T4 ->", p.resolve_quarantine(con, crm, "T4", fixed))
print("\nFinal:", p.summary(con)["inbox"])
