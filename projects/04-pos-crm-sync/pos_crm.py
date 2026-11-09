"""POS-to-CRM customer synchronization prototype.

POS webhook -> durable inbox -> match or create customer in the CRM -> record the purchase.
The CRM here is an in-memory stand-in (FakeCRM). To use a real CRM sandbox, write a client with the same
five methods: find_by_email, find_by_phone, create, update, add_purchase.
"""
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_ATTEMPTS = 5


class CRMUnavailable(Exception):
    """Raised when the CRM times out or errors; the transaction is retried later."""


class _Quarantine(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


class FakeCRM:
    """In-memory CRM. `fail_on = {"add_purchase": 1}` makes the next call to that method fail."""

    def __init__(self):
        self.customers, self.purchases, self._n, self.fail_on = {}, {}, 0, {}

    def _guard(self, method):
        if self.fail_on.get(method, 0) > 0:
            self.fail_on[method] -= 1
            raise CRMUnavailable(f"CRM timeout during {method}")

    def find_by_email(self, email):
        self._guard("find_by_email")
        return [dict(c) for c in self.customers.values() if c["email"] == email]

    def find_by_phone(self, phone):
        self._guard("find_by_phone")
        return [dict(c) for c in self.customers.values() if c["phone"] == phone]

    def create(self, data):
        self._guard("create")
        self._n += 1
        cid = f"C{self._n:04d}"
        self.customers[cid] = {"id": cid, "name": data.get("name"), "email": data.get("email"),
                               "phone": data.get("phone")}
        return cid

    def update(self, cid, data):
        self._guard("update")
        self.customers[cid].update(data)

    def add_purchase(self, cid, txn_id, total):
        self._guard("add_purchase")
        self.purchases[txn_id] = {"customer": cid, "total": total}  # keyed by txn_id: retries can't double-count


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=":memory:"):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def norm_email(value):
    value = (value or "").strip().lower()
    return value if EMAIL_RE.match(value) else None


def norm_phone(value):
    """Normalize Philippine mobile numbers to +639XXXXXXXXX. Anything else is treated as invalid."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("639"):
        return "+" + digits
    if len(digits) == 11 and digits.startswith("09"):
        return "+63" + digits[1:]
    if len(digits) == 10 and digits.startswith("9"):
        return "+63" + digits
    return None


def validate(txn):
    errs = []
    if not txn.get("txn_id"):
        errs.append("missing txn_id")
    cust = txn.get("customer") or {}
    if cust.get("email") and not norm_email(cust["email"]):
        errs.append(f"invalid email {cust['email']!r}")
    if cust.get("phone") and not norm_phone(cust["phone"]):
        errs.append(f"invalid phone {cust['phone']!r}")
    lines = txn.get("lines") or []
    if not lines:
        errs.append("no line items")
    try:
        computed = round(sum(float(l["qty"]) * float(l["price"]) for l in lines), 2)
        total = round(float(txn.get("total")), 2)
        if total <= 0:
            errs.append("total must be positive (refunds are out of scope)")
        elif lines and abs(computed - total) > 0.01:
            errs.append(f"total {total} does not match line items {computed}")
    except (KeyError, TypeError, ValueError):
        errs.append("bad line item or total")
    return errs


def _log(con, txn_id, event, detail=""):
    con.execute("INSERT INTO sync_log(txn_id,event,detail,at) VALUES (?,?,?,?)", (txn_id, event, detail, _now()))


def receive(con, payload):
    """Webhook entry point: store first, process later, so an outage never loses a transaction."""
    tid = payload.get("txn_id") if isinstance(payload, dict) else None
    if not tid:
        with con:
            _log(con, None, "REJECTED", "payload has no txn_id and cannot be stored safely")
        return "REJECTED"
    with con:
        cur = con.execute("INSERT OR IGNORE INTO inbox(txn_id,payload,status,received_at) VALUES (?,?,'PENDING',?)",
                          (tid, json.dumps(payload), _now()))
        if cur.rowcount == 0:
            _log(con, tid, "DUPLICATE_WEBHOOK", "already in inbox; ignored")
            return "DUPLICATE_WEBHOOK"
        _log(con, tid, "RECEIVED")
    return "RECEIVED"


def _quarantine(con, tid, code, detail):
    with con:
        con.execute("INSERT INTO quarantine(txn_id,code,detail,at) VALUES (?,?,?,?)", (tid, code, detail, _now()))
        con.execute("UPDATE inbox SET status='QUARANTINED', last_error=? WHERE txn_id=?", (f"{code}: {detail}", tid))
        _log(con, tid, code, detail)
    return "QUARANTINED"


def _sync(crm, txn):
    cust = txn.get("customer") or {}
    email, phone = norm_email(cust.get("email")), norm_phone(cust.get("phone"))
    rec_e = {c["id"]: c for c in crm.find_by_email(email)} if email else {}
    rec_p = {c["id"]: c for c in crm.find_by_phone(phone)} if phone else {}
    ids_e, ids_p = set(rec_e), set(rec_p)

    if len(ids_e) > 1 or len(ids_p) > 1 or (ids_e and ids_p and ids_e != ids_p):
        raise _Quarantine("MATCH_CONFLICT",
                          f"email matches {sorted(ids_e)}, phone matches {sorted(ids_p)}; a human must merge profiles")

    if ids_e or ids_p:
        cid = next(iter(ids_e or ids_p))
        outcome = "MATCHED_EMAIL" if ids_e else "MATCHED_PHONE"
        rec = (rec_e or rec_p)[cid]
        fill = {k: v for k, v in (("email", email), ("phone", phone), ("name", cust.get("name")))
                if v and not rec.get(k)}  # fill blanks only; never overwrite existing CRM data
        if fill:
            crm.update(cid, fill)
    elif email or phone:
        if not cust.get("name"):
            raise _Quarantine("INSUFFICIENT_DATA", "contact details but no name; cannot create a profile")
        cid, outcome = crm.create({"name": cust["name"], "email": email, "phone": phone}), "CREATED"
    else:
        cid, outcome = None, "ANONYMOUS"

    if cid:
        crm.add_purchase(cid, txn["txn_id"], round(float(txn["total"]), 2))
    return outcome, cid


def process_one(con, crm, tid):
    row = con.execute("SELECT payload, attempts FROM inbox WHERE txn_id=?", (tid,)).fetchone()
    txn = json.loads(row["payload"])
    errs = validate(txn)
    if errs:
        return _quarantine(con, tid, "INCONSISTENT_DATA", "; ".join(errs))
    try:
        outcome, cid = _sync(crm, txn)
    except _Quarantine as q:
        return _quarantine(con, tid, q.code, q.detail)
    except CRMUnavailable as e:
        with con:
            con.execute("UPDATE inbox SET status='FAILED', attempts=attempts+1, last_error=? WHERE txn_id=?",
                        (str(e), tid))
            _log(con, tid, "CRM_UNAVAILABLE", str(e))
            if row["attempts"] + 1 >= MAX_ATTEMPTS:
                _log(con, tid, "GAVE_UP", f"{MAX_ATTEMPTS} attempts used; needs manual replay")
        return "FAILED"
    with con:
        con.execute("INSERT OR REPLACE INTO txn_map(txn_id,crm_id,outcome,synced_at) VALUES (?,?,?,?)",
                    (tid, cid, outcome, _now()))
        con.execute("UPDATE inbox SET status='DONE', last_error=NULL WHERE txn_id=?", (tid,))
        _log(con, tid, outcome, cid or "")
    return "DONE"


def process_pending(con, crm):
    """Process new and previously failed transactions. This is also the recovery routine after an outage."""
    ids = [r["txn_id"] for r in con.execute(
        "SELECT txn_id FROM inbox WHERE status IN ('PENDING','FAILED') AND attempts<? ORDER BY rowid", (MAX_ATTEMPTS,))]
    return Counter(process_one(con, crm, t) for t in ids)


def resolve_quarantine(con, crm, tid, corrected=None):
    """After a human fixes the data (or merges CRM profiles), replay the transaction."""
    with con:
        con.execute("UPDATE quarantine SET resolved=1 WHERE txn_id=? AND resolved=0", (tid,))
        if corrected is not None:
            corrected = dict(corrected, txn_id=tid)
            con.execute("UPDATE inbox SET payload=? WHERE txn_id=?", (json.dumps(corrected), tid))
        con.execute("UPDATE inbox SET status='PENDING', attempts=0, last_error=NULL WHERE txn_id=?", (tid,))
        _log(con, tid, "REPLAY", "resolved by a human")
    return process_one(con, crm, tid)


def summary(con):
    status = dict(Counter(r["status"] for r in con.execute("SELECT status FROM inbox")))
    open_q = [dict(r) for r in con.execute(
        "SELECT txn_id, code, detail FROM quarantine WHERE resolved=0 ORDER BY id")]
    return {"inbox": status, "open_quarantine": open_q}
