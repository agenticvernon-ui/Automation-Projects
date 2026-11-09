import json
from datetime import datetime
from pathlib import Path
import retention as r

data = json.loads((Path(__file__).parent / "sample_data.json").read_text())
now = datetime.fromisoformat(data["now"])
later = datetime.fromisoformat(data["send_attempt_2"])
con = r.connect()
r.load_data(con, data)

print("== Segments ==", r.segment_counts(con, now))

print("\n== 1. Generate drafts ==")
created, skipped = r.generate_drafts(con, now)
print("created:", dict(created))
print("skipped:", dict(skipped), "  (consent, unsubscribe, complaint and 'already bought' are filtered out)")
print("run again:", dict(r.generate_drafts(con, now)[0]) or "no new drafts (duplicates prevented)")

print("\n== 2. Human approval ==")
for m in con.execute("SELECT id, customer_id, campaign, subject FROM messages WHERE status='DRAFT'").fetchall():
    print(f"  #{m['id']} {m['customer_id']:4} {m['campaign']:13} {m['subject']}")
first = con.execute("SELECT id FROM messages WHERE status='DRAFT' ORDER BY id").fetchone()["id"]
try:
    r.approve(con, first, "", now)
except ValueError as e:
    print("approve without a name ->", e)
try:
    r.edit_draft(con, first, "Act now: guaranteed savings", "Body text")
except ValueError as e:
    print("risky edit rejected ->", e)
for m in con.execute("SELECT id FROM messages WHERE status='DRAFT'").fetchall():
    r.approve(con, m["id"], "Vernon", now)

jun = con.execute("SELECT id FROM messages WHERE customer_id='c10'").fetchone()["id"]
r.unsubscribe(con, "c10", now)
print("Jun unsubscribes after approval (c10, message #%d)" % jun)

outbox = []
print("\n== 3. Send attempt at 20:00 local ==", dict(r.send_approved(con, now, outbox.append)))
print("== 4. Send attempt at 09:00 local ==", dict(r.send_approved(con, later, outbox.append)))
for m in outbox:
    print(f"  sent to {m['customer_id']:4} [{m['campaign']}] {m['subject']}")

print("\n== 5. Engagement and a later purchase ==")
sent = {m["customer_id"]: m["id"] for m in con.execute("SELECT id, customer_id FROM messages WHERE status='SENT' AND approved_by<>'history'")}
r.record_event(con, sent["c3"], "OPENED", later)
r.record_event(con, sent["c3"], "CLICKED", later)
r.record_event(con, sent["c1"], "OPENED", later)
r.load_data(con, {"orders": [data["later_order"]]})

print()
print(r.format_report(r.report(con, datetime.fromisoformat("2026-11-10T00:00:00+00:00"))))
