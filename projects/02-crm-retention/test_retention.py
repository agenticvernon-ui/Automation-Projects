import unittest
from datetime import datetime, timedelta
import retention as r

NOW = datetime.fromisoformat("2026-11-08T04:00:00+00:00")   # 12:00 local, inside the send window


def ago(**kw):
    return (NOW - timedelta(**kw)).isoformat()


def cust(cid, **kw):
    base = {"id": cid, "email": f"{cid}@x.com", "name": f"Name {cid}", "marketing_opt_in": 1,
            "created_at": ago(days=200)}
    return {**base, **kw}


class Tests(unittest.TestCase):
    def setUp(self):
        self.con = r.connect()

    def load(self, **data):
        r.load_data(self.con, data)

    def drafts(self):
        return r.generate_drafts(self.con, NOW)

    def one_message(self):
        return self.con.execute("SELECT * FROM messages ORDER BY id").fetchone()

    def test_each_trigger_creates_the_right_draft(self):
        self.load(customers=[cust("a"), cust("b"), cust("c"), cust("d")],
                  orders=[{"order_id": "Oa", "customer_id": "a", "total": 1, "ordered_at": ago(days=3)},
                          {"order_id": "Ob", "customer_id": "b", "total": 1, "ordered_at": ago(days=20)},
                          {"order_id": "Od", "customer_id": "d", "total": 1, "ordered_at": ago(days=120)}],
                  carts=[{"cart_id": "K", "customer_id": "c", "total": 99.0, "updated_at": ago(hours=5)}])
        created, _ = self.drafts()
        self.assertEqual(dict(created), {"POSTPURCHASE": 1, "NUDGE": 1, "CART": 1, "WINBACK": 1})

    def test_no_drafts_for_ineligible_customers(self):
        self.load(customers=[cust("a", marketing_opt_in=0), cust("b", unsubscribed_at=ago(days=5)),
                             cust("c", open_complaint=1), cust("d", suppressed_reason="hard bounce")],
                  orders=[{"order_id": f"O{c}", "customer_id": c, "total": 1, "ordered_at": ago(days=3)}
                          for c in "abcd"])
        created, skipped = self.drafts()
        self.assertEqual(sum(created.values()), 0)
        self.assertEqual(set(skipped), {"NO_CONSENT", "UNSUBSCRIBED", "OPEN_COMPLAINT", "SUPPRESSED"})

    def test_running_twice_does_not_duplicate(self):
        self.load(customers=[cust("a")],
                  orders=[{"order_id": "Oa", "customer_id": "a", "total": 1, "ordered_at": ago(days=3)}])
        self.drafts()
        created, skipped = self.drafts()
        self.assertEqual(sum(created.values()), 0)
        self.assertEqual(skipped["ALREADY_DRAFTED"], 1)

    def test_no_cart_draft_if_customer_already_bought(self):
        self.load(customers=[cust("a")],
                  orders=[{"order_id": "Oa", "customer_id": "a", "total": 1, "ordered_at": ago(hours=1)}],
                  carts=[{"cart_id": "K", "customer_id": "a", "total": 5.0, "updated_at": ago(hours=4)}])
        created, skipped = self.drafts()
        self.assertEqual(created["CART"], 0)
        self.assertEqual(skipped["PURCHASED_SINCE"], 1)

    def make_cart_message(self, **customer_kw):
        self.load(customers=[cust("a", **customer_kw)],
                  carts=[{"cart_id": "K", "customer_id": "a", "total": 5.0, "updated_at": ago(hours=4)}])
        self.drafts()
        return self.one_message()["id"]

    def test_unapproved_message_is_never_sent(self):
        self.make_cart_message()
        self.assertEqual(dict(r.send_approved(self.con, NOW)), {})
        self.assertEqual(self.one_message()["status"], "DRAFT")

    def test_approval_needs_a_name_and_a_draft(self):
        mid = self.make_cart_message()
        with self.assertRaises(ValueError):
            r.approve(self.con, mid, "  ", NOW)
        r.approve(self.con, mid, "Vernon", NOW)
        with self.assertRaises(ValueError):
            r.approve(self.con, mid, "Vernon", NOW)

    def test_risky_edit_rejected_and_edit_resets_approval(self):
        mid = self.make_cart_message()
        r.approve(self.con, mid, "Vernon", NOW)
        with self.assertRaises(ValueError):
            r.edit_draft(self.con, mid, "Guaranteed savings", "Buy now")
        r.edit_draft(self.con, mid, "A calmer subject", "Your cart is saved.")
        row = self.one_message()
        self.assertEqual((row["status"], row["approved_by"]), ("DRAFT", None))
        self.assertIn("Reply STOP", row["body"])

    def test_unsubscribe_after_approval_blocks_send(self):
        mid = self.make_cart_message()
        r.approve(self.con, mid, "Vernon", NOW)
        r.unsubscribe(self.con, "a", NOW)
        self.assertEqual(dict(r.send_approved(self.con, NOW)), {"BLOCKED_UNSUBSCRIBED": 1})

    def test_purchase_after_draft_blocks_stale_message(self):
        mid = self.make_cart_message()
        r.approve(self.con, mid, "Vernon", NOW)
        self.load(orders=[{"order_id": "Onew", "customer_id": "a", "total": 5, "ordered_at": NOW.isoformat()}])
        self.assertEqual(dict(r.send_approved(self.con, NOW + timedelta(minutes=1))),
                         {"BLOCKED_NO_LONGER_RELEVANT": 1})

    def test_quiet_hours_defer_then_send(self):
        mid = self.make_cart_message()
        r.approve(self.con, mid, "Vernon", NOW)
        night = NOW + timedelta(hours=10)                     # 22:00 local
        self.assertEqual(dict(r.send_approved(self.con, night)), {"DEFERRED_QUIET_HOURS": 1})
        morning = NOW + timedelta(hours=21)                   # 09:00 local next day
        self.assertEqual(dict(r.send_approved(self.con, morning)), {"SENT": 1})

    def test_weekly_cap_defers_third_message(self):
        mid = self.make_cart_message()
        self.load(prior_messages=[
            {"customer_id": "a", "campaign": "NUDGE", "dedupe_key": f"h{i}", "subject": "s", "body": "b",
             "sent_at": ago(days=i + 1)} for i in range(2)])
        r.approve(self.con, mid, "Vernon", NOW)
        self.assertEqual(dict(r.send_approved(self.con, NOW)), {"DEFERRED_FREQUENCY_CAP": 1})

    def test_approved_message_expires_after_72_hours(self):
        mid = self.make_cart_message()
        r.approve(self.con, mid, "Vernon", NOW)
        self.assertEqual(dict(r.send_approved(self.con, NOW + timedelta(hours=73))), {"EXPIRED": 1})

    def test_send_error_keeps_message_approved(self):
        mid = self.make_cart_message()
        r.approve(self.con, mid, "Vernon", NOW)

        def boom(_):
            raise RuntimeError("provider down")
        self.assertEqual(dict(r.send_approved(self.con, NOW, boom)), {"SEND_ERROR": 1})
        self.assertEqual(self.one_message()["status"], "APPROVED")

    def test_complaint_event_suppresses_future_sends(self):
        mid = self.make_cart_message()
        r.approve(self.con, mid, "Vernon", NOW)
        r.send_approved(self.con, NOW)
        r.record_event(self.con, mid, "COMPLAINED", NOW)
        self.assertIsNotNone(r.eligibility(r._customer(self.con, "a")))

    def test_segments(self):
        self.load(customers=[cust(c) for c in "abcdef"],
                  orders=[{"order_id": "a1", "customer_id": "a", "total": 1, "ordered_at": ago(days=5)},
                          {"order_id": "b1", "customer_id": "b", "total": 1, "ordered_at": ago(days=60)},
                          {"order_id": "c1", "customer_id": "c", "total": 1, "ordered_at": ago(days=200)},
                          {"order_id": "d1", "customer_id": "d", "total": 1, "ordered_at": ago(days=50)},
                          {"order_id": "d2", "customer_id": "d", "total": 1, "ordered_at": ago(days=10)},
                          {"order_id": "e1", "customer_id": "e", "total": 1, "ordered_at": ago(days=30)},
                          {"order_id": "e2", "customer_id": "e", "total": 1, "ordered_at": ago(days=20)},
                          {"order_id": "e3", "customer_id": "e", "total": 1, "ordered_at": ago(days=5)}])
        got = {c: r.segment(self.con, c, NOW)[0] for c in "abcdef"}
        self.assertEqual(got, {"a": "NEW", "b": "AT_RISK", "c": "LAPSED", "d": "REPEAT", "e": "VIP", "f": "PROSPECT"})

    def test_report_counts_and_rates(self):
        self.load(customers=[cust("a"), cust("b")],
                  carts=[{"cart_id": "K1", "customer_id": "a", "total": 5.0, "updated_at": ago(hours=4)},
                         {"cart_id": "K2", "customer_id": "b", "total": 5.0, "updated_at": ago(hours=4)}])
        self.drafts()
        for m in self.con.execute("SELECT id FROM messages").fetchall():
            r.approve(self.con, m["id"], "Vernon", NOW)
        r.send_approved(self.con, NOW)
        first = self.con.execute("SELECT id FROM messages ORDER BY id").fetchone()["id"]
        r.record_event(self.con, first, "OPENED", NOW)
        self.load(orders=[{"order_id": "O", "customer_id": "a", "total": 5, "ordered_at": (NOW + timedelta(days=1)).isoformat()}])
        rep = r.report(self.con, NOW + timedelta(days=2))
        self.assertEqual(rep["campaigns"]["CART"]["sent"], 2)
        self.assertEqual(rep["campaigns"]["CART"]["open_rate"], 0.5)
        self.assertEqual(rep["orders_within_7d_after_a_send"], 1)


if __name__ == "__main__":
    unittest.main()
