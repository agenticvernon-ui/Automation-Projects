"""Order-to-fulfillment pipeline. SQLite stands in for PostgreSQL in this prototype."""
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

TRANSITIONS = {
    "RECEIVED": {"READY_TO_FULFILL", "EXCEPTION"},
    "EXCEPTION": {"READY_TO_FULFILL", "CANCELLED"},
    "READY_TO_FULFILL": {"SHIPPED", "CANCELLED"},
    "SHIPPED": {"DELIVERED"},
    "DELIVERED": set(),
    "CANCELLED": set(),
}


def _now(offset_min=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_min)).isoformat(timespec="seconds")


def connect(path=":memory:"):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def load_inventory(con, stock):
    with con:
        for sku, qty in stock.items():
            con.execute(
                "INSERT INTO inventory(sku,on_hand,reserved) VALUES (?,?,0) "
                "ON CONFLICT(sku) DO UPDATE SET on_hand=excluded.on_hand", (sku, qty))


def _lines(order):
    """Aggregate line items by SKU (the same SKU may appear twice)."""
    totals = Counter()
    for item in order.get("items") or []:
        try:
            totals[str(item.get("sku", "")).strip()] += int(item.get("qty", 0))
        except (TypeError, ValueError):
            totals[str(item.get("sku", "")).strip()] += 0
    return totals


def validate(order):
    errs = []
    if not order.get("order_id"):
        errs.append("missing order_id")
    c = order.get("customer") or {}
    if not c.get("name"):
        errs.append("missing customer name")
    if not EMAIL_RE.match(str(c.get("email") or "")):
        errs.append("missing or invalid customer email")
    if not c.get("ship_address"):
        errs.append("missing shipping address")
    if not order.get("items"):
        errs.append("no line items")
    for sku, qty in _lines(order).items():
        if not sku or qty <= 0:
            errs.append(f"bad line item (sku={sku!r}, qty={qty})")
    return errs


def fingerprint(order):
    email = str((order.get("customer") or {}).get("email", "")).strip().lower()
    basket = sorted(_lines(order).items())
    return hashlib.sha1(f"{email}|{basket}".encode()).hexdigest()


def _history(con, oid, old, new, note):
    con.execute("INSERT INTO status_history(order_id,old_status,new_status,note,at) VALUES (?,?,?,?,?)",
                (oid, old, new, note, _now()))


def set_status(con, oid, new, note=""):
    row = con.execute("SELECT status FROM orders WHERE order_id=?", (oid,)).fetchone()
    if row is None:
        raise KeyError(f"unknown order {oid}")
    if new not in TRANSITIONS[row["status"]]:
        raise ValueError(f"illegal transition {row['status']} -> {new}")
    con.execute("UPDATE orders SET status=? WHERE order_id=?", (new, oid))
    _history(con, oid, row["status"], new, note)


def _exception(con, oid, code, detail):
    con.execute("INSERT INTO exceptions(order_id,code,detail,at) VALUES (?,?,?,?)", (oid, code, detail, _now()))


def _fail(con, oid, code, detail):
    _exception(con, oid, code, detail)
    set_status(con, oid, "EXCEPTION", f"{code}: {detail}")
    return "EXCEPTION"


def process_order(con, order, dup_window_min=60):
    """Validate, de-duplicate, update the customer, check/reserve stock, track status."""
    fp = fingerprint(order)
    oid = order.get("order_id") or f"UNKEYED-{fp[:8]}"

    if con.execute("SELECT 1 FROM orders WHERE order_id=?", (oid,)).fetchone():
        with con:
            _exception(con, oid, "DUPLICATE_ORDER_ID", "order id already processed; ignored (idempotent)")
        return "IGNORED_DUPLICATE"

    with con:
        con.execute("INSERT INTO orders(order_id,fingerprint,status,payload,created_at) VALUES (?,?,?,?,?)",
                    (oid, fp, "RECEIVED", json.dumps(order), _now()))
        _history(con, oid, None, "RECEIVED", "order received")

        errs = validate(order)
        if errs:
            return _fail(con, oid, "INVALID_ORDER", "; ".join(errs))

        twin = con.execute(
            "SELECT order_id FROM orders WHERE fingerprint=? AND order_id<>? "
            "AND status<>'CANCELLED' AND created_at>=?", (fp, oid, _now(-dup_window_min))).fetchone()
        if twin:
            return _fail(con, oid, "DUPLICATE_SUSPECTED",
                         f"same customer and basket as {twin['order_id']} within {dup_window_min} min")

        c = order["customer"]
        con.execute(
            "INSERT INTO customers(email,name,ship_address,order_count,updated_at) VALUES (?,?,?,1,?) "
            "ON CONFLICT(email) DO UPDATE SET name=excluded.name, ship_address=excluded.ship_address, "
            "order_count=order_count+1, updated_at=excluded.updated_at",
            (c["email"].strip().lower(), c["name"], c["ship_address"], _now()))

        lines, problems = _lines(order), []
        for sku, qty in lines.items():
            row = con.execute("SELECT on_hand, reserved FROM inventory WHERE sku=?", (sku,)).fetchone()
            if row is None:
                problems.append(f"{sku}: unknown SKU")
            elif row["on_hand"] - row["reserved"] < qty:
                problems.append(f"{sku}: need {qty}, available {row['on_hand'] - row['reserved']}")
        if problems:
            return _fail(con, oid, "INVENTORY_SHORTFALL", "; ".join(problems))

        for sku, qty in lines.items():
            con.execute("UPDATE inventory SET reserved=reserved+? WHERE sku=?", (qty, sku))
        set_status(con, oid, "READY_TO_FULFILL", "stock reserved")
        return "READY_TO_FULFILL"


def _release(con, oid):
    order = json.loads(con.execute("SELECT payload FROM orders WHERE order_id=?", (oid,)).fetchone()["payload"])
    return order, _lines(order)


def ship(con, oid):
    with con:
        _, lines = _release(con, oid)
        set_status(con, oid, "SHIPPED", "handed to carrier")  # raises if not READY_TO_FULFILL
        for sku, qty in lines.items():
            con.execute("UPDATE inventory SET on_hand=on_hand-?, reserved=reserved-? WHERE sku=?", (qty, qty, sku))


def cancel(con, oid):
    with con:
        _, lines = _release(con, oid)
        was = con.execute("SELECT status FROM orders WHERE order_id=?", (oid,)).fetchone()["status"]
        set_status(con, oid, "CANCELLED", "cancelled")
        if was == "READY_TO_FULFILL":
            for sku, qty in lines.items():
                con.execute("UPDATE inventory SET reserved=reserved-? WHERE sku=?", (qty, sku))


def track(con, oid):
    return [dict(r) for r in con.execute(
        "SELECT old_status,new_status,note,at FROM status_history WHERE order_id=? ORDER BY id", (oid,))]


def inventory_discrepancies(con):
    """Compare stored reservations with what READY_TO_FULFILL orders say should be reserved."""
    expected = Counter()
    for r in con.execute("SELECT payload FROM orders WHERE status='READY_TO_FULFILL'"):
        expected.update(_lines(json.loads(r["payload"])))
    out = []
    for r in con.execute("SELECT sku,on_hand,reserved FROM inventory"):
        exp = expected.get(r["sku"], 0)
        if r["reserved"] != exp or r["reserved"] > r["on_hand"] or r["reserved"] < 0:
            out.append({"sku": r["sku"], "stored_reserved": r["reserved"],
                        "expected_reserved": exp, "on_hand": r["on_hand"]})
    return out
