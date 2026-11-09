import unittest
import fulfillment as f

CUST = {"name": "Ana", "email": "ana@example.com", "ship_address": "12 Rizal St"}


def order(oid="O1", sku="MUG", qty=1, customer=None):
    return {"order_id": oid, "customer": customer or CUST, "items": [{"sku": sku, "qty": qty}]}


class Tests(unittest.TestCase):
    def setUp(self):
        self.con = f.connect()
        f.load_inventory(self.con, {"MUG": 3})

    def test_happy_path_reserves_stock(self):
        self.assertEqual(f.process_order(self.con, order(qty=2)), "READY_TO_FULFILL")
        self.assertEqual(self.con.execute("SELECT reserved FROM inventory").fetchone()[0], 2)

    def test_missing_email_goes_to_exception(self):
        bad = order(customer={**CUST, "email": ""})
        self.assertEqual(f.process_order(self.con, bad), "EXCEPTION")
        self.assertEqual(self.con.execute("SELECT code FROM exceptions").fetchone()[0], "INVALID_ORDER")

    def test_same_order_id_is_idempotent(self):
        f.process_order(self.con, order())
        self.assertEqual(f.process_order(self.con, order()), "IGNORED_DUPLICATE")
        self.assertEqual(self.con.execute("SELECT reserved FROM inventory").fetchone()[0], 1)

    def test_suspected_duplicate_basket(self):
        f.process_order(self.con, order("O1"))
        self.assertEqual(f.process_order(self.con, order("O2")), "EXCEPTION")
        code = self.con.execute("SELECT code FROM exceptions").fetchone()[0]
        self.assertEqual(code, "DUPLICATE_SUSPECTED")

    def test_shortfall_reserves_nothing(self):
        self.assertEqual(f.process_order(self.con, order(qty=9)), "EXCEPTION")
        self.assertEqual(self.con.execute("SELECT reserved FROM inventory").fetchone()[0], 0)

    def test_ship_and_illegal_transition(self):
        f.process_order(self.con, order())
        f.ship(self.con, "O1")
        self.assertEqual(self.con.execute("SELECT on_hand FROM inventory").fetchone()[0], 2)
        with self.assertRaises(ValueError):
            f.ship(self.con, "O1")

    def test_discrepancy_detected(self):
        f.process_order(self.con, order())
        self.assertEqual(f.inventory_discrepancies(self.con), [])
        self.con.execute("UPDATE inventory SET reserved=5")
        self.assertEqual(len(f.inventory_discrepancies(self.con)), 1)


if __name__ == "__main__":
    unittest.main()
