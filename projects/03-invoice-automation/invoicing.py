"""Accounting and invoice automation prototype.

Approved order -> checks -> DRAFT invoice (or exception queue) -> human approval -> number assigned ->
posted to the accounting system -> payments -> status, aging and exception reports.
The accounting system is a stand-in (FakeAccounting). A QuickBooks Online or Xero sandbox client would implement
the same three methods: create_invoice, record_payment, void_invoice.
Money is handled with Decimal and stored as integer centavos.
"""
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CENT = Decimal("0.01")

# Predefined billing rules. These are SIMULATED settings: confirm real tax treatment with an accountant.
RULES = {
    "currency": "PHP",
    "vat_rate": Decimal("0.12"),
    "shipping_taxable": True,
    "max_discount_pct": Decimal("0.20"),
    "high_value_cents": 10_000_000,        # PHP 100,000.00: needs controller release
    "accountant_limit_cents": 5_000_000,   # PHP 50,000.00: above this the controller must approve the invoice
    "duplicate_window_days": 30,
}
MAX_POST_ATTEMPTS = 5
FOLLOWUP_COOLDOWN_DAYS = 7                 # no new reminder within a week of the last one sent
ESCALATE_AFTER_DAYS = 60                   # beyond this, a person decides; no more automated emails
STAGES = ((1, 7, "REMINDER_1"), (8, 30, "REMINDER_2"), (31, 60, "FINAL_REMINDER"))
BANNED_COLLECTION = ("legal action", "lawyer", "blacklist", "court", "police", "final warning", "shame")
ROLE_RANK = {"accountant": 1, "controller": 2}
BLOCKING = {"NOT_APPROVED", "UNKNOWN_CUSTOMER", "MISSING_CUSTOMER_INFO", "INVALID_LINE", "INVALID_AMOUNT",
            "AMOUNT_MISMATCH", "WRONG_CURRENCY"}
RELEASE_ROLE = {"POSSIBLE_DUPLICATE": "accountant", "HIGH_VALUE_REVIEW": "controller", "CREDIT_LIMIT": "controller"}


class AccountingUnavailable(Exception):
    """The accounting system timed out or errored; posting is retried later."""


class _Invalid(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


# ---------- money helpers ----------

def dec(value):
    d = Decimal(str(value))
    if not d.is_finite():
        raise InvalidOperation("not a finite number")
    return d


def q(d):
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def cents(d):
    return int(q(d) * 100)


def php(c):
    return f"PHP {Decimal(c) / 100:,.2f}"


# ---------- stand-in accounting system ----------

class FakeAccounting:
    """In-memory accounting system. `fail_next = n` makes the next n calls raise AccountingUnavailable."""

    def __init__(self):
        self.invoices, self.by_ref, self.payments, self.fail_next = {}, {}, {}, 0

    def _guard(self):
        if self.fail_next > 0:
            self.fail_next -= 1
            raise AccountingUnavailable("accounting system timeout")

    def create_invoice(self, payload):
        self._guard()
        ref = payload["reference"]
        if ref in self.by_ref:                       # idempotent: a retry returns the existing invoice
            return self.by_ref[ref]
        self.invoices[payload["number"]] = dict(payload, paid_cents=0, status="OPEN")
        self.by_ref[ref] = payload["number"]
        return payload["number"]

    def record_payment(self, number, amount_c, ref):
        self._guard()
        if ref not in self.payments:
            self.payments[ref] = (number, amount_c)
            self.invoices[number]["paid_cents"] += amount_c

    def void_invoice(self, number):
        self._guard()
        self.invoices[number]["status"] = "VOID"


# ---------- database ----------

def connect(path=":memory:"):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _audit(con, actor, action, ref="", detail=""):
    con.execute("INSERT INTO audit_log(at,actor,action,ref,detail) VALUES (datetime('now'),?,?,?,?)",
                (actor, action, ref, detail))


def load_customers(con, customers):
    with con:
        for c in customers:
            limit = c.get("credit_limit")
            con.execute("INSERT OR REPLACE INTO customers VALUES (?,?,?,?,?,?,?)",
                        (c["id"], c.get("name"), c.get("email"), c.get("billing_address"),
                         c.get("terms_days", 30), None if limit is None else cents(dec(limit)),
                         int(c.get("vat_exempt", 0))))


def ingest_order(con, order):
    """Store an order for processing. Re-delivery of the same order_id is ignored."""
    oid = order.get("order_id") if isinstance(order, dict) else None
    if not oid:
        return "REJECTED_NO_ORDER_ID"
    with con:
        cur = con.execute("INSERT OR IGNORE INTO orders(order_id,payload,status) VALUES (?,?,'NEW')",
                          (oid, json.dumps(order)))
    return "RECEIVED" if cur.rowcount else "DUPLICATE_ORDER_ID"


# ---------- calculation ----------

def compute(order, customer):
    """Apply the billing rules. Raises _Invalid for lines or amounts that cannot be invoiced."""
    items = order.get("items") or []
    if not items:
        raise _Invalid("INVALID_LINE", "no line items")
    lines, subtotal = [], Decimal(0)
    for i, item in enumerate(items, 1):
        try:
            qty, price = dec(item["qty"]), dec(item["unit_price"])
        except (KeyError, InvalidOperation, ValueError):
            raise _Invalid("INVALID_LINE", f"line {i}: missing or non-numeric qty or unit_price")
        if not (item.get("sku") or item.get("description")):
            raise _Invalid("INVALID_LINE", f"line {i}: needs a sku or description")
        if qty <= 0 or price < 0:
            raise _Invalid("INVALID_LINE", f"line {i}: quantity must be positive and price cannot be negative")
        net = q(qty * price)
        subtotal += net
        lines.append({"sku": item.get("sku"), "description": item.get("description") or item.get("sku"),
                      "qty": str(qty), "unit_price_c": cents(price), "net_c": cents(net)})
    try:
        pct, shipping = dec(order.get("discount_pct", 0)), dec(order.get("shipping", 0))
    except (InvalidOperation, ValueError):
        raise _Invalid("INVALID_AMOUNT", "discount or shipping is not a number")
    if pct < 0 or pct > RULES["max_discount_pct"] or shipping < 0:
        raise _Invalid("INVALID_AMOUNT", f"discount must be 0 to {RULES['max_discount_pct']:.0%} and shipping cannot be negative")
    discount = q(subtotal * pct)
    taxable = subtotal - discount + (shipping if RULES["shipping_taxable"] else Decimal(0))
    vat = Decimal(0) if customer["vat_exempt"] else q(taxable * RULES["vat_rate"])
    total = subtotal - discount + shipping + vat
    if total <= 0:
        raise _Invalid("INVALID_AMOUNT", "invoice total must be greater than zero")
    return {"lines": lines, "subtotal_c": cents(subtotal), "discount_c": cents(discount),
            "shipping_c": cents(shipping), "vat_c": cents(vat), "total_c": cents(total)}


def _fingerprint(customer_id, lines):
    return json.dumps([customer_id, sorted((l["sku"] or l["description"], l["qty"]) for l in lines)])


def _outstanding_c(con, customer_id):
    row = con.execute(
        "SELECT COALESCE(SUM(i.total_c - COALESCE((SELECT SUM(amount_c) FROM payments p WHERE p.invoice_id=i.id),0)),0) "
        "FROM invoices i WHERE i.customer_id=? AND i.status IN ('APPROVED','POSTED','PARTIALLY_PAID')",
        (customer_id,)).fetchone()
    return row[0]


# ---------- processing ----------

def process_order(con, order_id, today):
    """Run every check on an approved order, then create a DRAFT invoice or place it in the exception queue."""
    row = con.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if con.execute("SELECT 1 FROM invoices WHERE order_id=?", (order_id,)).fetchone():
        return "ALREADY_INVOICED"
    order, overrides = json.loads(row["payload"]), set(json.loads(row["overrides"]))
    cust = con.execute("SELECT * FROM customers WHERE id=?", (order.get("customer_id"),)).fetchone()
    problems, calc = [], None

    if not (order.get("approved_by") or "").strip() or not order.get("approved_at"):
        problems.append(("NOT_APPROVED", "the order has no approver name or approval date"))
    if (order.get("currency") or RULES["currency"]) != RULES["currency"]:
        problems.append(("WRONG_CURRENCY", f"order currency {order.get('currency')} is not {RULES['currency']}"))
    if cust is None:
        problems.append(("UNKNOWN_CUSTOMER", f"customer {order.get('customer_id')!r} is not in the customer master"))
    else:
        missing = [f for f in ("name", "billing_address") if not (cust[f] or "").strip()]
        if not EMAIL_RE.match(cust["email"] or ""):
            missing.append("valid email")
        if missing:
            problems.append(("MISSING_CUSTOMER_INFO", "customer is missing: " + ", ".join(missing)))
        try:
            calc = compute(order, cust)
        except _Invalid as e:
            problems.append((e.code, e.detail))

    if calc:
        if order.get("total") is not None:
            try:
                stated = cents(dec(order["total"]))
            except (InvalidOperation, ValueError):
                stated = None
            if stated != calc["total_c"]:
                problems.append(("AMOUNT_MISMATCH", f"order says {order['total']}, billing rules give "
                                 f"{Decimal(calc['total_c']) / 100:.2f}"))
        fp = _fingerprint(cust["id"], calc["lines"])
        twin = con.execute("SELECT order_id FROM invoices WHERE fingerprint=? AND status<>'VOID' AND created_on>=?",
                           (fp, (date.fromisoformat(today) - timedelta(days=RULES["duplicate_window_days"])).isoformat())).fetchone()
        if twin:
            problems.append(("POSSIBLE_DUPLICATE", f"same customer and items as invoice for order {twin['order_id']}"))
        if calc["total_c"] >= RULES["high_value_cents"]:
            problems.append(("HIGH_VALUE_REVIEW", f"{php(calc['total_c'])} is at or above the high-value threshold"))
        limit = cust["credit_limit_cents"]
        if limit is not None and _outstanding_c(con, cust["id"]) + calc["total_c"] > limit:
            problems.append(("CREDIT_LIMIT", f"outstanding {php(_outstanding_c(con, cust['id']))} plus this invoice "
                             f"exceeds the limit of {php(limit)}"))

    problems = [(c, d) for c, d in problems if c not in overrides]
    with con:
        con.execute("UPDATE exceptions SET resolved=1, resolved_by='system', resolution='superseded by reprocessing' "
                    "WHERE order_id=? AND resolved=0", (order_id,))
        if problems:
            for code, detail in problems:
                con.execute("INSERT INTO exceptions(order_id,code,severity,detail,created_on) VALUES (?,?,?,?,?)",
                            (order_id, code, "BLOCKING" if code in BLOCKING else "REVIEW", detail, today))
            con.execute("UPDATE orders SET status='EXCEPTION' WHERE order_id=?", (order_id,))
            return "EXCEPTION: " + ", ".join(sorted(c for c, _ in problems))
        con.execute("INSERT INTO invoices(order_id,customer_id,status,fingerprint,created_on,terms_days,lines,subtotal_c,"
                    "discount_c,shipping_c,vat_c,total_c) VALUES (?,?,'DRAFT',?,?,?,?,?,?,?,?,?)",
                    (order_id, cust["id"], _fingerprint(cust["id"], calc["lines"]), today, cust["terms_days"],
                     json.dumps(calc["lines"]), calc["subtotal_c"], calc["discount_c"], calc["shipping_c"],
                     calc["vat_c"], calc["total_c"]))
        con.execute("UPDATE orders SET status='DRAFTED' WHERE order_id=?", (order_id,))
        _audit(con, "system", "DRAFT_CREATED", order_id, php(calc["total_c"]))
    return "DRAFT_CREATED"


def correct_order(con, order_id, corrected, user, today):
    """Replace the order data after a human fixes it. Earlier releases are cleared, then the order is re-checked."""
    if con.execute("SELECT 1 FROM invoices WHERE order_id=?", (order_id,)).fetchone():
        raise ValueError("this order already has an invoice")
    with con:
        con.execute("UPDATE orders SET payload=?, overrides='[]' WHERE order_id=?",
                    (json.dumps(dict(corrected, order_id=order_id)), order_id))
        _audit(con, user, "ORDER_CORRECTED", order_id)
    return process_order(con, order_id, today)


def release_exception(con, order_id, codes, reviewer, role, note, today):
    """A person confirms a REVIEW exception is acceptable. BLOCKING exceptions can only be fixed, not released."""
    if not (reviewer or "").strip() or not (note or "").strip():
        raise ValueError("a reviewer name and a note are required")
    if role not in ROLE_RANK:
        raise PermissionError("unknown role")
    row = con.execute("SELECT overrides FROM orders WHERE order_id=?", (order_id,)).fetchone()
    open_codes = {r["code"] for r in con.execute("SELECT code FROM exceptions WHERE order_id=? AND resolved=0", (order_id,))}
    for code in codes:
        if code not in RELEASE_ROLE:
            raise ValueError(f"{code} cannot be released; correct the order data instead")
        if code not in open_codes:
            raise ValueError(f"{code} is not an open exception on {order_id}")
        if ROLE_RANK[role] < ROLE_RANK[RELEASE_ROLE[code]]:
            raise PermissionError(f"releasing {code} needs the {RELEASE_ROLE[code]}")
    with con:
        for code in codes:
            con.execute("UPDATE exceptions SET resolved=1, resolved_by=?, resolution=? WHERE order_id=? AND code=? AND resolved=0",
                        (reviewer.strip(), note.strip(), order_id, code))
        con.execute("UPDATE orders SET overrides=? WHERE order_id=?",
                    (json.dumps(sorted(set(json.loads(row["overrides"])) | set(codes))), order_id))
        _audit(con, reviewer.strip(), "EXCEPTION_RELEASED", order_id, f"{','.join(codes)}: {note.strip()}")
    return process_order(con, order_id, today)


# ---------- approval, posting, payments ----------

def _invoice(con, number):
    row = con.execute("SELECT * FROM invoices WHERE number=?", (number,)).fetchone()
    if row is None:
        raise KeyError(f"unknown invoice {number}")
    return dict(row)


def approve_invoice(con, invoice_id, approver, role, today):
    """Controls: a named approver, a different person from the one who approved the order, and a role limit."""
    if not (approver or "").strip():
        raise ValueError("an approver name is required")
    if role not in ROLE_RANK:
        raise PermissionError("only an accountant or controller can approve invoices")
    inv = con.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if inv is None or inv["status"] != "DRAFT":
        raise ValueError("only DRAFT invoices can be approved")
    order = json.loads(con.execute("SELECT payload FROM orders WHERE order_id=?", (inv["order_id"],)).fetchone()[0])
    if approver.strip().lower() == (order.get("approved_by") or "").strip().lower():
        raise PermissionError("the person who approved the order cannot approve its invoice (separation of duties)")
    if inv["total_c"] > RULES["accountant_limit_cents"] and role != "controller":
        raise PermissionError(f"{php(inv['total_c'])} is above the accountant limit of "
                              f"{php(RULES['accountant_limit_cents'])}; the controller must approve")
    year = date.fromisoformat(today).year
    with con:
        con.execute("INSERT OR IGNORE INTO counters VALUES (?,0)", (f"INV-{year}",))
        con.execute("UPDATE counters SET value=value+1 WHERE name=?", (f"INV-{year}",))
        n = con.execute("SELECT value FROM counters WHERE name=?", (f"INV-{year}",)).fetchone()[0]
        number = f"INV-{year}-{n:04d}"
        due = (date.fromisoformat(today) + timedelta(days=inv["terms_days"])).isoformat()
        con.execute("UPDATE invoices SET status='APPROVED', number=?, issue_date=?, due_date=?, approved_by=?, "
                    "approver_role=? WHERE id=?", (number, today, due, approver.strip(), role, invoice_id))
        _audit(con, approver.strip(), "INVOICE_APPROVED", number, php(inv["total_c"]))
    return number


def reject_draft(con, invoice_id, actor, reason):
    if not (reason or "").strip():
        raise ValueError("a reason is required")
    with con:
        cur = con.execute("UPDATE invoices SET status='REJECTED', last_error=? WHERE id=? AND status='DRAFT'",
                          (reason.strip(), invoice_id))
        if cur.rowcount:
            _audit(con, actor, "DRAFT_REJECTED", str(invoice_id), reason.strip())


def _payload(con, inv):
    cust = con.execute("SELECT * FROM customers WHERE id=?", (inv["customer_id"],)).fetchone()
    return {"number": inv["number"], "reference": inv["order_id"], "customer": cust["name"], "email": cust["email"],
            "issue_date": inv["issue_date"], "due_date": inv["due_date"], "lines": json.loads(inv["lines"]),
            "subtotal_cents": inv["subtotal_c"], "vat_cents": inv["vat_c"], "total_cents": inv["total_c"],
            "currency": RULES["currency"]}


def post_approved(con, acct, today):
    """Push APPROVED invoices to the accounting system. Failures are retried; the order id is the idempotency key."""
    results = Counter()
    for inv in map(dict, con.execute("SELECT * FROM invoices WHERE status='APPROVED' AND post_attempts<? ORDER BY id",
                                     (MAX_POST_ATTEMPTS,)).fetchall()):
        try:
            acct.create_invoice(_payload(con, inv))
        except AccountingUnavailable as e:
            with con:
                con.execute("UPDATE invoices SET post_attempts=post_attempts+1, last_error=? WHERE id=?", (str(e), inv["id"]))
                if inv["post_attempts"] + 1 >= MAX_POST_ATTEMPTS:
                    con.execute("INSERT INTO exceptions(order_id,code,severity,detail,created_on) VALUES (?,?,?,?,?)",
                                (inv["order_id"], "POSTING_FAILED", "REVIEW",
                                 f"{inv['number']} could not be posted after {MAX_POST_ATTEMPTS} attempts", today))
            results["FAILED"] += 1
            continue
        with con:
            con.execute("UPDATE invoices SET status='POSTED', posted_at=?, last_error=NULL WHERE id=?", (today, inv["id"]))
            _audit(con, "system", "INVOICE_POSTED", inv["number"])
        results["POSTED"] += 1
    return results


def paid_c(con, invoice_id):
    return con.execute("SELECT COALESCE(SUM(amount_c),0) FROM payments WHERE invoice_id=?", (invoice_id,)).fetchone()[0]


def record_payment(con, acct, number, amount, paid_on, ref):
    inv = _invoice(con, number)
    if inv["status"] not in ("POSTED", "PARTIALLY_PAID"):
        raise ValueError(f"cannot record a payment on a {inv['status']} invoice")
    amount_c = cents(dec(amount))
    if amount_c <= 0:
        raise ValueError("payment must be positive")
    if con.execute("SELECT 1 FROM payments WHERE ref=?", (ref,)).fetchone():
        return "DUPLICATE_PAYMENT"
    balance = inv["total_c"] - paid_c(con, inv["id"])
    if amount_c > balance:
        raise ValueError(f"payment {php(amount_c)} exceeds the balance {php(balance)}; record any overpayment as a customer credit")
    acct.record_payment(number, amount_c, ref)             # idempotent on ref, so a retry after an error is safe
    with con:
        con.execute("INSERT INTO payments(invoice_id,ref,amount_c,paid_on) VALUES (?,?,?,?)", (inv["id"], ref, amount_c, paid_on))
        status = "PAID" if amount_c == balance else "PARTIALLY_PAID"
        con.execute("UPDATE invoices SET status=? WHERE id=?", (status, inv["id"]))
        _audit(con, "system", "PAYMENT_RECORDED", number, f"{php(amount_c)} ref {ref}")
    return status


def void_invoice(con, acct, number, actor, reason):
    """Void an unpaid invoice. The number is never reused. Paid invoices need a credit note (not built)."""
    inv = _invoice(con, number)
    if not (reason or "").strip():
        raise ValueError("a reason is required")
    if inv["status"] not in ("APPROVED", "POSTED") or paid_c(con, inv["id"]):
        raise ValueError("only unpaid APPROVED or POSTED invoices can be voided; use a credit note otherwise")
    if inv["status"] == "POSTED":
        acct.void_invoice(number)
    with con:
        con.execute("UPDATE invoices SET status='VOID', last_error=? WHERE id=?", (reason.strip(), inv["id"]))
        _audit(con, actor, "INVOICE_VOIDED", number, reason.strip())


# ---------- reports ----------

def status_summary(con):
    return {r["status"]: {"count": r["n"], "total": php(r["t"])} for r in con.execute(
        "SELECT status, COUNT(*) n, SUM(total_c) t FROM invoices GROUP BY status ORDER BY status")}


def aging_report(con, today):
    """Outstanding balances of POSTED and PARTIALLY_PAID invoices by customer and days past due."""
    now = date.fromisoformat(today)
    buckets = ("Current", "1-30", "31-60", "61-90", "90+")
    by_customer = defaultdict(lambda: dict.fromkeys(buckets, 0))
    overdue = []
    for inv in con.execute("SELECT i.*, c.name FROM invoices i JOIN customers c ON c.id=i.customer_id "
                           "WHERE i.status IN ('POSTED','PARTIALLY_PAID') ORDER BY i.due_date"):
        balance = inv["total_c"] - paid_c(con, inv["id"])
        late = (now - date.fromisoformat(inv["due_date"])).days
        b = "Current" if late <= 0 else "1-30" if late <= 30 else "31-60" if late <= 60 else "61-90" if late <= 90 else "90+"
        by_customer[inv["name"]][b] += balance
        if late > 0:
            overdue.append({"number": inv["number"], "customer": inv["name"], "days_past_due": late, "balance": php(balance)})
    rows = [dict(customer=name, **{b: php(v) for b, v in amounts.items()}, total=php(sum(amounts.values())))
            for name, amounts in sorted(by_customer.items())]
    return {"as_of": today, "customers": rows, "overdue_invoices": overdue,
            "total_outstanding": php(sum(sum(a.values()) for a in by_customer.values()))}


def exceptions_report(con):
    rows = con.execute("SELECT code, severity, order_id, detail FROM exceptions WHERE resolved=0 ORDER BY code, order_id")
    return [dict(r) for r in rows]


def reconcile(con, acct):
    """Compare invoices in this system with the accounting system. Anything that differs needs a human."""
    issues, local = [], set()
    for inv in con.execute("SELECT * FROM invoices WHERE status IN ('POSTED','PARTIALLY_PAID','PAID') ORDER BY number"):
        local.add(inv["number"])
        remote = acct.invoices.get(inv["number"])
        if remote is None:
            issues.append({"number": inv["number"], "issue": "MISSING_IN_ACCOUNTING"})
            continue
        if remote["total_cents"] != inv["total_c"]:
            issues.append({"number": inv["number"], "issue": "AMOUNT_DIFFERS",
                           "here": php(inv["total_c"]), "accounting": php(remote["total_cents"])})
        if remote["paid_cents"] != paid_c(con, inv["id"]):
            issues.append({"number": inv["number"], "issue": "PAYMENT_DIFFERS",
                           "here": php(paid_c(con, inv["id"])), "accounting": php(remote["paid_cents"])})
    for number, remote in acct.invoices.items():
        if number not in local and remote["status"] != "VOID":
            issues.append({"number": number, "issue": "EXTRA_IN_ACCOUNTING"})
    return issues


# ---------- payment follow-ups ----------

def mark_disputed(con, number, actor, reason):
    """Stop automated follow-ups on an invoice the customer disputes. A person handles it."""
    if not (reason or "").strip():
        raise ValueError("a reason is required")
    inv = _invoice(con, number)
    with con:
        con.execute("UPDATE invoices SET disputed=1 WHERE id=?", (inv["id"],))
        _audit(con, actor, "INVOICE_DISPUTED", number, reason.strip())


def _followup_text(stage, inv, name, balance, late):
    n, due, amount = inv["number"], inv["due_date"], php(balance)
    if stage == "REMINDER_1":
        subject = f"Friendly reminder: invoice {n}"
        body = (f"Hello {name},\n\nOur records show invoice {n} for {amount} was due on {due}. If you have already "
                "paid, thank you, and please ignore this note. Otherwise, please let us know when to expect payment, "
                "or reply if anything about the invoice needs checking.\n\nThank you.")
    elif stage == "REMINDER_2":
        subject = f"Invoice {n} is overdue"
        body = (f"Hello {name},\n\nInvoice {n} for {amount} was due on {due} and is now {late} days overdue. "
                "Please reply with the date we can expect payment, or tell us if there is a problem with the invoice "
                "so we can fix it.\n\nThank you.")
    else:
        subject = f"Follow-up on overdue invoice {n}"
        body = (f"Hello {name},\n\nWe have not yet received payment of {amount} for invoice {n}, due on {due} "
                f"({late} days ago). We would like to sort this out with you. Please contact us this week to arrange "
                "payment or to tell us about any issue.\n\nThank you.")
    return subject, body


def followup_check(subject, body):
    text = f"{subject}\n{body}".lower()
    problems = [f"banned phrase: {b!r}" for b in BANNED_COLLECTION if b in text]
    if not subject.strip() or not body.strip():
        problems.append("empty subject or body")
    return problems


def generate_followups(con, today):
    """Draft reminders for overdue invoices by stage. Over 60 days late goes to a person, not to an email."""
    now = date.fromisoformat(today)
    created, skipped = Counter(), Counter()
    rows = con.execute("SELECT i.*, c.name AS cname FROM invoices i JOIN customers c ON c.id=i.customer_id "
                       "WHERE i.status IN ('POSTED','PARTIALLY_PAID') ORDER BY i.number").fetchall()
    for inv in rows:
        late = (now - date.fromisoformat(inv["due_date"])).days
        if late <= 0:
            continue
        if inv["disputed"]:
            skipped["DISPUTED"] += 1
            continue
        balance = inv["total_c"] - paid_c(con, inv["id"])
        if late > ESCALATE_AFTER_DAYS:
            if not con.execute("SELECT 1 FROM exceptions WHERE order_id=? AND code='OVERDUE_ESCALATION' AND resolved=0",
                               (inv["order_id"],)).fetchone():
                with con:
                    con.execute("INSERT INTO exceptions(order_id,code,severity,detail,created_on) VALUES (?,?,?,?,?)",
                                (inv["order_id"], "OVERDUE_ESCALATION", "REVIEW",
                                 f"{inv['number']}: {php(balance)} is {late} days overdue; a person should contact the customer",
                                 today))
                created["ESCALATION"] += 1
            continue
        stage = next(name for lo, hi, name in STAGES if lo <= late <= hi)
        recent = con.execute("SELECT 1 FROM followups WHERE invoice_id=? AND status='SENT' AND sent_on>=?",
                             (inv["id"], (now - timedelta(days=FOLLOWUP_COOLDOWN_DAYS)).isoformat())).fetchone()
        if recent:
            skipped["COOLDOWN"] += 1
            continue
        subject, body = _followup_text(stage, inv, inv["cname"], balance, late)
        with con:
            cur = con.execute("INSERT OR IGNORE INTO followups(invoice_id,stage,dedupe_key,subject,body,status,created_on) "
                              "VALUES (?,?,?,?,?,'DRAFT',?)",
                              (inv["id"], stage, f"{inv['number']}:{stage}:{balance}", subject, body, today))
        if cur.rowcount:
            created[stage] += 1
        else:
            skipped["ALREADY_DRAFTED"] += 1
    return created, skipped


def approve_followup(con, followup_id, approver, today):
    if not (approver or "").strip():
        raise ValueError("an approver name is required")
    f = con.execute("SELECT * FROM followups WHERE id=?", (followup_id,)).fetchone()
    if f is None or f["status"] != "DRAFT":
        raise ValueError("only DRAFT follow-ups can be approved")
    problems = followup_check(f["subject"], f["body"])
    if problems:
        raise ValueError("; ".join(problems))
    with con:
        con.execute("UPDATE followups SET status='APPROVED', approved_by=? WHERE id=?", (approver.strip(), followup_id))
        _audit(con, approver.strip(), "FOLLOWUP_APPROVED", str(followup_id))


def send_followups(con, today, send_fn=None):
    """Send APPROVED follow-ups after re-checking the invoice. Nothing goes out for paid, voided or disputed invoices."""
    results = Counter()
    for f in map(dict, con.execute("SELECT * FROM followups WHERE status='APPROVED' ORDER BY id").fetchall()):
        inv = con.execute("SELECT * FROM invoices WHERE id=?", (f["invoice_id"],)).fetchone()
        balance = inv["total_c"] - paid_c(con, inv["id"])
        reason = None
        if inv["status"] not in ("POSTED", "PARTIALLY_PAID") or balance <= 0:
            reason = "NO_LONGER_OVERDUE"
        elif inv["disputed"]:
            reason = "DISPUTED"
        elif not f["dedupe_key"].endswith(f":{balance}"):
            reason = "BALANCE_CHANGED"
        elif followup_check(f["subject"], f["body"]):
            reason = "WORDING_CHECK_FAILED"
        if reason:
            with con:
                con.execute("UPDATE followups SET status='BLOCKED', block_reason=? WHERE id=?", (reason, f["id"]))
            results[f"BLOCKED_{reason}"] += 1
            continue
        if send_fn:
            cust = con.execute("SELECT email FROM customers WHERE id=?", (inv["customer_id"],)).fetchone()
            send_fn(dict(f, to=cust["email"]))
        with con:
            con.execute("UPDATE followups SET status='SENT', sent_on=? WHERE id=?", (today, f["id"]))
            _audit(con, "system", "FOLLOWUP_SENT", str(f["id"]))
        results["SENT"] += 1
    return results


def followup_summary(con):
    return dict(Counter(r["status"] for r in con.execute("SELECT status FROM followups")))
