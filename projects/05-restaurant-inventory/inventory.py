"""Restaurant inventory monitoring and reorder automation prototype.

Sales -> theoretical ingredient usage (via recipes) -> stock levels -> reorder alerts ->
DRAFT purchase requests -> manager/owner approval -> receiving -> variance and usage reports.
The system never places an order itself: people do, after approving a draft.
"""
import csv
import io
import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

LOOKBACK_DAYS = 7          # window for "recent" usage
BASELINE_DAYS = 21         # earlier window used to detect a usage spike
MIN_DATA_DAYS = 3          # trading days needed before usage-based reordering is trusted
CYCLE_DAYS = 7             # each order should cover this many days beyond the lead time
SPIKE_RATIO = 1.4          # recent daily usage this far above the earlier average gets flagged
MAX_PACKS = 20             # sanity cap per line; larger orders must be typed in by a human
MANAGER_LIMIT = 8000.0     # PHP; purchase requests above this need the owner
VARIANCE_PCT = 0.05        # count tolerance: 5% of usage since the last count (or 0.1 pack) ...
DELIVERY_TOL = 0.05        # ... and delivery tolerance of 5%
OPEN_STATUSES = ("DRAFT", "APPROVED", "ORDERED")


def _d(text):
    return date.fromisoformat(text)


def _s(day):
    return day.isoformat()


def connect(path=":memory:"):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def load_master(con, ingredients, recipes):
    with con:
        for i in ingredients:
            con.execute("INSERT OR REPLACE INTO ingredients VALUES (?,?,?,?,?,?,?,?,?)",
                        (i["id"], i["name"], i["unit"], i["supplier"], i["pack_size"], i["lead_time_days"],
                         i["unit_cost"], i["min_threshold"], i.get("safety_days", 1)))
        for dish, parts in recipes.items():
            for ing, qty in parts.items():
                con.execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?)", (dish, ing, qty))


def _ingredient(con, ing_id):
    row = con.execute("SELECT * FROM ingredients WHERE id=?", (ing_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown ingredient {ing_id}")
    return dict(row)


# ---------- recording what happened ----------

def post_sales(con, sales):
    """Explode sales into theoretical ingredient usage. Safe to re-import: sale_id is unique."""
    posted, duplicate, unknown, invalid = 0, 0, [], []
    for s in sales:
        if not isinstance(s.get("qty"), int) or s["qty"] <= 0:
            invalid.append(s.get("sale_id"))
            continue
        recipe = con.execute("SELECT ingredient_id, qty FROM recipes WHERE dish=?", (s["dish"],)).fetchall()
        if not recipe:
            unknown.append(s["sale_id"])   # not stored, so it can be re-imported once the recipe exists
            continue
        with con:
            cur = con.execute("INSERT OR IGNORE INTO sales VALUES (?,?,?,?)",
                              (s["sale_id"], s["day"], s["dish"], s["qty"]))
            if cur.rowcount == 0:
                duplicate += 1
                continue
            for r in recipe:
                con.execute("INSERT OR IGNORE INTO ledger(ingredient_id,day,kind,qty,ref) VALUES (?,?,'USAGE',?,?)",
                            (r["ingredient_id"], s["day"], -r["qty"] * s["qty"], s["sale_id"]))
        posted += 1
    return {"posted": posted, "duplicate": duplicate, "unknown_dish": unknown, "invalid": invalid}


def record_waste(con, ing_id, day, qty, reason, waste_id):
    _ingredient(con, ing_id)
    if qty <= 0:
        raise ValueError("waste quantity must be positive")
    with con:
        con.execute("INSERT OR IGNORE INTO ledger(ingredient_id,day,kind,qty,ref) VALUES (?,?,'WASTE',?,?)",
                    (ing_id, day, -qty, f"{waste_id}:{reason}"))


def record_delivery(con, ing_id, day, qty, ref):
    _ingredient(con, ing_id)
    with con:
        con.execute("INSERT OR IGNORE INTO ledger(ingredient_id,day,kind,qty,ref) VALUES (?,?,'DELIVERY',?,?)",
                    (ing_id, day, qty, ref))


def stock_at(con, ing_id, day):
    """Counted stock at the latest count on or before `day`, plus movements after that count."""
    base = con.execute("SELECT day, qty FROM ledger WHERE ingredient_id=? AND kind='COUNT' AND day<=? "
                       "ORDER BY day DESC, id DESC LIMIT 1", (ing_id, day)).fetchone()
    start, qty = (base["day"], base["qty"]) if base else ("0000-00-00", 0.0)
    moved = con.execute("SELECT COALESCE(SUM(qty),0) FROM ledger WHERE ingredient_id=? AND kind<>'COUNT' "
                        "AND day>? AND day<=?", (ing_id, start, day)).fetchone()[0]
    return qty + moved


def record_count(con, ing_id, day, counted):
    """Physical count at close of business. Compares it with what the records say should be there."""
    ing = _ingredient(con, ing_id)
    if counted < 0:
        raise ValueError("a physical count cannot be negative")
    prior = con.execute("SELECT day FROM ledger WHERE ingredient_id=? AND kind='COUNT' AND day<? "
                        "ORDER BY day DESC, id DESC LIMIT 1", (ing_id, day)).fetchone()
    expected = stock_at(con, ing_id, day)
    if prior is None:
        flag, usage_since, tol = "OPENING", 0.0, 0.0
    else:
        usage_since = _usage(con, ing_id, _s(_d(prior["day"]) + timedelta(days=1)), day)
        tol = max(VARIANCE_PCT * usage_since, 0.1 * ing["pack_size"])
        diff = counted - expected
        flag = "SHORTAGE" if diff < -tol else "SURPLUS" if diff > tol else "OK"
    n = con.execute("SELECT COUNT(*) FROM ledger WHERE ingredient_id=? AND kind='COUNT' AND day=?",
                    (ing_id, day)).fetchone()[0]
    with con:
        con.execute("INSERT INTO ledger(ingredient_id,day,kind,qty,ref) VALUES (?,?,'COUNT',?,?)",
                    (ing_id, day, counted, f"count-{day}-{n + 1}"))
        con.execute("INSERT INTO variances(ingredient_id,day,expected,counted,variance,tolerance,flag) "
                    "VALUES (?,?,?,?,?,?,?)", (ing_id, day, expected, counted, counted - expected, tol, flag))
    return {"ingredient": ing_id, "expected": round(expected, 2), "counted": counted,
            "variance": round(counted - expected, 2), "tolerance": round(tol, 2), "flag": flag}


# ---------- analysis ----------

def _usage(con, ing_id, start, end):
    return con.execute("SELECT COALESCE(-SUM(qty),0) FROM ledger WHERE ingredient_id=? AND kind='USAGE' "
                       "AND day BETWEEN ? AND ?", (ing_id, start, end)).fetchone()[0]


def _trading_days(con, start, end):
    return con.execute("SELECT COUNT(DISTINCT day) FROM sales WHERE day BETWEEN ? AND ?", (start, end)).fetchone()[0]


def analyze(con, ing, today):
    """Stock position for one ingredient. Averages are per trading day (days with sales)."""
    t = _d(today)
    ws = _s(t - timedelta(days=LOOKBACK_DAYS - 1))
    days = _trading_days(con, ws, today)
    low_data = days < MIN_DATA_DAYS
    avg = _usage(con, ing["id"], ws, today) / days if days else 0.0
    bs, be = _s(t - timedelta(days=LOOKBACK_DAYS + BASELINE_DAYS - 1)), _s(t - timedelta(days=LOOKBACK_DAYS))
    bdays = _trading_days(con, bs, be)
    base = _usage(con, ing["id"], bs, be) / bdays if bdays >= MIN_DATA_DAYS else None
    spike = bool(base) and not low_data and avg > SPIKE_RATIO * base
    stock = stock_at(con, ing["id"], today)
    reorder_point = ing["min_threshold"] if low_data else max(
        ing["min_threshold"], avg * (ing["lead_time_days"] + ing["safety_days"]))
    cover = stock / avg if avg > 0 else None
    needs = stock <= reorder_point
    critical = needs and (stock <= 0 or (cover is not None and cover < ing["lead_time_days"]))
    return {"id": ing["id"], "name": ing["name"], "unit": ing["unit"], "supplier": ing["supplier"],
            "stock": round(stock, 2), "avg_daily_use": round(avg, 2),
            "days_of_cover": None if cover is None else round(cover, 1),
            "reorder_point": round(reorder_point, 2),
            "status": "CRITICAL" if critical else "LOW" if needs else "OK",
            "low_data": low_data, "spike": spike, "negative_stock": stock < 0,
            "baseline_daily_use": None if base is None else round(base, 2),
            "stock_value": round(max(stock, 0) * ing["unit_cost"], 2),
            "on_order": round(con.execute(
                "SELECT COALESCE(SUM(l.qty),0) FROM pr_lines l JOIN purchase_requests p ON p.id=l.pr_id "
                "WHERE l.ingredient_id=? AND p.status IN ('APPROVED','ORDERED')", (ing["id"],)).fetchone()[0], 2)}


def stock_report(con, today):
    return [analyze(con, dict(r), today) for r in con.execute("SELECT * FROM ingredients ORDER BY name")]


def alerts(con, today):
    order = {"CRITICAL": 0, "LOW": 1}
    return sorted((r for r in stock_report(con, today) if r["status"] != "OK"), key=lambda r: order[r["status"]])


# ---------- purchasing ----------

def _open_pr_for(con, ing_id):
    return con.execute("SELECT pr_id FROM pr_lines l JOIN purchase_requests p ON p.id=l.pr_id "
                       "WHERE l.ingredient_id=? AND p.status IN ('DRAFT','APPROVED','ORDERED')", (ing_id,)).fetchone()


def generate_reorders(con, today):
    """Create one DRAFT purchase request per supplier for ingredients at or below their reorder point."""
    by_supplier, skipped = {}, []
    for a in alerts(con, today):
        ing = _ingredient(con, a["id"])
        if _open_pr_for(con, ing["id"]):
            skipped.append((ing["id"], "ALREADY_ON_ORDER"))
            continue
        flags = []
        if a["negative_stock"]:
            flags.append("NEGATIVE_STOCK: records show less than zero in stock; check for missing deliveries or counts")
            a["stock"] = 0.0
        if a["low_data"]:
            target = ing["min_threshold"] * 2
            flags.append("LOW_DATA: not enough sales history, sized from the minimum threshold")
        else:
            target = a["avg_daily_use"] * (ing["lead_time_days"] + CYCLE_DAYS)
        if a["spike"]:
            flags.append(f"USAGE_SPIKE: {a['avg_daily_use']} per day recently vs {a['baseline_daily_use']} earlier; "
                         "confirm the spike will continue before approving")
        packs = max(1, math.ceil(max(target - a["stock"], 0) / ing["pack_size"]))
        if packs > MAX_PACKS:
            packs = MAX_PACKS
            flags.append(f"CAPPED_AT_{MAX_PACKS}_PACKS")
        by_supplier.setdefault(ing["supplier"], []).append((ing, packs, "; ".join(flags)))
    ids = []
    with con:
        for supplier, lines in by_supplier.items():
            cur = con.execute("INSERT INTO purchase_requests(supplier,status,created_on) VALUES (?,'DRAFT',?)",
                              (supplier, today))
            pr_id = cur.lastrowid
            for ing, packs, flags in lines:
                con.execute("INSERT INTO pr_lines(pr_id,ingredient_id,packs,qty,unit_cost,flags) VALUES (?,?,?,?,?,?)",
                            (pr_id, ing["id"], packs, packs * ing["pack_size"], ing["unit_cost"], flags))
            _retotal(con, pr_id)
            ids.append(pr_id)
    return ids, skipped


def _retotal(con, pr_id):
    con.execute("UPDATE purchase_requests SET est_total=(SELECT COALESCE(SUM(qty*unit_cost),0) FROM pr_lines "
                "WHERE pr_id=?) WHERE id=?", (pr_id, pr_id))


def _pr(con, pr_id):
    return dict(con.execute("SELECT * FROM purchase_requests WHERE id=?", (pr_id,)).fetchone())


def purchase_request(con, pr_id):
    pr = _pr(con, pr_id)
    pr["lines"] = [dict(r) for r in con.execute(
        "SELECT l.*, i.name, i.unit FROM pr_lines l JOIN ingredients i ON i.id=l.ingredient_id WHERE pr_id=?", (pr_id,))]
    return pr


def adjust_line(con, pr_id, ing_id, packs):
    """A manager changes a quantity on a DRAFT. Zero packs removes the line."""
    if _pr(con, pr_id)["status"] != "DRAFT":
        raise ValueError("only DRAFT purchase requests can be edited")
    if packs < 0:
        raise ValueError("packs cannot be negative")
    ing = _ingredient(con, ing_id)
    with con:
        if packs == 0:
            con.execute("DELETE FROM pr_lines WHERE pr_id=? AND ingredient_id=?", (pr_id, ing_id))
        else:
            con.execute("UPDATE pr_lines SET packs=?, qty=? WHERE pr_id=? AND ingredient_id=?",
                        (packs, packs * ing["pack_size"], pr_id, ing_id))
        _retotal(con, pr_id)
        if con.execute("SELECT COUNT(*) FROM pr_lines WHERE pr_id=?", (pr_id,)).fetchone()[0] == 0:
            con.execute("UPDATE purchase_requests SET status='REJECTED', note='all lines removed' WHERE id=?", (pr_id,))


def approve_pr(con, pr_id, approver, role, today):
    if not (approver or "").strip():
        raise ValueError("an approver name is required")
    if role not in ("manager", "owner"):
        raise PermissionError("only a manager or the owner can approve")
    pr = _pr(con, pr_id)
    if pr["status"] != "DRAFT":
        raise ValueError(f"only DRAFT requests can be approved (this one is {pr['status']})")
    if pr["est_total"] > MANAGER_LIMIT and role != "owner":
        raise PermissionError(f"PHP {pr['est_total']:,.2f} is above the manager limit of PHP {MANAGER_LIMIT:,.2f}; "
                              "the owner must approve")
    with con:
        con.execute("UPDATE purchase_requests SET status='APPROVED', approved_by=?, approver_role=?, decided_on=? "
                    "WHERE id=?", (approver.strip(), role, today, pr_id))


def reject_pr(con, pr_id, approver, reason, today):
    with con:
        con.execute("UPDATE purchase_requests SET status='REJECTED', approved_by=?, decided_on=?, note=? "
                    "WHERE id=? AND status='DRAFT'", (approver, today, reason, pr_id))


def mark_ordered(con, pr_id, today):
    """Record that a person actually placed the order with the supplier."""
    if _pr(con, pr_id)["status"] != "APPROVED":
        raise ValueError("only APPROVED requests can be marked as ordered")
    with con:
        con.execute("UPDATE purchase_requests SET status='ORDERED', ordered_on=? WHERE id=?", (today, pr_id))


def receive_delivery(con, pr_id, received, day):
    """Book what actually arrived. Short or over deliveries are flagged, not hidden."""
    pr = purchase_request(con, pr_id)
    if pr["status"] != "ORDERED":
        raise ValueError(f"only ORDERED requests can be received (this one is {pr['status']})")
    wanted = {l["ingredient_id"] for l in pr["lines"]}
    stray = set(received) - wanted
    if stray:
        raise ValueError(f"not on this purchase request: {sorted(stray)}")
    flags = []
    with con:
        for l in pr["lines"]:
            got = float(received.get(l["ingredient_id"], 0.0))
            con.execute("UPDATE pr_lines SET received_qty=? WHERE id=?", (got, l["id"]))
            if got:
                con.execute("INSERT OR IGNORE INTO ledger(ingredient_id,day,kind,qty,ref) VALUES (?,?,'DELIVERY',?,?)",
                            (l["ingredient_id"], day, got, f"PR{pr_id}"))
            if got < l["qty"] * (1 - DELIVERY_TOL):
                flags.append({"ingredient": l["ingredient_id"], "ordered": l["qty"], "received": got, "flag": "SHORT"})
            elif got > l["qty"] * (1 + DELIVERY_TOL):
                flags.append({"ingredient": l["ingredient_id"], "ordered": l["qty"], "received": got, "flag": "OVER"})
        con.execute("UPDATE purchase_requests SET status='RECEIVED' WHERE id=?", (pr_id,))
    return flags


# ---------- reports ----------

def usage_report(con, start, end):
    rows = []
    for r in con.execute("SELECT * FROM ingredients ORDER BY name"):
        used = _usage(con, r["id"], start, end)
        waste = -con.execute("SELECT COALESCE(SUM(qty),0) FROM ledger WHERE ingredient_id=? AND kind='WASTE' "
                             "AND day BETWEEN ? AND ?", (r["id"], start, end)).fetchone()[0]
        delivered = con.execute("SELECT COALESCE(SUM(qty),0) FROM ledger WHERE ingredient_id=? AND kind='DELIVERY' "
                                "AND day BETWEEN ? AND ?", (r["id"], start, end)).fetchone()[0]
        rows.append({"id": r["id"], "name": r["name"], "unit": r["unit"], "theoretical_usage": round(used, 2),
                     "usage_cost": round(used * r["unit_cost"], 2), "waste_recorded": round(waste, 2),
                     "delivered": round(delivered, 2)})
    return rows


def variance_report(con, flagged_only=True):
    sql = "SELECT v.*, i.name, i.unit FROM variances v JOIN ingredients i ON i.id=v.ingredient_id"
    if flagged_only:
        sql += " WHERE v.flag IN ('SHORTAGE','SURPLUS')"
    return [dict(r) for r in con.execute(sql + " ORDER BY v.day, i.name")]


def to_csv(rows, columns):
    """CSV text that Google Sheets can import (File > Import)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
