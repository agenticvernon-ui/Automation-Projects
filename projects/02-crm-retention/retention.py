"""E-commerce CRM and customer retention automation prototype.

Triggers create DRAFT messages -> a human approves -> a send job re-checks every safeguard -> events feed reporting.
Nothing is actually emailed: `send_fn` is where an email provider call would go.
All functions take `now` (a timezone-aware datetime) so behavior is deterministic and testable.
"""
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

LOCAL_OFFSET = timedelta(hours=8)        # Philippines, UTC+8
SEND_WINDOW = (9, 20)                    # local hours: sends allowed from 09:00 until 19:59
WEEKLY_CAP = 2                           # max marketing messages per customer per 7 days
EXPIRY = timedelta(hours=72)             # an approved message that cannot go out in time is dropped, not sent late
ATTRIBUTION_WINDOW = timedelta(days=7)
RELEVANCE_CAMPAIGNS = {"CART", "NUDGE", "WINBACK"}
BANNED = ("guarantee", "risk-free", "act now", "100% free")
FOOTER = "\n\n--\nYou are receiving this because you opted in. Reply STOP to opt out at any time."
EVENT_TYPES = {"OPENED", "CLICKED", "UNSUBSCRIBED", "COMPLAINED"}


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _dt(text):
    return datetime.fromisoformat(text)


def connect(path=":memory:"):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def load_data(con, data):
    """Load simulated customers, orders, carts and prior messages (all timestamps ISO with offset)."""
    with con:
        for c in data.get("customers", []):
            con.execute("INSERT OR REPLACE INTO customers VALUES (?,?,?,?,?,?,?,?)",
                        (c["id"], c["email"].lower(), c["name"], int(c.get("marketing_opt_in", 0)),
                         c.get("unsubscribed_at"), c.get("suppressed_reason"),
                         int(c.get("open_complaint", 0)), _iso(_dt(c["created_at"]))))
        for o in data.get("orders", []):
            con.execute("INSERT OR REPLACE INTO orders VALUES (?,?,?,?)",
                        (o["order_id"], o["customer_id"], o["total"], _iso(_dt(o["ordered_at"]))))
        for k in data.get("carts", []):
            con.execute("INSERT OR REPLACE INTO carts VALUES (?,?,?,?,?)",
                        (k["cart_id"], k["customer_id"], k["total"], _iso(_dt(k["updated_at"])),
                         k.get("status", "OPEN")))
        for m in data.get("prior_messages", []):
            con.execute("INSERT OR IGNORE INTO messages(customer_id,campaign,dedupe_key,subject,body,status,"
                        "created_at,sent_at,approved_by) VALUES (?,?,?,?,?,'SENT',?,?,?)",
                        (m["customer_id"], m["campaign"], m["dedupe_key"], m["subject"], m["body"],
                         _iso(_dt(m["sent_at"])), _iso(_dt(m["sent_at"])), "history"))


def _customer(con, cid):
    return dict(con.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone())


def _orders(con, cid):
    return [dict(r) for r in con.execute(
        "SELECT * FROM orders WHERE customer_id=? ORDER BY ordered_at", (cid,))]


def _bought_since(con, cid, iso):
    return con.execute("SELECT 1 FROM orders WHERE customer_id=? AND ordered_at>=?", (cid, iso)).fetchone() is not None


# ---------- segmentation ----------

def segment(con, cid, now):
    """Return (lifecycle, engagement) from purchase history and recent opens/clicks."""
    orders = _orders(con, cid)
    if not orders:
        lifecycle = "PROSPECT"
    else:
        days = (now - _dt(orders[-1]["ordered_at"])).days
        n = len(orders)
        if days > 90:
            lifecycle = "LAPSED"
        elif days > 45:
            lifecycle = "AT_RISK"
        elif n >= 3:
            lifecycle = "VIP"
        elif n == 2:
            lifecycle = "REPEAT"
        else:
            lifecycle = "NEW"
    engaged = con.execute(
        "SELECT 1 FROM events e JOIN messages m ON m.id=e.message_id WHERE m.customer_id=? "
        "AND e.type IN ('OPENED','CLICKED') AND e.at>=?", (cid, _iso(now - timedelta(days=30)))).fetchone()
    return lifecycle, "ENGAGED" if engaged else "UNENGAGED"


def segment_counts(con, now):
    return dict(Counter(segment(con, r["id"], now)[0] for r in con.execute("SELECT id FROM customers")))


# ---------- safeguards ----------

def eligibility(cust):
    """Why this customer must not receive marketing, or None if they may."""
    if not cust["marketing_opt_in"]:
        return "NO_CONSENT"
    if cust["unsubscribed_at"]:
        return "UNSUBSCRIBED"
    if cust["suppressed_reason"]:
        return "SUPPRESSED"
    if cust["open_complaint"]:
        return "OPEN_COMPLAINT"
    return None


def content_check(subject, body):
    text = f"{subject}\n{body}".lower()
    problems = [f"banned phrase: {b!r}" for b in BANNED if b in text]
    if not subject.strip() or not body.strip():
        problems.append("empty subject or body")
    if len(subject) > 80:
        problems.append("subject longer than 80 characters")
    if "reply stop" not in text:
        problems.append("missing opt-out footer")
    return problems


# ---------- drafting ----------

def _compose(campaign, cust, **fmt):
    first = cust["name"].split()[0]
    if campaign == "POSTPURCHASE":
        subject = f"Thanks for your order, {first}"
        body = (f"Your order {fmt['order_id']} should have reached you by now. "
                "If anything is wrong, reply to this email and we will help.")
    elif campaign == "NUDGE":
        subject = f"{first}, anything we can help you find?"
        body = ("Thanks again for your first order. If you would like to reorder or have questions "
                "about what you bought, just reply to this email.")
    elif campaign == "CART":
        subject = "You left something in your cart"
        body = (f"Hi {first}, you left items worth PHP {fmt['total']:,.2f} in your cart. "
                "They are still saved if you want to finish your order.")
    else:  # WINBACK
        subject = f"We would love to see you again, {first}"
        body = "It has been a while since your last order. If there is something we could do better, reply and tell us."
    return subject, body + FOOTER


def generate_drafts(con, now):
    """Find trigger events and create DRAFT messages. Safe to run repeatedly (dedupe_key is unique)."""
    created, skipped = Counter(), Counter()

    def add(cid, campaign, key, **fmt):
        cust = _customer(con, cid)
        reason = eligibility(cust)
        if reason:
            skipped[reason] += 1
            return
        subject, body = _compose(campaign, cust, **fmt)
        with con:
            cur = con.execute("INSERT OR IGNORE INTO messages(customer_id,campaign,dedupe_key,subject,body,"
                              "status,created_at) VALUES (?,?,?,?,?,'DRAFT',?)",
                              (cid, campaign, key, subject, body, _iso(now)))
        if cur.rowcount:
            created[campaign] += 1
        else:
            skipped["ALREADY_DRAFTED"] += 1

    for o in con.execute("SELECT * FROM orders WHERE ordered_at<=? AND ordered_at>=?",
                         (_iso(now - timedelta(days=2)), _iso(now - timedelta(days=7)))).fetchall():
        add(o["customer_id"], "POSTPURCHASE", f"postpurchase:{o['order_id']}", order_id=o["order_id"])

    for c in con.execute("SELECT id FROM customers").fetchall():
        orders = _orders(con, c["id"])
        if len(orders) == 1 and now - timedelta(days=30) <= _dt(orders[0]["ordered_at"]) <= now - timedelta(days=14):
            add(c["id"], "NUDGE", f"nudge:{orders[0]['order_id']}")
        if segment(con, c["id"], now)[0] == "LAPSED":
            add(c["id"], "WINBACK", f"winback:{c['id']}:{orders[-1]['order_id']}")

    for k in con.execute("SELECT * FROM carts WHERE status='OPEN' AND updated_at<=? AND updated_at>=?",
                         (_iso(now - timedelta(hours=2)), _iso(now - timedelta(hours=48)))).fetchall():
        if _bought_since(con, k["customer_id"], k["updated_at"]):
            skipped["PURCHASED_SINCE"] += 1
            continue
        add(k["customer_id"], "CART", f"cart:{k['cart_id']}", total=k["total"])
    return created, skipped


# ---------- approval ----------

def _message(con, mid):
    return dict(con.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone())


def edit_draft(con, mid, subject, body):
    """A human edits a draft. Editing always sends it back to DRAFT so the new text must be approved again."""
    m = _message(con, mid)
    if m["status"] not in ("DRAFT", "APPROVED"):
        raise ValueError(f"cannot edit a {m['status']} message")
    if "reply stop" not in body.lower():
        body += FOOTER
    problems = content_check(subject, body)
    if problems:
        raise ValueError("; ".join(problems))
    with con:
        con.execute("UPDATE messages SET subject=?, body=?, status='DRAFT', approved_by=NULL, approved_at=NULL "
                    "WHERE id=?", (subject, body, mid))


def approve(con, mid, approver, now):
    if not (approver or "").strip():
        raise ValueError("an approver name is required")
    m = _message(con, mid)
    if m["status"] != "DRAFT":
        raise ValueError(f"only DRAFT messages can be approved (this one is {m['status']})")
    problems = content_check(m["subject"], m["body"])
    if problems:
        raise ValueError("; ".join(problems))
    with con:
        con.execute("UPDATE messages SET status='APPROVED', approved_by=?, approved_at=? WHERE id=?",
                    (approver.strip(), _iso(now), mid))


def reject(con, mid, approver, reason):
    with con:
        con.execute("UPDATE messages SET status='REJECTED', block_reason=?, approved_by=? WHERE id=? AND status='DRAFT'",
                    (reason, approver, mid))


# ---------- sending ----------

def unsubscribe(con, cid, now):
    """Opt-out via link or a STOP reply. Takes effect immediately, even for messages already approved."""
    with con:
        con.execute("UPDATE customers SET unsubscribed_at=? WHERE id=? AND unsubscribed_at IS NULL", (_iso(now), cid))


def _block(con, mid, reason):
    with con:
        con.execute("UPDATE messages SET status='BLOCKED', block_reason=? WHERE id=?", (reason, mid))


def send_approved(con, now, send_fn=None):
    """Send APPROVED messages after re-checking every safeguard at send time."""
    results = Counter()
    rows = con.execute("SELECT * FROM messages WHERE status='APPROVED' ORDER BY id").fetchall()
    for m in map(dict, rows):
        if now - _dt(m["created_at"]) > EXPIRY:
            with con:
                con.execute("UPDATE messages SET status='EXPIRED' WHERE id=?", (m["id"],))
            results["EXPIRED"] += 1
            continue
        cust = _customer(con, m["customer_id"])
        reason = eligibility(cust)
        if reason:
            _block(con, m["id"], reason)
            results[f"BLOCKED_{reason}"] += 1
            continue
        problems = content_check(m["subject"], m["body"])
        if problems:
            _block(con, m["id"], "CONTENT_CHECK_FAILED")
            results["BLOCKED_CONTENT_CHECK_FAILED"] += 1
            continue
        if m["campaign"] in RELEVANCE_CAMPAIGNS and _bought_since(con, m["customer_id"], m["created_at"]):
            _block(con, m["id"], "NO_LONGER_RELEVANT")
            results["BLOCKED_NO_LONGER_RELEVANT"] += 1
            continue
        local_hour = (now + LOCAL_OFFSET).hour
        if not SEND_WINDOW[0] <= local_hour < SEND_WINDOW[1]:
            results["DEFERRED_QUIET_HOURS"] += 1
            continue
        recent = con.execute("SELECT COUNT(*) FROM messages WHERE customer_id=? AND status='SENT' AND sent_at>=?",
                             (m["customer_id"], _iso(now - timedelta(days=7)))).fetchone()[0]
        if recent >= WEEKLY_CAP:
            results["DEFERRED_FREQUENCY_CAP"] += 1
            continue
        try:
            if send_fn:
                send_fn(m)
        except Exception:
            results["SEND_ERROR"] += 1   # stays APPROVED and is retried on the next run
            continue
        with con:
            con.execute("UPDATE messages SET status='SENT', sent_at=? WHERE id=?", (_iso(now), m["id"]))
        results["SENT"] += 1
    return results


# ---------- engagement and reporting ----------

def record_event(con, mid, event_type, now):
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {event_type}")
    m = _message(con, mid)
    if m["status"] != "SENT":
        raise ValueError("events can only be recorded for SENT messages")
    with con:
        con.execute("INSERT INTO events(message_id,type,at) VALUES (?,?,?)", (mid, event_type, _iso(now)))
        if event_type == "UNSUBSCRIBED":
            con.execute("UPDATE customers SET unsubscribed_at=? WHERE id=? AND unsubscribed_at IS NULL",
                        (_iso(now), m["customer_id"]))
        elif event_type == "COMPLAINED":
            con.execute("UPDATE customers SET open_complaint=1 WHERE id=?", (m["customer_id"],))


def report(con, now, days=30):
    since = _iso(now - timedelta(days=days))
    campaigns = {}
    for r in con.execute(
            "SELECT m.campaign, COUNT(DISTINCT m.id) sent, "
            "COUNT(DISTINCT CASE WHEN e.type='OPENED' THEN m.id END) opened, "
            "COUNT(DISTINCT CASE WHEN e.type='CLICKED' THEN m.id END) clicked, "
            "COUNT(DISTINCT CASE WHEN e.type='UNSUBSCRIBED' THEN m.id END) unsubscribed "
            "FROM messages m LEFT JOIN events e ON e.message_id=m.id "
            "WHERE m.status='SENT' AND m.sent_at>=? GROUP BY m.campaign ORDER BY m.campaign", (since,)):
        campaigns[r["campaign"]] = {
            "sent": r["sent"], "opened": r["opened"], "clicked": r["clicked"], "unsubscribed": r["unsubscribed"],
            "open_rate": round(r["opened"] / r["sent"], 2), "click_rate": round(r["clicked"] / r["sent"], 2)}

    buyers = con.execute("SELECT COUNT(DISTINCT customer_id) FROM orders").fetchone()[0]
    repeaters = con.execute("SELECT COUNT(*) FROM (SELECT customer_id FROM orders GROUP BY customer_id "
                            "HAVING COUNT(*)>=2)").fetchone()[0]
    near_send = 0
    for o in con.execute("SELECT * FROM orders WHERE ordered_at>=?", (since,)).fetchall():
        for s in con.execute("SELECT sent_at FROM messages WHERE customer_id=? AND status='SENT'",
                             (o["customer_id"],)).fetchall():
            sent = _dt(s["sent_at"])
            if sent <= _dt(o["ordered_at"]) <= sent + ATTRIBUTION_WINDOW:
                near_send += 1
                break
    return {
        "window_days": days, "campaigns": campaigns,
        "customers_with_orders": buyers, "repeat_customers": repeaters,
        "repeat_rate": round(repeaters / buyers, 2) if buyers else 0.0,
        "orders_within_7d_after_a_send": near_send,
        "blocked": dict(Counter(r["block_reason"] for r in con.execute(
            "SELECT block_reason FROM messages WHERE status='BLOCKED'"))),
        "message_status": dict(Counter(r["status"] for r in con.execute("SELECT status FROM messages"))),
    }


def format_report(rep):
    lines = [f"Retention report (last {rep['window_days']} days)", "", "Campaign engagement"]
    for name, c in rep["campaigns"].items():
        lines.append(f"  {name:12} sent {c['sent']}  opened {c['opened']} ({c['open_rate']:.0%})  "
                     f"clicked {c['clicked']} ({c['click_rate']:.0%})  unsubscribed {c['unsubscribed']}")
    lines += ["", f"Repeat customers: {rep['repeat_customers']} of {rep['customers_with_orders']} "
                  f"({rep['repeat_rate']:.0%})",
              f"Orders placed within 7 days after a send: {rep['orders_within_7d_after_a_send']} "
              "(timing only; does not prove the email caused the order)",
              f"Blocked messages: {rep['blocked'] or 'none'}", f"All messages: {rep['message_status']}"]
    return "\n".join(lines)
