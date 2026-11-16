import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from posting.post import channel, post_all, split  # noqa: E402
from posting.verify import check  # noqa: E402

PAYMENT_TYPES = {"UPI": 4, "NEFT": 5, "IMPS": 6, "NACH": 7, "CASH": 8}
UNIQUE = {"error": "Validation Error: Failed data validation due to: value.must.be.unique.", "status_code": 400}


class FakeFineract:
    """One loan (id 7) with two 8,450 instalments. Answers like the connector's HTTP adapter."""

    def __init__(self, paid=(), active=True, transactions=(), post_error=None):
        self.periods = [{"dueDate": [2026, 8, 5]}] + [  # the first row is the disbursal, as in Fineract
            {"period": n, "dueDate": [2026, 8 + n, 5], "totalDueForPeriod": 8450.0, "complete": n in paid}
            for n in (1, 2)]
        self.active, self.transactions, self.post_error = active, list(transactions), post_error
        self.posts = []

    def execute_get(self, endpoint, params=None):
        if "/transactions/external-id/" in endpoint:
            found = [t for t in self.transactions if endpoint.endswith("/" + t["externalId"])]
            return found[0] if found and endpoint.startswith("loans/7/") else {"error": "not found", "status_code": 404}
        if endpoint != "loans/7":
            return {"error": "Connection failed", "status_code": 500}
        # The loan's own list shows what an earlier run left behind, unless the test hides it.
        return {"status": {"active": self.active}, "repaymentSchedule": {"periods": self.periods},
                "transactions": [t for t in self.transactions if not t.get("hidden")]}

    def execute_post(self, endpoint, body):
        self.posts.append((endpoint, body))
        if self.post_error:
            return self.post_error
        return {"resourceId": 900 + len(self.posts)}


def decision(txn="100000000001", day="2026-09-05", amount="8450.00", ref="L1", outcome="post"):
    return {"txn_id": txn, "date": day, "amount": amount, "outcome": outcome, "loan_ref": ref,
            "reason": "matched by name", "candidates": ref}


def run(fake, decisions=None, ledger_ids=(), loan_ids=None):
    decisions = decisions or [decision()]
    narrations = {d["txn_id"]: "UPI/000001/RAMESH KUM/ramesh@okaxis/emi sep" for d in decisions}
    return list(post_all(decisions, narrations, loan_ids or {"L1": 7}, fake, PAYMENT_TYPES, set(ledger_ids)))


class SplitTest(unittest.TestCase):
    def test_the_example_in_the_spec(self):
        self.assertEqual(split("8450.00"), (Decimal("6760.00"), Decimal("1690.00")))

    def test_odd_paise_round_half_up_and_still_add_up(self):
        self.assertEqual(split("0.03"), (Decimal("0.02"), Decimal("0.01")))    # 0.024 rounds down
        self.assertEqual(split("0.07"), (Decimal("0.06"), Decimal("0.01")))    # 0.056 rounds up
        self.assertEqual(split("2552.23"), (Decimal("2041.78"), Decimal("510.45")))  # 2041.784
        self.assertEqual(split("1000.33"), (Decimal("800.26"), Decimal("200.07")))   # 800.264

    def test_shares_always_sum_to_the_amount(self):
        for paise in range(1, 2000):
            amount = Decimal(paise) / 100
            partner, lender = split(str(amount))
            self.assertEqual(partner + lender, amount)
            self.assertEqual(partner.as_tuple().exponent, -2)


class ChannelTest(unittest.TestCase):
    def test_each_narration_format(self):
        self.assertEqual(channel("UPI/189749/SANJAY PAT/sanjay@oksbi"), "UPI")
        self.assertEqual(channel("NEFT-611680609954-RAMESH KUMAR-EMI"), "NEFT")
        self.assertEqual(channel("IMPS/P2A/609954/RAMESH KUMAR/EMI"), "IMPS")
        self.assertEqual(channel("NACH/ACH DR/SCPL000000002/SEP EMI/00000001"), "NACH")


class PostingTest(unittest.TestCase):
    def test_posts_with_the_txn_id_as_external_id_and_splits(self):
        fake = FakeFineract()
        (audit, ledger), = run(fake)
        endpoint, body = fake.posts[0]
        self.assertEqual(endpoint, "loans/7/transactions?command=repayment")
        self.assertEqual((body["externalId"], body["receiptNumber"]), ("100000000001", "100000000001"))
        self.assertEqual((body["transactionDate"], body["transactionAmount"]), ("2026-09-05", "8450.00"))
        self.assertEqual((body["paymentTypeId"], body["note"]), (4, "matched by name"))
        self.assertEqual(ledger, {"txn_id": "100000000001", "date": "2026-09-05", "loan_ref": "L1",
                                  "fineract_txn_id": 901, "amount": "8450.00", "partner_share": "6760.00",
                                  "lender_share": "1690.00"})
        self.assertEqual(audit, {"txn_id": "100000000001", "action": "posted", "loan_ref": "L1",
                                 "reason": "matched by name", "approver": "auto (matcher rules)"})

    def test_a_txn_already_in_the_ledger_is_skipped_untouched(self):
        fake = FakeFineract()
        self.assertEqual(run(fake, ledger_ids=["100000000001"]), [])
        self.assertEqual(fake.posts, [])

    def test_queue_and_ignore_decisions_are_never_posted(self):
        fake = FakeFineract()
        self.assertEqual(run(fake, [decision(outcome="queue"), decision(outcome="ignore")]), [])
        self.assertEqual(fake.posts, [])

    def test_an_instalment_already_paid_in_fineract_is_queued_not_posted(self):
        fake = FakeFineract(paid=[1])
        (audit, ledger), = run(fake)
        self.assertEqual((audit["action"], ledger, fake.posts), ("queued", None, []))
        self.assertIn("instalment already paid", audit["reason"])
        self.assertEqual(audit["approver"], "")

    def test_the_live_check_looks_at_the_matched_instalment_only(self):
        fake = FakeFineract(paid=[1])
        (audit, _), = run(fake, [decision(day="2026-10-07")])  # the second instalment, two days late
        self.assertEqual(audit["action"], "posted")

    def test_a_closed_loan_is_queued_not_posted(self):
        fake = FakeFineract(active=False)
        (audit, ledger), = run(fake)
        self.assertEqual((audit["action"], ledger, fake.posts), ("queued", None, []))

    def test_external_id_already_exists_is_recovered_without_a_second_post(self):
        earlier = {"id": 519, "externalId": "100000000001", "amount": 8450.0, "manuallyReversed": False, "hidden": True}
        fake = FakeFineract(transactions=[earlier], post_error=UNIQUE)
        (audit, ledger), = run(fake)
        self.assertEqual(len(fake.posts), 1)
        self.assertEqual((audit["action"], audit["approver"]), ("recovered", "auto (matcher rules)"))
        self.assertEqual((ledger["fineract_txn_id"], ledger["partner_share"], ledger["lender_share"]),
                         (519, "6760.00", "1690.00"))

    def test_its_own_earlier_payment_is_recovered_not_read_as_already_paid(self):
        # The crash this guards against: posted, then died before the ledger write. On the next run the
        # instalment is paid, by this very line.
        earlier = {"id": 519, "externalId": "100000000001", "amount": 8450.0, "manuallyReversed": False}
        fake = FakeFineract(paid=[1], transactions=[earlier])
        (audit, ledger), = run(fake)
        self.assertEqual((audit["action"], ledger["fineract_txn_id"], fake.posts), ("recovered", 519, []))

    def test_a_reversed_earlier_payment_is_not_put_in_the_ledger(self):
        earlier = {"id": 519, "externalId": "100000000001", "amount": 8450.0, "manuallyReversed": True}
        (audit, ledger), = run(FakeFineract(transactions=[earlier]))
        self.assertEqual((audit["action"], ledger), ("failed", None))

    def test_external_id_used_on_another_loan_fails(self):
        (audit, ledger), = run(FakeFineract(post_error=UNIQUE))
        self.assertEqual((audit["action"], ledger), ("failed", None))

    def test_a_failed_post_is_logged_and_the_run_carries_on(self):
        fake = FakeFineract()
        results = run(fake, [decision(txn="1", ref="L9"), decision(txn="2")], loan_ids={"L9": 99, "L1": 7})
        self.assertEqual([(a["txn_id"], a["action"]) for a, _ in results], [("1", "failed"), ("2", "posted")])
        self.assertIn("Connection failed", results[0][0]["reason"])
        self.assertIsNone(results[0][1])

    def test_a_rejected_post_is_logged_with_fineracts_reason(self):
        fake = FakeFineract(post_error={"error": "Validation Error: date is in the future", "status_code": 400})
        (audit, ledger), = run(fake)
        self.assertEqual((audit["action"], audit["reason"], ledger),
                         ("failed", "Validation Error: date is in the future", None))


class VerifyTest(unittest.TestCase):
    ROW = {"txn_id": "1", "date": "2026-09-05", "loan_ref": "L1", "fineract_txn_id": "901", "amount": "8450.00",
           "partner_share": "6760.00", "lender_share": "1690.00"}

    def test_a_clean_ledger_has_no_problems(self):
        self.assertEqual(check([self.ROW], {"1": 8450.0}), [])

    def test_finds_each_kind_of_problem(self):
        self.assertIn("not in Fineract", check([self.ROW], {})[0])
        self.assertIn("amount", check([self.ROW], {"1": 8400.0})[0])
        self.assertIn("twice", check([self.ROW, self.ROW], {"1": 8450.0})[0])
        self.assertIn("add up", check([{**self.ROW, "lender_share": "1690.01"}], {"1": 8450.0})[0])


if __name__ == "__main__":
    unittest.main()
