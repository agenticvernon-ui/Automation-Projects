import json
from pathlib import Path
import invoicing as v

D = json.loads((Path(__file__).parent / "sample_data.json").read_text())
today, con, acct = D["today"], v.connect(), v.FakeAccounting()
v.load_customers(con, D["customers"])
orders = {o["order_id"]: o for o in D["orders"]}


def draft_id(order_id):
    return con.execute("SELECT id FROM invoices WHERE order_id=?", (order_id,)).fetchone()["id"]


print("== 1. Orders arrive ==")
for o in D["orders"]:
    v.ingest_order(con, o)
print("O-1001 delivered twice ->", v.ingest_order(con, orders["O-1001"]))

print("\n== 2. First invoice for Carla (builds her credit exposure) ==")
print("O-1000 ->", v.process_order(con, "O-1000", today))
n = v.approve_invoice(con, draft_id("O-1000"), "Accountant Maria", "accountant", today)
print("approved as", n, "| posting:", dict(v.post_approved(con, acct, today)))

print("\n== 3. Checks on every other order ==")
for oid in list(orders)[1:]:
    print(f"{oid} -> {v.process_order(con, oid, today)}")
print("O-1001 again ->", v.process_order(con, "O-1001", today))

print("\n== 4. Exception queue ==")
for e in v.exceptions_report(con):
    print(f"  {e['order_id']} {e['code']:22} {e['severity']:8} {e['detail']}")

print("\n== 5. People fix and release exceptions ==")
D["customers"][1]["billing_address"] = "5 Perdices St, Dumaguete"
v.load_customers(con, D["customers"])
print("O-1002 after customer master fixed ->", v.process_order(con, "O-1002", today))
print("O-1003 after total corrected     ->", v.correct_order(con, "O-1003", dict(orders["O-1003"], total="2240.00"), "Billing Clerk", today))
try:
    v.release_exception(con, "O-1006", ["HIGH_VALUE_REVIEW"], "Accountant Maria", "accountant", "confirmed with sales", today)
except PermissionError as e:
    print("accountant releasing O-1006 ->", e)
print("controller releases O-1006        ->", v.release_exception(con, "O-1006", ["HIGH_VALUE_REVIEW"], "Controller Joy", "controller", "customer PO checked", today))
print("accountant releases O-1005        ->", v.release_exception(con, "O-1005", ["POSSIBLE_DUPLICATE"], "Accountant Maria", "accountant", "separate PO from the customer", today))
try:
    v.release_exception(con, "O-1004", ["NOT_APPROVED"], "Accountant Maria", "accountant", "please", today)
except ValueError as e:
    print("releasing a blocking error        ->", e)

print("\n== 6. Invoice approval controls ==")
try:
    v.approve_invoice(con, draft_id("O-1001"), "Sales Lead", "accountant", today)
except PermissionError as e:
    print("order approver on own invoice ->", e)
try:
    v.approve_invoice(con, draft_id("O-1006"), "Accountant Maria", "accountant", today)
except PermissionError as e:
    print("accountant over the limit     ->", e)
for oid, who, role in [("O-1001", "Accountant Maria", "accountant"), ("O-1002", "Accountant Maria", "accountant"),
                       ("O-1003", "Accountant Maria", "accountant"), ("O-1005", "Accountant Maria", "accountant"),
                       ("O-1009", "Accountant Maria", "accountant"), ("O-1006", "Controller Joy", "controller")]:
    print(f"  {oid} approved as", v.approve_invoice(con, draft_id(oid), who, role, today))

print("\n== 7. Posting during an accounting outage ==")
acct.fail_next = 2
print("first run :", dict(v.post_approved(con, acct, today)))
print("retry     :", dict(v.post_approved(con, acct, today)), "| invoices in accounting:", len(acct.invoices))

print("\n== 8. Payments ==")
print("INV-2026-0001 full payment   ->", v.record_payment(con, acct, "INV-2026-0001", "11200.00", "2026-11-20", "BANK-001"))
print("INV-2026-0002 partial        ->", v.record_payment(con, acct, "INV-2026-0002", "3000.00", "2026-11-25", "BANK-002"))
print("same bank reference again    ->", v.record_payment(con, acct, "INV-2026-0002", "3000.00", "2026-11-25", "BANK-002"))
try:
    v.record_payment(con, acct, "INV-2026-0002", "9999.00", "2026-11-26", "BANK-003")
except ValueError as e:
    print("overpayment                  ->", e)

print("\n== 9. Reports ==")
print("Status:", v.status_summary(con))
rep = v.aging_report(con, D["later"])
print(f"\nAging as of {rep['as_of']} (outstanding {rep['total_outstanding']}):")
for r in rep["customers"]:
    print("  ", r)
print("Overdue:", rep["overdue_invoices"])
print("\nOpen exceptions:", [(e["order_id"], e["code"]) for e in v.exceptions_report(con)])
print("Reconciliation:", v.reconcile(con, acct) or "books agree")
acct.invoices["INV-2026-0002"]["total_cents"] += 100
del acct.invoices["INV-2026-0005"]
acct.invoices["INV-2026-9999"] = {"total_cents": 1, "paid_cents": 0, "status": "OPEN"}
print("After tampering with the accounting side:")
for i in v.reconcile(con, acct):
    print("  ", i)

print("\n== 10. Payment follow-ups (as of %s) ==" % D["later"])
later = D["later"]
v.mark_disputed(con, "INV-2026-0004", "Accountant Maria", "customer says the quantity was wrong")
created, skipped = v.generate_followups(con, later)
print("drafted:", dict(created), "| skipped:", dict(skipped))
for f in con.execute("SELECT f.id, f.stage, i.number, f.subject FROM followups f JOIN invoices i ON i.id=f.invoice_id").fetchall():
    print(f"  #{f['id']} {f['stage']:15} {f['number']}  {f['subject']}")
print("run again:", dict(v.generate_followups(con, later)[0]) or "nothing new (duplicates prevented)")
print("send before approval:", dict(v.send_followups(con, later)) or "nothing sent")
for f in con.execute("SELECT id FROM followups").fetchall():
    v.approve_followup(con, f["id"], "Accountant Maria", later)
v.record_payment(con, acct, "INV-2026-0007", "168000.00", later, "BANK-004")     # pays before the send job runs
outbox = []
print("send:", dict(v.send_followups(con, later, outbox.append)), "(the invoice paid just now is blocked)")
print("sent to:", [m["to"] for m in outbox])
print("a week later:", dict(v.generate_followups(con, "2026-12-27")[0]) or "none", "(cooldown applies to invoices just contacted)")
print("two months on (2027-02-01):", dict(v.generate_followups(con, "2027-02-01")[0]))
print("Escalations for a person:", [e["detail"] for e in v.exceptions_report(con) if e["code"] == "OVERDUE_ESCALATION"])
print("Follow-up summary:", v.followup_summary(con))
