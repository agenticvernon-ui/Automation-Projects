import unittest
from datetime import date, timedelta
import inventory as inv

INGREDIENTS = [
    {"id": "a", "name": "Alpha", "unit": "kg", "supplier": "S1", "pack_size": 10, "lead_time_days": 1,
     "unit_cost": 200, "min_threshold": 5},
    {"id": "b", "name": "Beta", "unit": "kg", "supplier": "S2", "pack_size": 1, "lead_time_days": 2,
     "unit_cost": 10, "min_threshold": 1},
    {"id": "c", "name": "Gamma", "unit": "kg", "supplier": "S3", "pack_size": 0.1, "lead_time_days": 1,
     "unit_cost": 5, "min_threshold": 1},
]
RECIPES = {"Dish": {"a": 1.0}, "Mix": {"a": 0.5, "b": 0.2}, "Cdish": {"c": 1.0}}


def day(n):
    return (date(2026, 11, 1) + timedelta(days=n)).isoformat()


def sales(first, last, dish="Dish", qty=10):
    return [{"sale_id": f"{dish}-{n}", "day": day(n), "dish": dish, "qty": qty} for n in range(first, last + 1)]


class Tests(unittest.TestCase):
    def setUp(self):
        self.con = inv.connect()
        inv.load_master(self.con, INGREDIENTS, RECIPES)
        for ing in ("b", "c"):                      # well stocked, so they stay out of the way unless a test uses them
            inv.record_count(self.con, ing, day(-1), 1000)

    # ---- recording ----
    def test_sales_become_usage_and_reimport_is_harmless(self):
        inv.record_count(self.con, "a", day(0), 100)
        self.assertEqual(inv.post_sales(self.con, sales(1, 1, qty=4))["posted"], 1)
        self.assertEqual(inv.stock_at(self.con, "a", day(1)), 96)
        self.assertEqual(inv.post_sales(self.con, sales(1, 1, qty=4))["duplicate"], 1)
        self.assertEqual(inv.stock_at(self.con, "a", day(1)), 96)

    def test_unknown_dish_and_bad_quantity_are_not_posted(self):
        res = inv.post_sales(self.con, [{"sale_id": "x", "day": day(1), "dish": "Mystery", "qty": 2},
                                        {"sale_id": "y", "day": day(1), "dish": "Dish", "qty": 0}])
        self.assertEqual((res["unknown_dish"], res["invalid"], res["posted"]), (["x"], ["y"], 0))

    def test_stock_is_last_count_plus_later_movements(self):
        inv.record_count(self.con, "a", day(0), 100)
        inv.post_sales(self.con, sales(2, 2, qty=10))
        inv.record_delivery(self.con, "a", day(3), 20, "D1")
        inv.record_waste(self.con, "a", day(3), 2, "spoiled", "W1")
        self.assertEqual(inv.stock_at(self.con, "a", day(2)), 90)
        self.assertEqual(inv.stock_at(self.con, "a", day(3)), 108)

    # ---- counts and variances ----
    def count_scenario(self, delta):
        con = inv.connect()                            # fresh database per scenario
        inv.load_master(con, INGREDIENTS, RECIPES)
        inv.record_count(con, "a", day(0), 200)
        inv.post_sales(con, sales(1, 5))               # expected stock at day 5 is 150
        return inv.record_count(con, "a", day(5), 150 + delta)

    def test_count_flags_shortage_surplus_and_ok(self):
        self.assertEqual(self.count_scenario(-10)["flag"], "SHORTAGE")
        self.assertEqual(self.count_scenario(+10)["flag"], "SURPLUS")
        self.assertEqual(self.count_scenario(-2)["flag"], "OK")   # tolerance is 5% of the 50 used = 2.5

    def test_first_count_is_opening_and_negative_count_rejected(self):
        self.assertEqual(inv.record_count(self.con, "a", day(0), 50)["flag"], "OPENING")
        with self.assertRaises(ValueError):
            inv.record_count(self.con, "a", day(1), -1)

    # ---- reorder logic ----
    def steady(self, start_count):
        inv.record_count(self.con, "a", day(0), start_count)
        inv.post_sales(self.con, sales(1, 7))
        return day(7)

    def test_reorder_only_below_reorder_point(self):
        today = self.steady(200)                       # stock 130, reorder point 20
        self.assertEqual(inv.generate_reorders(self.con, today)[0], [])
        self.con.execute("UPDATE ledger SET qty=85 WHERE kind='COUNT'")   # stock 15
        ids, _ = inv.generate_reorders(self.con, today)
        pr = inv.purchase_request(self.con, ids[0])
        self.assertEqual((pr["status"], pr["lines"][0]["packs"], pr["est_total"]), ("DRAFT", 7, 14000.0))

    def test_no_duplicate_request_while_one_is_open(self):
        today = self.steady(85)
        self.assertEqual(len(inv.generate_reorders(self.con, today)[0]), 1)
        ids, skipped = inv.generate_reorders(self.con, today)
        self.assertEqual((ids, skipped), ([], [("a", "ALREADY_ON_ORDER")]))
        inv.reject_pr(self.con, 1, "Manager", "recount first", today)
        self.assertEqual(len(inv.generate_reorders(self.con, today)[0]), 1)

    def test_little_history_falls_back_to_minimum_threshold(self):
        inv.record_count(self.con, "a", day(0), 30)
        inv.post_sales(self.con, sales(6, 7))          # only 2 trading days
        self.assertEqual(inv.generate_reorders(self.con, day(7))[0], [])          # stock 10 > minimum 5
        inv.record_count(self.con, "a", day(7), 3)
        ids, _ = inv.generate_reorders(self.con, day(7))
        line = inv.purchase_request(self.con, ids[0])["lines"][0]
        self.assertIn("LOW_DATA", line["flags"])
        self.assertEqual(line["packs"], 1)             # target = 2 x minimum = 10, need 7, one 10 kg pack

    def test_usage_spike_is_flagged_on_the_draft(self):
        inv.record_count(self.con, "a", day(0), 250)
        inv.post_sales(self.con, sales(1, 7, qty=10))
        inv.post_sales(self.con, sales(8, 14, qty=20))
        ids, _ = inv.generate_reorders(self.con, day(14))
        self.assertIn("USAGE_SPIKE", inv.purchase_request(self.con, ids[0])["lines"][0]["flags"])

    def test_packs_round_up_and_huge_orders_are_capped(self):
        inv.record_count(self.con, "c", day(0), 71)
        inv.post_sales(self.con, sales(1, 7, dish="Cdish"))        # stock 1, would need 790 packs
        ids, _ = inv.generate_reorders(self.con, day(7))
        lines = [l for i in ids for l in inv.purchase_request(self.con, i)["lines"] if l["ingredient_id"] == "c"]
        line = lines[0]
        self.assertEqual(line["packs"], inv.MAX_PACKS)
        self.assertIn("CAPPED", line["flags"])

    def test_critical_when_cover_is_shorter_than_lead_time(self):
        today = self.steady(85)                        # stock 15, 1.5 days of cover, lead time 1 day
        self.assertEqual(inv.alerts(self.con, today)[0]["status"], "LOW")
        self.con.execute("UPDATE ledger SET qty=78 WHERE kind='COUNT'")   # stock 8 -> under 1 day of cover
        self.assertEqual(inv.alerts(self.con, today)[0]["status"], "CRITICAL")

    def test_negative_stock_is_flagged_and_sized_from_zero(self):
        today = self.steady(60)                        # records say 60 - 70 = -10
        self.assertTrue(inv.alerts(self.con, today)[0]["negative_stock"])
        ids, _ = inv.generate_reorders(self.con, today)
        line = inv.purchase_request(self.con, ids[0])["lines"][0]
        self.assertIn("NEGATIVE_STOCK", line["flags"])
        self.assertEqual(line["packs"], 8)             # target 80 from zero stock

    # ---- approval and receiving ----
    def draft(self):
        today = self.steady(85)
        return inv.generate_reorders(self.con, today)[0][0], today

    def test_approval_rules(self):
        pr, today = self.draft()                       # PHP 14,000: above the manager limit
        with self.assertRaises(ValueError):
            inv.approve_pr(self.con, pr, " ", "manager", today)
        with self.assertRaises(PermissionError):
            inv.approve_pr(self.con, pr, "Sam", "cashier", today)
        with self.assertRaises(PermissionError):
            inv.approve_pr(self.con, pr, "Sam", "manager", today)
        inv.approve_pr(self.con, pr, "Owner", "owner", today)
        with self.assertRaises(ValueError):
            inv.approve_pr(self.con, pr, "Owner", "owner", today)

    def test_manager_can_approve_after_reducing_the_request(self):
        pr, today = self.draft()
        inv.adjust_line(self.con, pr, "a", 3)          # PHP 6,000
        self.assertEqual(inv.purchase_request(self.con, pr)["est_total"], 6000.0)
        inv.approve_pr(self.con, pr, "Sam", "manager", today)
        with self.assertRaises(ValueError):
            inv.adjust_line(self.con, pr, "a", 1)      # approved requests cannot be edited

    def test_removing_every_line_rejects_the_request(self):
        pr, _ = self.draft()
        inv.adjust_line(self.con, pr, "a", 0)
        self.assertEqual(inv.purchase_request(self.con, pr)["status"], "REJECTED")

    def test_receiving_books_stock_and_flags_short_delivery(self):
        pr, today = self.draft()
        with self.assertRaises(ValueError):
            inv.receive_delivery(self.con, pr, {"a": 70}, day(8))      # not ordered yet
        inv.approve_pr(self.con, pr, "Owner", "owner", today)
        inv.mark_ordered(self.con, pr, today)
        before = inv.stock_at(self.con, "a", day(8))
        with self.assertRaises(ValueError):
            inv.receive_delivery(self.con, pr, {"zzz": 1}, day(8))
        flags = inv.receive_delivery(self.con, pr, {"a": 50}, day(8))
        self.assertEqual(flags[0]["flag"], "SHORT")
        self.assertEqual(inv.stock_at(self.con, "a", day(8)), before + 50)
        with self.assertRaises(ValueError):
            inv.receive_delivery(self.con, pr, {"a": 50}, day(8))      # cannot be booked twice

    def test_on_order_quantity_shows_in_stock_report(self):
        pr, today = self.draft()
        inv.approve_pr(self.con, pr, "Owner", "owner", today)
        row = [r for r in inv.stock_report(self.con, today) if r["id"] == "a"][0]
        self.assertEqual(row["on_order"], 70)

    # ---- reports ----
    def test_usage_report_and_csv_export(self):
        inv.record_count(self.con, "a", day(0), 200)
        inv.post_sales(self.con, sales(1, 2, qty=10))
        inv.record_waste(self.con, "a", day(2), 1.5, "dropped", "W1")
        row = [r for r in inv.usage_report(self.con, day(1), day(2)) if r["id"] == "a"][0]
        self.assertEqual((row["theoretical_usage"], row["usage_cost"], row["waste_recorded"]), (20.0, 4000.0, 1.5))
        text = inv.to_csv([row], ["name", "theoretical_usage"])
        self.assertEqual(text.splitlines(), ["name,theoretical_usage", "Alpha,20.0"])


if __name__ == "__main__":
    unittest.main()
