import json
import unittest
from datetime import date, timedelta
import invoicing as v

TODAY = "2026-11-09"
CUSTOMERS = [
    {"id": "cus_ana", "name": "Ana Trading", "email": "ana@x.example", "billing_address": "12 Rizal St", "terms_days": 30},
    {"id": "cus_ben", "name": "Ben Cafe", "email": "ben@x.example", "billing_address": "", "terms_days": 15},
    {"id": "cus_bad", "name": "Bad Email Co", "email": "not-an-email", "billing_address": "1 St", "terms_days": 30},
    {"id": "cus_carla", "name": "Carla Retail", "email": "carla@x.example", "billing_address": "88 Ave",
     "terms_days": 30, "credit_limit": 20000},
    {"id": "cus_dan", "name": "Dan Logistics", "email": "dan@x.example", "billing_address": "3 Ave",
     "terms_days": 45, "vat_exempt": 1},
]


def order(oid="O1", cust="cus_ana", items=None, **kw):
    base = {"order_id": oid, "customer_id": cust, "approved_by": "Sales Lead", "approved_at": "2026-11-08",
            "items": items if items is not None else [{"sku": "A", "qty": 3, "unit_price": 1000}]}
    base.update(kw)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.con, self.acct = v.connect(), v.FakeAccounting()
        v.load_customers(self.con, CUSTOMERS)

    def submit(self, o, today=TODAY):
        v.ingest_order(self.con, o)
        return v.process_order(self.con, o["order_id"], today)

    def invoice(self, oid="O1"):
        return dict(self.con.execute("SELECT * FROM invoices WHERE order_id=?", (oid,)).fetchone())

    def codes(self, oid="O1"):
        return {r["code"] for r in self.con.execute("SELECT code FROM exceptions WHERE order_id=? AND resolved=0", (oid,))}

    def posted(self, oid="O1", cust="cus_ana", items=None, today=TODAY, **kw):
        self.submit(order(oid, cust, items, **kw), today)
        role = "controller" if self.invoice(oid)["total_c"] > v.RULES["accountant_limit_cents"] else "accountant"
        number = v.approve_invoice(self.con, self.invoice(oid)["id"], "Accountant Maria", role, today)
        v.post_approved(self.con, self.acct, today)
        return number


class Calculation(Base):
    def test_totals_with_discount_and_vat(self):
        self.submit(order(discount_pct=0.10))
        inv = self.invoice()
        self.assertEqual((inv["subtotal_c"], inv["discount_c"], inv["vat_c"], inv["total_c"]), (300000, 30000, 32400, 302400))

    def test_rounding_is_half_up_on_exact_decimals(self):
        self.submit(order(items=[{"sku": "A", "qty": 1, "unit_price": "10.005"}]))
        inv = self.invoice()
        self.assertEqual((inv["subtotal_c"], inv["vat_c"], inv["total_c"]), (1001, 120, 1121))

    def test_vat_exempt_customer_has_no_vat(self):
        self.submit(order(cust="cus_dan"))
        self.assertEqual(self.invoice()["vat_c"], 0)

    def test_shipping_is_added_and_taxed(self):
        self.submit(order(items=[{"sku": "A", "qty": 1, "unit_price": 1000}], shipping=100))
        self.assertEqual(self.invoice()["total_c"], 123200)        # (1000 + 100) x 1.12


class Validation(Base):
    def test_unapproved_order_cannot_be_invoiced(self):
        self.assertEqual(self.submit(order(approved_by="")), "EXCEPTION: NOT_APPROVED")
        self.assertIsNone(self.con.execute("SELECT 1 FROM invoices").fetchone())

    def test_customer_problems(self):
        self.assertEqual(self.submit(order("O1", "cus_zzz")), "EXCEPTION: UNKNOWN_CUSTOMER")
        self.assertEqual(self.submit(order("O2", "cus_ben")), "EXCEPTION: MISSING_CUSTOMER_INFO")
        self.assertEqual(self.submit(order("O3", "cus_bad")), "EXCEPTION: MISSING_CUSTOMER_INFO")

    def test_bad_lines_are_rejected(self):
        bad = [[], [{"sku": "A", "qty": 0, "unit_price": 5}], [{"sku": "A", "qty": 1, "unit_price": -5}],
               [{"sku": "A", "qty": "two", "unit_price": 5}], [{"sku": "A", "qty": 1, "unit_price": "NaN"}],
               [{"qty": 1, "unit_price": 5}]]
        for i, items in enumerate(bad):
            self.assertEqual(self.submit(order(f"B{i}", items=items)), "EXCEPTION: INVALID_LINE", items)

    def test_bad_amounts_and_currency(self):
        self.assertEqual(self.submit(order("O1", discount_pct=0.5)), "EXCEPTION: INVALID_AMOUNT")
        self.assertEqual(self.submit(order("O2", items=[{"sku": "A", "qty": 1, "unit_price": 0}])), "EXCEPTION: INVALID_AMOUNT")
        self.assertEqual(self.submit(order("O3", currency="USD")), "EXCEPTION: WRONG_CURRENCY")

    def test_stated_total_must_match_the_billing_rules(self):
        self.assertEqual(self.submit(order("O1", total="3000.00")), "EXCEPTION: AMOUNT_MISMATCH")
        self.assertEqual(self.submit(order("O2", total="3360.00")), "DRAFT_CREATED")

    def test_duplicate_delivery_and_missing_id(self):
        self.assertEqual(v.ingest_order(self.con, order()), "RECEIVED")
        self.assertEqual(v.ingest_order(self.con, order()), "DUPLICATE_ORDER_ID")
        self.assertEqual(v.ingest_order(self.con, {"customer_id": "x"}), "REJECTED_NO_ORDER_ID")
        v.process_order(self.con, "O1", TODAY)
        self.assertEqual(v.process_order(self.con, "O1", TODAY), "ALREADY_INVOICED")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 1)


class ExceptionHandling(Base):
    def test_possible_duplicate_needs_a_note_then_releases(self):
        self.submit(order("O1"))
        self.assertEqual(self.submit(order("O2")), "EXCEPTION: POSSIBLE_DUPLICATE")
        with self.assertRaises(ValueError):
            v.release_exception(self.con, "O2", ["POSSIBLE_DUPLICATE"], "Maria", "accountant", " ", TODAY)
        self.assertEqual(v.release_exception(self.con, "O2", ["POSSIBLE_DUPLICATE"], "Maria", "accountant",
                                             "separate PO confirmed", TODAY), "DRAFT_CREATED")

    def test_high_value_needs_the_controller(self):
        big = [{"sku": "P", "qty": 1, "unit_price": 150000}]
        self.assertEqual(self.submit(order("O1", items=big)), "EXCEPTION: HIGH_VALUE_REVIEW")
        with self.assertRaises(PermissionError):
            v.release_exception(self.con, "O1", ["HIGH_VALUE_REVIEW"], "Maria", "accountant", "checked", TODAY)
        self.assertEqual(v.release_exception(self.con, "O1", ["HIGH_VALUE_REVIEW"], "Joy", "controller",
                                             "PO verified", TODAY), "DRAFT_CREATED")

    def test_blocking_exceptions_cannot_be_released(self):
        self.submit(order(approved_by=""))
        with self.assertRaises(ValueError):
            v.release_exception(self.con, "O1", ["NOT_APPROVED"], "Maria", "accountant", "please", TODAY)

    def test_credit_limit_counts_unpaid_invoices(self):
        self.posted("O1", "cus_carla", [{"sku": "A", "qty": 1, "unit_price": 10000}])        # PHP 11,200 outstanding
        out = self.submit(order("O2", "cus_carla", [{"sku": "B", "qty": 1, "unit_price": 15000}]))
        self.assertEqual(out, "EXCEPTION: CREDIT_LIMIT")

    def test_correcting_an_order_resolves_the_exception_and_clears_releases(self):
        self.assertEqual(self.submit(order(total="1.00")), "EXCEPTION: AMOUNT_MISMATCH")
        self.assertEqual(v.correct_order(self.con, "O1", order(total="3360.00"), "Clerk", TODAY), "DRAFT_CREATED")
        self.assertEqual(self.codes(), set())

    def test_reprocessing_after_customer_data_is_fixed(self):
        self.assertEqual(self.submit(order("O1", "cus_ben")), "EXCEPTION: MISSING_CUSTOMER_INFO")
        fixed = dict(CUSTOMERS[1], billing_address="5 Perdices St")
        v.load_customers(self.con, [fixed])
        self.assertEqual(v.process_order(self.con, "O1", TODAY), "DRAFT_CREATED")


class ApprovalAndPosting(Base):
    def test_approval_controls(self):
        self.submit(order())
        i = self.invoice()["id"]
        with self.assertRaises(ValueError):
            v.approve_invoice(self.con, i, " ", "accountant", TODAY)
        with self.assertRaises(PermissionError):
            v.approve_invoice(self.con, i, "Maria", "clerk", TODAY)
        with self.assertRaises(PermissionError):
            v.approve_invoice(self.con, i, "sales lead", "accountant", TODAY)       # separation of duties
        number = v.approve_invoice(self.con, i, "Maria", "accountant", TODAY)
        with self.assertRaises(ValueError):
            v.approve_invoice(self.con, i, "Maria", "accountant", TODAY)
        self.assertEqual((number, self.invoice()["due_date"]), ("INV-2026-0001", "2026-12-09"))

    def test_large_invoice_needs_the_controller(self):
        self.submit(order(items=[{"sku": "A", "qty": 1, "unit_price": 60000}]))
        with self.assertRaises(PermissionError):
            v.approve_invoice(self.con, self.invoice()["id"], "Maria", "accountant", TODAY)
        v.approve_invoice(self.con, self.invoice()["id"], "Joy", "controller", TODAY)

    def test_numbers_are_sequential_and_audited(self):
        self.assertEqual([self.posted("O1"), self.posted("O2", "cus_dan")], ["INV-2026-0001", "INV-2026-0002"])
        who = self.con.execute("SELECT actor FROM audit_log WHERE action='INVOICE_APPROVED'").fetchone()[0]
        self.assertEqual(who, "Accountant Maria")

    def test_posting_retries_without_duplicates(self):
        self.submit(order())
        v.approve_invoice(self.con, self.invoice()["id"], "Maria", "accountant", TODAY)
        self.acct.fail_next = 1
        self.assertEqual(dict(v.post_approved(self.con, self.acct, TODAY)), {"FAILED": 1})
        self.assertEqual(dict(v.post_approved(self.con, self.acct, TODAY)), {"POSTED": 1})
        self.assertEqual(len(self.acct.invoices), 1)
        self.assertEqual(self.acct.create_invoice(v._payload(self.con, self.invoice())), "INV-2026-0001")
        self.assertEqual(len(self.acct.invoices), 1)

    def test_giving_up_creates_an_exception(self):
        self.submit(order())
        v.approve_invoice(self.con, self.invoice()["id"], "Maria", "accountant", TODAY)
        self.acct.fail_next = 99
        for _ in range(v.MAX_POST_ATTEMPTS + 2):
            v.post_approved(self.con, self.acct, TODAY)
        self.assertEqual(self.codes(), {"POSTING_FAILED"})
        self.assertEqual(self.invoice()["status"], "APPROVED")


class PaymentsAndVoids(Base):
    def test_payment_flow_and_safeguards(self):
        n = self.posted()                                              # PHP 3,360.00
        self.assertEqual(v.record_payment(self.con, self.acct, n, "1000", TODAY, "R1"), "PARTIALLY_PAID")
        self.assertEqual(v.record_payment(self.con, self.acct, n, "1000", TODAY, "R1"), "DUPLICATE_PAYMENT")
        with self.assertRaises(ValueError):
            v.record_payment(self.con, self.acct, n, "5000", TODAY, "R2")
        self.assertEqual(v.record_payment(self.con, self.acct, n, "2360.00", TODAY, "R3"), "PAID")
        self.assertEqual(self.acct.invoices[n]["paid_cents"], 336000)
        with self.assertRaises(ValueError):
            v.record_payment(self.con, self.acct, n, "1", TODAY, "R4")      # already paid

    def test_cannot_pay_a_draft(self):
        self.submit(order())
        self.con.execute("UPDATE invoices SET number='INV-X'")
        with self.assertRaises(ValueError):
            v.record_payment(self.con, self.acct, "INV-X", "10", TODAY, "R1")

    def test_void_rules_and_numbers_are_not_reused(self):
        n1 = self.posted("O1")
        v.void_invoice(self.con, self.acct, n1, "Maria", "wrong customer")
        self.assertEqual(self.acct.invoices[n1]["status"], "VOID")
        self.assertEqual(self.posted("O2", "cus_dan"), "INV-2026-0002")
        v.record_payment(self.con, self.acct, "INV-2026-0002", "1", TODAY, "R1")
        with self.assertRaises(ValueError):
            v.void_invoice(self.con, self.acct, "INV-2026-0002", "Maria", "oops")
        with self.assertRaises(ValueError):
            v.void_invoice(self.con, self.acct, n1, "Maria", "")


class Reports(Base):
    def test_aging_buckets(self):
        for i, late in enumerate([-5, 10, 45, 75, 120]):
            n = self.posted(f"O{i}", "cus_ana", [{"sku": f"A{i}", "qty": 1, "unit_price": 100}])
            due = (date.fromisoformat(TODAY) - timedelta(days=late)).isoformat()
            self.con.execute("UPDATE invoices SET due_date=? WHERE number=?", (due, n))
        row = v.aging_report(self.con, TODAY)["customers"][0]
        for bucket in ("Current", "1-30", "31-60", "61-90", "90+"):
            self.assertEqual(row[bucket], "PHP 112.00")
        self.assertEqual(len(v.aging_report(self.con, TODAY)["overdue_invoices"]), 4)

    def test_status_summary_and_exception_report(self):
        self.posted("O1")
        self.submit(order("O2", items=[{"sku": "Z", "qty": 1, "unit_price": 5}], approved_by=""))
        self.assertEqual(v.status_summary(self.con)["POSTED"]["count"], 1)
        self.assertEqual([e["code"] for e in v.exceptions_report(self.con)], ["NOT_APPROVED"])

    def test_reconciliation_finds_every_kind_of_difference(self):
        n1, n2 = self.posted("O1"), self.posted("O2", "cus_dan")
        self.assertEqual(v.reconcile(self.con, self.acct), [])
        self.acct.invoices[n1]["total_cents"] += 1
        del self.acct.invoices[n2]
        self.acct.invoices["INV-9"] = {"total_cents": 1, "paid_cents": 0, "status": "OPEN"}
        issues = {(i["number"], i["issue"]) for i in v.reconcile(self.con, self.acct)}
        self.assertEqual(issues, {(n1, "AMOUNT_DIFFERS"), (n2, "MISSING_IN_ACCOUNTING"), ("INV-9", "EXTRA_IN_ACCOUNTING")})


class FollowUps(Base):
    def overdue(self, days_late, **kw):
        n = self.posted(**kw)
        due = (date.fromisoformat(TODAY) - timedelta(days=days_late)).isoformat()
        self.con.execute("UPDATE invoices SET due_date=? WHERE number=?", (due, n))
        return n

    def stage(self):
        return [r["stage"] for r in self.con.execute("SELECT stage FROM followups ORDER BY id")]

    def test_stage_depends_on_days_overdue(self):
        for late, expected in [(3, "REMINDER_1"), (20, "REMINDER_2"), (45, "FINAL_REMINDER")]:
            self.setUp()
            self.overdue(late)
            v.generate_followups(self.con, TODAY)
            self.assertEqual(self.stage(), [expected])

    def test_nothing_for_invoices_not_yet_due_or_paid(self):
        n = self.overdue(-3)
        v.generate_followups(self.con, TODAY)
        self.assertEqual(self.stage(), [])
        self.con.execute("UPDATE invoices SET due_date='2026-10-01' WHERE number=?", (n,))
        v.record_payment(self.con, self.acct, n, "3360.00", TODAY, "R1")
        v.generate_followups(self.con, TODAY)
        self.assertEqual(self.stage(), [])

    def test_disputed_invoices_are_skipped(self):
        n = self.overdue(10)
        v.mark_disputed(self.con, n, "Maria", "wrong quantity")
        _, skipped = v.generate_followups(self.con, TODAY)
        self.assertEqual((self.stage(), skipped["DISPUTED"]), ([], 1))

    def test_running_twice_does_not_duplicate(self):
        self.overdue(10)
        v.generate_followups(self.con, TODAY)
        created, skipped = v.generate_followups(self.con, TODAY)
        self.assertEqual((sum(created.values()), skipped["ALREADY_DRAFTED"]), (0, 1))

    def test_nothing_is_sent_without_approval(self):
        self.overdue(10)
        v.generate_followups(self.con, TODAY)
        self.assertEqual(dict(v.send_followups(self.con, TODAY)), {})
        with self.assertRaises(ValueError):
            v.approve_followup(self.con, 1, " ", TODAY)

    def test_threatening_wording_cannot_be_approved(self):
        self.overdue(10)
        v.generate_followups(self.con, TODAY)
        self.con.execute("UPDATE followups SET body=body || ' We will take legal action.'")
        with self.assertRaises(ValueError):
            v.approve_followup(self.con, 1, "Maria", TODAY)

    def test_send_checks_the_invoice_again_and_applies_a_cooldown(self):
        n = self.overdue(10)
        v.generate_followups(self.con, TODAY)
        v.approve_followup(self.con, 1, "Maria", TODAY)
        outbox = []
        self.assertEqual(dict(v.send_followups(self.con, TODAY, outbox.append)), {"SENT": 1})
        self.assertEqual(outbox[0]["to"], "ana@x.example")
        later = (date.fromisoformat(TODAY) + timedelta(days=3)).isoformat()
        _, skipped = v.generate_followups(self.con, later)
        self.assertEqual(skipped["COOLDOWN"], 1)

    def test_payment_or_dispute_before_sending_blocks_it(self):
        n = self.overdue(10)
        v.generate_followups(self.con, TODAY)
        v.approve_followup(self.con, 1, "Maria", TODAY)
        v.record_payment(self.con, self.acct, n, "3360.00", TODAY, "R1")
        self.assertEqual(dict(v.send_followups(self.con, TODAY)), {"BLOCKED_NO_LONGER_OVERDUE": 1})

    def test_partial_payment_changes_the_balance_and_allows_a_fresh_draft(self):
        n = self.overdue(10)
        v.generate_followups(self.con, TODAY)
        v.approve_followup(self.con, 1, "Maria", TODAY)
        v.record_payment(self.con, self.acct, n, "1000", TODAY, "R1")
        self.assertEqual(dict(v.send_followups(self.con, TODAY)), {"BLOCKED_BALANCE_CHANGED": 1})
        created, _ = v.generate_followups(self.con, TODAY)
        self.assertEqual(created["REMINDER_2"], 1)

    def test_very_late_invoices_go_to_a_person(self):
        self.overdue(70)
        created, _ = v.generate_followups(self.con, TODAY)
        self.assertEqual((dict(created), self.stage()), ({"ESCALATION": 1}, []))
        self.assertEqual(dict(v.generate_followups(self.con, TODAY)[0]), {})          # not raised twice
        self.assertEqual(self.codes(), {"OVERDUE_ESCALATION"})


if __name__ == "__main__":
    unittest.main()
