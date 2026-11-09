import unittest
import pos_crm as p


def txn(tid, email=None, phone=None, name="Test", total=100.0):
    return {"txn_id": tid, "customer": {"name": name, "email": email, "phone": phone},
            "lines": [{"sku": "X", "qty": 1, "price": total}], "total": total}


class Tests(unittest.TestCase):
    def setUp(self):
        self.con, self.crm = p.connect(), p.FakeCRM()

    def run_txn(self, t):
        p.receive(self.con, t)
        return p.process_pending(self.con, self.crm)

    def outcome(self, tid):
        return self.con.execute("SELECT outcome FROM txn_map WHERE txn_id=?", (tid,)).fetchone()[0]

    def test_new_customer_created_and_purchase_recorded(self):
        self.run_txn(txn("T1", "a@x.com", "0917 111 2222"))
        self.assertEqual(self.outcome("T1"), "CREATED")
        self.assertIn("T1", self.crm.purchases)

    def test_email_case_and_spaces_do_not_create_duplicates(self):
        self.run_txn(txn("T1", "a@x.com"))
        self.run_txn(txn("T2", " A@X.com "))
        self.assertEqual(len(self.crm.customers), 1)
        self.assertEqual(self.outcome("T2"), "MATCHED_EMAIL")

    def test_phone_formats_match_same_customer(self):
        self.run_txn(txn("T1", phone="0917 111 2222"))
        for i, ph in enumerate(["+63 917 111 2222", "639171112222", "917-111-2222"]):
            self.run_txn(txn(f"M{i}", phone=ph))
        self.assertEqual(len(self.crm.customers), 1)

    def test_conflicting_matches_are_quarantined_not_merged(self):
        self.crm.create({"name": "A", "email": "a@x.com", "phone": None})
        self.crm.create({"name": "B", "email": "b@x.com", "phone": "+639171112222"})
        self.run_txn(txn("T1", "a@x.com", "0917 111 2222"))
        self.assertEqual(p.summary(self.con)["open_quarantine"][0]["code"], "MATCH_CONFLICT")
        self.assertEqual(len(self.crm.customers), 2)

    def test_invalid_email_and_total_mismatch_quarantined(self):
        self.run_txn(txn("T1", "not-an-email"))
        bad = txn("T2", "b@x.com")
        bad["total"] = 999.0
        self.run_txn(bad)
        codes = [q["code"] for q in p.summary(self.con)["open_quarantine"]]
        self.assertEqual(codes, ["INCONSISTENT_DATA", "INCONSISTENT_DATA"])

    def test_anonymous_sale_synced_without_customer(self):
        t = txn("T1")
        t["customer"] = {}
        self.run_txn(t)
        self.assertEqual(self.outcome("T1"), "ANONYMOUS")
        self.assertEqual(len(self.crm.customers), 0)

    def test_contact_without_name_quarantined(self):
        self.run_txn(txn("T1", phone="0917 111 2222", name=None))
        self.assertEqual(p.summary(self.con)["open_quarantine"][0]["code"], "INSUFFICIENT_DATA")

    def test_duplicate_webhook_ignored(self):
        self.assertEqual(p.receive(self.con, txn("T1", "a@x.com")), "RECEIVED")
        self.assertEqual(p.receive(self.con, txn("T1", "a@x.com")), "DUPLICATE_WEBHOOK")

    def test_payload_without_id_rejected(self):
        self.assertEqual(p.receive(self.con, {"total": 5}), "REJECTED")

    def test_outage_mid_sync_recovers_without_duplicate_profile(self):
        p.receive(self.con, txn("T1", "a@x.com"))
        self.crm.fail_on["add_purchase"] = 1          # profile is created, then the purchase call fails
        self.assertEqual(dict(p.process_pending(self.con, self.crm)), {"FAILED": 1})
        self.assertEqual(len(self.crm.customers), 1)
        self.assertEqual(dict(p.process_pending(self.con, self.crm)), {"DONE": 1})
        self.assertEqual(len(self.crm.customers), 1)   # no duplicate created on retry
        self.assertEqual(len(self.crm.purchases), 1)   # purchase recorded exactly once

    def test_gives_up_after_max_attempts(self):
        p.receive(self.con, txn("T1", "a@x.com"))
        self.crm.fail_on["find_by_email"] = 99
        for _ in range(p.MAX_ATTEMPTS + 2):
            p.process_pending(self.con, self.crm)
        row = self.con.execute("SELECT attempts, status FROM inbox").fetchone()
        self.assertEqual((row["attempts"], row["status"]), (p.MAX_ATTEMPTS, "FAILED"))

    def test_resolve_quarantine_replays(self):
        self.run_txn(txn("T1", "bad-email"))
        self.assertEqual(p.resolve_quarantine(self.con, self.crm, "T1", txn("T1", "ok@x.com")), "DONE")
        self.assertEqual(p.summary(self.con)["open_quarantine"], [])


if __name__ == "__main__":
    unittest.main()
