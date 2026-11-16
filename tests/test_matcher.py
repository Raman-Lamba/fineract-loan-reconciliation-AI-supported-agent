import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from matcher.evaluate import evaluate  # noqa: E402
from matcher.match import decide  # noqa: E402


def loan(ref, first, last, amounts_by_due, account):
    return {"loan_ref": ref, "loan_id": 1, "account_no": account, "client_id": 1, "firstname": first,
            "lastname": last, "instalments": [{"due": d, "amount": a} for d, a in amounts_by_due]}


SCHEDULES = [
    loan("L1", "Ramesh", "Kumar", [("2026-09-05", 8450.0), ("2026-10-05", 8450.0)], "000000001"),
    loan("L2", "Lakshmi", "Sharma", [("2026-09-05", 3100.5)], "000000002"),
    loan("L3", "Anita", "Nair", [("2026-09-10", 5000.0)], "000000003"),   # same name and amount as L4
    loan("L4", "Anita", "Nair", [("2026-09-12", 5000.0)], "000000004"),
    loan("L5", "Suresh", "Patel", [("2026-09-20", 7000.0)], "000000005"),  # same initial and amount as L6
    loan("L6", "Sanjay", "Patel", [("2026-09-20", 7000.0)], "000000006"),
]


def line(narration, amount="8450.00", day="2026-09-05", txn="100000000001"):
    return {"txn_id": txn, "date": day, "amount": amount, "narration": narration}


def one(narration, **kw):
    return decide([line(narration, **kw)], SCHEDULES)[0]


class MatcherTest(unittest.TestCase):
    def assertPosts(self, decision, ref):
        self.assertEqual((decision["outcome"], decision["loan_ref"]), ("post", ref), decision["reason"])

    def assertQueues(self, decision, candidates, reason_part):
        self.assertEqual((decision["outcome"], decision["loan_ref"]), ("queue", ""))
        self.assertEqual(sorted(decision["candidates"]), candidates)
        self.assertIn(reason_part, decision["reason"])

    def test_decision_carries_the_line_and_a_reason(self):
        d = one("UPI/000001/RAMESH KUM/ramesh@okaxis/emi sep")
        self.assertEqual((d["txn_id"], d["date"], d["amount"]), ("100000000001", "2026-09-05", "8450.00"))
        self.assertEqual(d["candidates"], ["L1"])
        self.assertTrue(d["reason"])

    # Rule 1
    def test_repeated_txn_id_is_ignored(self):
        first, second = decide([line("NEFT-100000000001-RAMESH KUMAR-EMI")] * 2, SCHEDULES)
        self.assertPosts(first, "L1")
        self.assertEqual((second["outcome"], second["loan_ref"]), ("ignore", ""))

    # Rule 2
    def test_loan_reference_posts(self):
        self.assertPosts(one("NACH/ACH DR/SCPL000000002/SEP EMI/00000001", amount="3100.50"), "L2")

    def test_loan_reference_with_another_amount_queues_that_loan(self):
        d = one("NACH/ACH DR/SCPL000000002/SEP EMI/00000001", amount="3000.00")
        self.assertQueues(d, ["L2"], "no instalment of this amount")

    def test_loan_reference_outside_the_window_queues(self):
        d = one("NACH/ACH DR/SCPL000000002/SEP EMI/00000001", amount="3100.50", day="2026-09-11")
        self.assertQueues(d, ["L2"], "no instalment of this amount")

    # Rule 3: the narration formats
    def test_upi_truncated_name_posts(self):
        self.assertPosts(one("UPI/000001/RAMESH KUM/ramesh@okaxis/emi sep"), "L1")
        self.assertPosts(one("UPI/000001/LAKSHMI SH/lakshmi@ybl", amount="3100.50"), "L2")

    def test_neft_with_and_without_memo_posts(self):
        self.assertPosts(one("NEFT-100000000001-RAMESH KUMAR-LOAN EMI"), "L1")
        self.assertPosts(one("NEFT-100000000001-RAMESH KUMAR"), "L1")

    def test_imps_posts(self):
        self.assertPosts(one("IMPS/P2A/000001/RAMESH KUMAR/EMI SEP"), "L1")

    # Rule 3: the ways a name is mangled
    def test_initial_posts(self):
        self.assertPosts(one("IMPS/P2A/000001/R KUMAR"), "L1")

    def test_swapped_order_posts(self):
        self.assertPosts(one("NEFT-100000000001-KUMAR RAMESH-EMI"), "L1")
        self.assertPosts(one("UPI/000001/KUMAR RAME/kumar@oksbi/emi"), "L1")  # swapped, then cut to 10

    def test_run_together_posts(self):
        self.assertPosts(one("NEFT-100000000001-RAMESHKUMAR"), "L1")
        self.assertPosts(one("UPI/000001/RAMESHKUMA/rameshkumar@paytm"), "L1")
        self.assertPosts(one("IMPS/P2A/000001/LAKSHMISHARM", amount="3100.50"), "L2")  # cut to 12

    def test_within_five_days_posts_and_six_does_not(self):
        self.assertPosts(one("NEFT-100000000001-RAMESH KUMAR", day="2026-09-01"), "L1")
        self.assertPosts(one("NEFT-100000000001-RAMESH KUMAR", day="2026-09-10"), "L1")
        self.assertEqual(one("NEFT-100000000001-RAMESH KUMAR", day="2026-09-11")["outcome"], "queue")

    # Rule 3: never guess
    def test_same_name_same_amount_pair_queues_with_both(self):
        d = one("NEFT-100000000001-ANITA NAIR-EMI", amount="5000.00", day="2026-09-11")
        self.assertQueues(d, ["L3", "L4"], "2 loans fit")

    def test_same_initial_same_amount_pair_queues_even_with_a_full_name(self):
        for name in ("S PATEL", "SURESH PATEL", "SURESH PAT"):
            d = one(f"IMPS/P2A/000001/{name}", amount="7000.00", day="2026-09-20")
            self.assertQueues(d, ["L5", "L6"], "2 loans fit")

    def test_partial_and_overpaid_queue(self):
        for amount in ("6000.00", "9450.00", "16900.00"):
            d = one("NEFT-100000000001-RAMESH KUMAR-EMI", amount=amount)
            self.assertQueues(d, ["L1"], "amount differs")

    def test_another_first_name_queues(self):
        d = one("NEFT-100000000001-GEETA KUMAR-EMI")
        self.assertQueues(d, [], "no loan fits")

    def test_stranger_queues(self):
        self.assertQueues(one("NEFT-100000000001-R K STORES"), [], "no loan fits")
        self.assertQueues(one("NEFT-100000000001-ACME TRADERS"), [], "no loan fits")

    def test_unreadable_narration_queues(self):
        self.assertQueues(one("CHQ DEP 000123"), [], "no payer name")


class EvaluateTest(unittest.TestCase):
    def test_counts_and_rates(self):
        key = [
            {"txn_id": "1", "case": "clean", "expected_outcome": "post", "expected_loan_ref": "L1"},
            {"txn_id": "1", "case": "duplicate", "expected_outcome": "ignore", "expected_loan_ref": ""},
            {"txn_id": "2", "case": "clean", "expected_outcome": "post", "expected_loan_ref": "L2"},
            {"txn_id": "3", "case": "partial", "expected_outcome": "queue", "expected_loan_ref": ""},
            {"txn_id": "4", "case": "messy_name", "expected_outcome": "post", "expected_loan_ref": "L4"},
            {"txn_id": "", "case": "missed", "expected_outcome": "none", "expected_loan_ref": "L9"},
        ]
        decisions = [
            {"txn_id": "1", "outcome": "post", "loan_ref": "L1"},
            {"txn_id": "1", "outcome": "post", "loan_ref": "L1"},   # a duplicate, posted: wrong
            {"txn_id": "2", "outcome": "post", "loan_ref": "L7"},   # wrong loan
            {"txn_id": "3", "outcome": "queue", "loan_ref": ""},
            {"txn_id": "4", "outcome": "queue", "loan_ref": ""},
        ]
        r = evaluate(decisions, key)
        self.assertEqual((r["posts"], r["wrong_posts"], r["duplicates_posted"]), (3, 2, 1))
        self.assertEqual((r["should_post"], r["posted_right"]), (3, 1))
        self.assertEqual(r["table"]["clean"], {"post": 2, "queue": 0, "ignore": 0})
        self.assertEqual(r["table"]["duplicate"], {"post": 1, "queue": 0, "ignore": 0})
        self.assertNotIn("missed", r["table"])


if __name__ == "__main__":
    unittest.main()
