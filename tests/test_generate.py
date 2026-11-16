import hashlib
import json
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
        self.assertEqual(len(self.data["loans"]), 260)  # 250 drawn + 10 twins

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

    def test_original_loans_unchanged_by_twins(self):
        # Pinned before twins were added: L0001-L0250 are already in Fineract and must not move.
        blob = json.dumps([self.data["borrowers"], self.data["loans"][:250]], sort_keys=True).encode()
        self.assertEqual(hashlib.sha256(blob).hexdigest(),
                         "b62e254262d61faa75382a9f2c7c5d3894512b5d75fc61376b2bf972c8c199f2")
        self.assertEqual(generate(n_twins=0)["loans"], self.data["loans"][:250])

    def test_twins_copy_a_loan_of_a_lookalike_borrower(self):
        people = {b["ref"]: b for b in self.data["borrowers"]}
        originals, twins = self.data["loans"][:250], self.data["loans"][250:]
        self.assertEqual([t["ref"] for t in twins], [f"L{i:04d}" for i in range(251, 261)])
        terms = lambda loan: (loan["principal"], loan["months"], loan["disbursed_on"])  # noqa: E731
        for twin in twins:
            x = people[twin["borrower_ref"]]
            sources = [o for o in originals if terms(o) == terms(twin) and o["borrower_ref"] != x["ref"]
                       and people[o["borrower_ref"]]["lastname"] == x["lastname"]
                       and people[o["borrower_ref"]]["firstname"][0] == x["firstname"][0]]
            self.assertTrue(sources, twin["ref"])

    def test_twins_cover_same_name_and_same_initial_pairs(self):
        people = {b["ref"]: b for b in self.data["borrowers"]}
        by_ref = {loan["ref"]: loan for loan in self.data["loans"]}
        same = [people[t["borrower_ref"]]["firstname"] == people[by_ref[t["twin_of"]]["borrower_ref"]]["firstname"]
                for t in self.data["loans"][250:]]
        self.assertIn(True, same)
        self.assertIn(False, same)

    def test_twin_not_disbursed_before_its_borrower_was_activated(self):
        # Fineract activated each client on the date of their earliest original loan.
        activated = {}
        for loan in self.data["loans"][:250]:
            ref = loan["borrower_ref"]
            activated[ref] = min(activated.get(ref, loan["disbursed_on"]), loan["disbursed_on"])
        for twin in self.data["loans"][250:]:
            self.assertGreaterEqual(twin["disbursed_on"], activated[twin["borrower_ref"]], twin["ref"])

    def test_fewer_loans_than_borrowers_rejected(self):
        with self.assertRaises(ValueError):
            generate(n_borrowers=10, n_loans=5)


if __name__ == "__main__":
    unittest.main()
