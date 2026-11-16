import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from statement.generate import OUTCOME, generate  # noqa: E402


def loan(ref, first, last, amounts_by_due, account="000000001"):
    return {"loan_ref": ref, "loan_id": 1, "account_no": account, "client_id": 1, "firstname": first,
            "lastname": last, "instalments": [{"due": d, "amount": a} for d, a in amounts_by_due]}


MONTHS = [f"2026-{m:02d}-15" for m in range(3, 11)]
SCHEDULES = [loan(f"L{i:04d}", f"First{i}", f"Last{i % 7}", [(d, 1000.0 + i) for d in MONTHS], f"{i:09d}")
             for i in range(1, 121)]


class StatementTest(unittest.TestCase):
    def setUp(self):
        self.lines, self.key = generate(SCHEDULES)

    def test_deterministic(self):
        self.assertEqual((self.lines, self.key), generate(SCHEDULES))

    def test_every_line_has_a_key_row_and_vice_versa(self):
        line_ids = Counter(r["txn_id"] for r in self.lines)
        key_ids = Counter(r["txn_id"] for r in self.key if r["case"] != "missed")
        self.assertEqual(line_ids, key_ids)

    def test_txn_ids_repeat_only_for_duplicates(self):
        repeated = {t for t, n in Counter(r["txn_id"] for r in self.lines).items() if n > 1}
        duplicates = {r["txn_id"] for r in self.key if r["case"] == "duplicate"}
        self.assertEqual(repeated, duplicates)
        self.assertTrue(duplicates)

    def test_outcomes(self):
        for row in self.key:
            self.assertEqual(row["expected_outcome"], OUTCOME[row["case"]])
            self.assertEqual(bool(row["expected_loan_ref"]), row["expected_outcome"] in ("post", "none"))

    def test_partial_and_overpaid_go_to_a_person(self):
        cases = {r["case"] for r in self.key}
        self.assertTrue({"partial", "overpaid", "unrelated", "missed"} <= cases)
        for row in self.key:
            if row["case"] in ("partial", "overpaid"):
                self.assertEqual(row["expected_outcome"], "queue")

    def test_nothing_after_cutoff(self):
        self.assertTrue(all(r["date"] <= "2026-11-04" for r in self.lines))

    def test_same_name_and_amount_is_queued(self):
        twins = [loan("L0001", "Ramesh", "Kumar", [("2026-09-05", 8450.0)]),
                 loan("L0002", "Ramesh", "Kumar", [("2026-09-05", 8450.0)], "000000002")]
        for seed in range(20):
            _, key = generate(twins, seed=seed)
            for row in key:
                if row["case"] in ("clean", "messy_name", "early_late"):
                    self.fail(f"seed {seed}: {row['case']} line for a same-name same-amount pair was not queued")


if __name__ == "__main__":
    unittest.main()
