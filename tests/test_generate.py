import sys
import unittest
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from seed.generate import generate  # noqa: E402


class GenerateTest(unittest.TestCase):
    def setUp(self):
        self.data = generate()

    def test_counts(self):
        self.assertEqual(len(self.data["borrowers"]), 200)
        self.assertEqual(len(self.data["loans"]), 250)

    def test_deterministic(self):
        self.assertEqual(self.data, generate())
        self.assertNotEqual(self.data, generate(seed=1))

    def test_refs_and_mobiles_unique(self):
        for rows, key in ((self.data["borrowers"], "ref"), (self.data["borrowers"], "mobile"), (self.data["loans"], "ref")):
            values = [r[key] for r in rows]
            self.assertEqual(len(values), len(set(values)), key)

    def test_every_borrower_has_a_loan_and_every_loan_an_owner(self):
        refs = {b["ref"] for b in self.data["borrowers"]}
        owners = {loan["borrower_ref"] for loan in self.data["loans"]}
        self.assertEqual(refs, owners)

    def test_duplicate_full_names_exist(self):
        names = Counter((b["firstname"], b["lastname"]) for b in self.data["borrowers"])
        self.assertTrue(any(n > 1 for n in names.values()))

    def test_loan_terms_in_range(self):
        for loan in self.data["loans"]:
            self.assertIsInstance(loan["principal"], int)
            self.assertTrue(20_000 <= loan["principal"] <= 300_000)
            self.assertEqual(loan["principal"] % 5_000, 0)
            self.assertIn(loan["months"], (6, 9, 12, 18, 24, 36))
            self.assertTrue(date(2026, 1, 5) <= date.fromisoformat(loan["disbursed_on"]) <= date(2026, 6, 30))

    def test_fewer_loans_than_borrowers_rejected(self):
        with self.assertRaises(ValueError):
            generate(n_borrowers=10, n_loans=5)


if __name__ == "__main__":
    unittest.main()
