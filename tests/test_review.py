import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from posting.post import handled, post_all  # noqa: E402
from review.apply import apply  # noqa: E402
from review.build import FIELDS, build  # noqa: E402
from review.report import by_loan_month, totals  # noqa: E402
from review.simulate import APPROVER as DEMO, simulate  # noqa: E402
from statement.generate import generate  # noqa: E402
from test_posting import PAYMENT_TYPES, FakeFineract  # noqa: E402
from test_statement import SCHEDULES as STATEMENT_SCHEDULES  # noqa: E402

SCHEDULES = [{"loan_ref": "L1", "loan_id": 7, "account_no": "000000001", "client_id": 1, "firstname": "Ramesh",
              "lastname": "Kumar", "instalments": [{"due": "2026-09-05", "amount": 8450.0},
                                                   {"due": "2026-10-05", "amount": 8450.0}]},
             {"loan_ref": "L2", "loan_id": 8, "account_no": "000000002", "client_id": 2, "firstname": "Rajesh",
              "lastname": "Kumar", "instalments": [{"due": "2026-09-06", "amount": 8450.0}]}]


def line(txn, amount="8450.00", day="2026-09-05"):
    return {"txn_id": txn, "date": day, "amount": amount, "narration": f"UPI/{txn}/R KUMAR/r@okaxis/emi"}


def decision(txn, outcome="queue", ref="", candidates="L1;L2", amount="8450.00"):
    return {"txn_id": txn, "date": "2026-09-05", "amount": amount, "outcome": outcome, "loan_ref": ref,
            "reason": "2 loans fit 'R KUMAR' and the amount", "candidates": candidates}


def queued(txn, decision="", loan_ref="", approver="", note="", amount="8450.00"):
    return {"txn_id": txn, "date": "2026-09-05", "amount": amount, "narration": line(txn)["narration"],
            "reason": "2 loans fit", "candidates": "", "decision": decision, "loan_ref": loan_ref,
            "approver": approver, "note": note}


class BuildTest(unittest.TestCase):
    def build(self, old=()):
        decisions = [decision("1"), decision("2"), decision("3"), decision("4", "post", "L1", "L1"),
                     decision("5", "post", "L1", "L1"), decision("1", "ignore", candidates="")]
        audit = [{"txn_id": "3", "action": "rejected", "loan_ref": "", "reason": "manual: no", "approver": "Asha"},
                 {"txn_id": "4", "action": "queued", "loan_ref": "L1", "reason": "instalment already paid (due 2026-09-05)",
                  "approver": ""},
                 {"txn_id": "5", "action": "posted", "loan_ref": "L1", "reason": "fit", "approver": "auto"}]
        return build(decisions, audit, {"2", "5"}, [line(str(n)) for n in range(1, 6)], SCHEDULES, list(old))

    def test_holds_the_matchers_queue_and_postings_queued_lines(self):
        rows = self.build()
        self.assertEqual([r["txn_id"] for r in rows], ["1", "4"])
        self.assertEqual(list(rows[0]), FIELDS)
        self.assertEqual((rows[0]["date"], rows[0]["amount"], rows[0]["narration"]),
                         ("2026-09-05", "8450.00", line("1")["narration"]))
        self.assertEqual(rows[0]["reason"], "2 loans fit 'R KUMAR' and the amount")
        self.assertIn("already paid", rows[1]["reason"])

    def test_candidates_carry_the_borrower_and_the_instalment_due(self):
        rows = self.build()
        self.assertEqual(rows[0]["candidates"],
                         "L1 Ramesh Kumar: 8450.00 due 2026-09-05; L2 Rajesh Kumar: 8450.00 due 2026-09-06")
        self.assertEqual(rows[1]["candidates"], "L1 Ramesh Kumar: 8450.00 due 2026-09-05")

    def test_a_rebuild_keeps_a_persons_edits(self):
        mine = queued("1", "post", "L2", "Asha", "called the borrower")
        row = self.build(old=[mine, queued("2", "post", "L1", "Asha")])[0]
        self.assertEqual((row["decision"], row["loan_ref"], row["approver"], row["note"]),
                         ("post", "L2", "Asha", "called the borrower"))

    def test_ledgered_and_rejected_lines_are_dropped(self):
        ids = {r["txn_id"] for r in self.build(old=[queued("2"), queued("3")])}
        self.assertFalse(ids & {"2", "3", "5"})


class NorefQueueTest(unittest.TestCase):
    def test_the_queue_shows_the_line_and_the_excel_prefix_round_trips(self):
        import tempfile
        from review import build as queue_file
        noref = "NOREF-2026-09-05-0a1b2c3d"
        rows = build([decision(noref, candidates="")], [], set(), [line(noref)], SCHEDULES, [])
        self.assertEqual(rows[0]["txn_id"], noref)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(queue_file, "QUEUE", Path(tmp) / "queue.csv"):
                queue_file.write_queue(rows)
                self.assertEqual(queue_file.read_queue()[0]["txn_id"], noref)


class ApplyTest(unittest.TestCase):
    def apply(self, rows, fake=None, ledger_ids=(), rejected_ids=()):
        self.fake = fake or FakeFineract()
        lines = {r["txn_id"]: line(r["txn_id"], r["amount"]) for r in rows if r["txn_id"] != "5.07E+11"}
        return list(apply(rows, lines, {"L1": 7}, self.fake, PAYMENT_TYPES, set(ledger_ids), set(rejected_ids)))

    def assertKept(self, row, problem):
        (keep, audit, ledger), = self.apply([row])
        self.assertEqual((keep["txn_id"], audit, ledger, self.fake.posts), (row["txn_id"], None, None, []))
        self.assertIn(problem, keep["note"])
        self.assertIn(row["note"], keep["note"])  # the person's own note is not lost

    def test_invalid_rows_stay_in_the_queue_with_the_problem_in_note(self):
        self.assertKept(queued("1", "post", "L1", "", "his brother paid"), "approver")
        self.assertKept(queued("1", "reject", "", " "), "approver")
        self.assertKept(queued("1", "post", "L9", "Asha"), "L9")
        self.assertKept(queued("1", "post", "", "Asha"), "loan_ref")
        self.assertKept(queued("1", "maybe", "L1", "Asha"), "maybe")
        self.assertKept(queued("5.07E+11", "post", "L1", "Asha"), "txn_id")

    def test_a_problem_is_written_once_however_often_apply_runs(self):
        (keep, _, _), = self.apply([queued("1", "post", "L9", "Asha", "his brother paid")])
        (again, _, _), = self.apply([keep])
        self.assertEqual(again["note"], keep["note"])

    def test_a_row_without_a_decision_is_left_alone(self):
        row = queued("1", note="waiting for the branch")
        self.assertEqual(self.apply([row]), [(row, None, None)])

    def test_post_goes_through_posting_with_the_person_as_approver(self):
        # 5,000 is no instalment of this loan: a partial amount is posted as it is, a person approved it.
        (keep, audit, ledger), = self.apply([queued("1", " Post ", " l1 ", "Asha", "his brother paid", "5000.00")])
        endpoint, body = self.fake.posts[0]
        self.assertEqual(endpoint, "loans/7/transactions?command=repayment")
        self.assertEqual((body["externalId"], body["transactionAmount"], body["transactionDate"], body["paymentTypeId"]),
                         ("1", "5000.00", "2026-09-05", 4))
        self.assertEqual(body["note"], "manual: his brother paid")
        self.assertIsNone(keep)
        self.assertEqual(audit, {"txn_id": "1", "action": "posted", "loan_ref": "L1",
                                 "reason": "manual: his brother paid", "approver": "Asha"})
        self.assertEqual((ledger["loan_ref"], ledger["amount"], ledger["partner_share"], ledger["lender_share"]),
                         ("L1", "5000.00", "4000.00", "1000.00"))

    def test_a_line_with_no_bank_reference_posts_once_under_its_synthetic_id(self):
        noref = "NOREF-2026-09-05-0a1b2c3d"
        row = queued(noref, "post", "L1", "Asha", "cheque 4411 from Ramesh")
        (keep, audit, ledger), = self.apply([row])
        self.assertEqual((self.fake.posts[0][1]["externalId"], keep, ledger["txn_id"]), (noref, None, noref))
        self.assertIn("no bank reference", audit["reason"])
        self.assertIn("cheque 4411", audit["reason"])
        # The next apply finds it in the ledger, and so does Fineract's own guard if the ledger were lost.
        self.assertEqual(self.apply([row], ledger_ids=[noref]), [(None, None, None)])
        self.assertEqual(self.fake.posts, [])
        earlier = {"id": 519, "externalId": noref, "amount": 8450.0, "manuallyReversed": False}
        (_, audit, _), = self.apply([row], FakeFineract(transactions=[earlier]))
        self.assertEqual((audit["action"], self.fake.posts), ("recovered", []))

    def test_the_amount_and_date_come_from_the_statement_not_the_edited_file(self):
        row = {**queued("1", "post", "L1", "Asha"), "amount": "8450", "date": "05-09-2026"}  # as Excel saves them
        list(apply([row], {"1": line("1")}, {"L1": 7}, fake := FakeFineract(), PAYMENT_TYPES, set(), set()))
        self.assertEqual((fake.posts[0][1]["transactionAmount"], fake.posts[0][1]["transactionDate"]),
                         ("8450.00", "2026-09-05"))

    def test_a_line_already_in_the_ledger_leaves_the_queue_without_a_second_post(self):
        self.assertEqual(self.apply([queued("1", "post", "L1", "Asha")], ledger_ids=["1"]), [(None, None, None)])
        self.assertEqual(self.fake.posts, [])

    def test_fineracts_duplicate_guard_still_holds(self):
        earlier = {"id": 519, "externalId": "1", "amount": 8450.0, "manuallyReversed": False}
        (keep, audit, ledger), = self.apply([queued("1", "post", "L1", "Asha")], FakeFineract(transactions=[earlier]))
        self.assertEqual((keep, audit["action"], ledger["fineract_txn_id"], self.fake.posts), (None, "recovered", 519, []))

    def test_a_failed_post_is_logged_and_stays_in_the_queue(self):
        refused = FakeFineract(post_error={"error": "Validation Error: loan is closed", "status_code": 400})
        (keep, audit, ledger), = self.apply([queued("1", "post", "L1", "Asha", "ok")], refused)
        self.assertEqual((audit["action"], ledger), ("failed", None))
        self.assertIn("loan is closed", keep["note"])

    def test_reject_writes_an_audit_row_and_leaves_the_queue(self):
        (keep, audit, ledger), = self.apply([queued("1", "reject", "", "Asha", "a refund, not a repayment")])
        self.assertEqual((keep, ledger, self.fake.posts), (None, None, []))
        self.assertEqual(audit, {"txn_id": "1", "action": "rejected", "loan_ref": "",
                                 "reason": "manual: a refund, not a repayment", "approver": "Asha"})

    def test_a_line_already_rejected_is_not_logged_again(self):
        self.assertEqual(self.apply([queued("1", "reject", "", "Asha")], rejected_ids=["1"]), [(None, None, None)])


class ReportTest(unittest.TestCase):
    LEDGER = [
        {"txn_id": "1", "date": "2026-09-05", "loan_ref": "L1", "amount": "8450.00", "partner_share": "6760.00", "lender_share": "1690.00"},
        {"txn_id": "2", "date": "2026-09-28", "loan_ref": "L1", "amount": "0.03", "partner_share": "0.02", "lender_share": "0.01"},
        {"txn_id": "3", "date": "2026-10-05", "loan_ref": "L1", "amount": "8450.00", "partner_share": "6760.00", "lender_share": "1690.00"},
        {"txn_id": "4", "date": "2026-09-06", "loan_ref": "L2", "amount": "1000.33", "partner_share": "800.26", "lender_share": "200.07"}]

    def test_one_row_per_loan_per_month(self):
        rows = by_loan_month(self.LEDGER)
        self.assertEqual([(r["month"], r["loan_ref"], r["payments"]) for r in rows],
                         [("2026-09", "L1", 2), ("2026-09", "L2", 1), ("2026-10", "L1", 1)])
        self.assertEqual(rows[0], {"month": "2026-09", "loan_ref": "L1", "payments": 2, "amount": "8450.03",
                                   "partner_share": "6760.02", "lender_share": "1690.01"})

    def test_totals_equal_the_ledger(self):
        self.assertEqual(totals(by_loan_month(self.LEDGER), self.LEDGER),
                         {"month": "TOTAL", "loan_ref": "", "payments": 4, "amount": "17900.36",
                          "partner_share": "14320.28", "lender_share": "3580.08"})

    def test_a_mismatch_fails_loudly(self):
        rows = by_loan_month(self.LEDGER)
        with self.assertRaises(SystemExit) as stop:
            totals(rows[:-1], self.LEDGER)
        self.assertIn("ledger", str(stop.exception))
        with self.assertRaises(SystemExit):
            totals([{**rows[0], "partner_share": "6760.03"}] + rows[1:], self.LEDGER)


class SimulateTest(unittest.TestCase):
    def test_posts_to_the_true_loan_and_rejects_unrelated_lines(self):
        key = [{"txn_id": "1", "case": "ambiguous", "true_loan_ref": "L2"},
               {"txn_id": "2", "case": "partial", "true_loan_ref": "L1"},
               {"txn_id": "2", "case": "duplicate", "true_loan_ref": "L1"},
               {"txn_id": "3", "case": "unrelated", "true_loan_ref": ""},
               {"txn_id": "5", "case": "clean", "true_loan_ref": "L1"}]
        mine = queued("4", "reject", "", "Asha", "mine")
        rows = simulate([queued("1"), queued("2"), queued("3"), mine, queued("5")], key)
        self.assertEqual([(r["decision"], r["loan_ref"], r["approver"]) for r in rows[:3]],
                         [("post", "L2", DEMO), ("post", "L1", DEMO), ("reject", "", DEMO)])
        self.assertEqual(rows[3], mine)            # a person's decision is never overwritten
        self.assertEqual(rows[4]["decision"], "")  # already paid: not a case the demo covers, left for a person
        self.assertIn("simulated", DEMO)


class RunTwiceTest(unittest.TestCase):
    def test_an_already_paid_line_is_logged_once_not_on_every_run(self):
        d = {"txn_id": "1", "date": "2026-09-05", "amount": "8450.00", "outcome": "post", "loan_ref": "L1",
             "reason": "fit", "candidates": "L1"}
        fake = FakeFineract(paid=[1])
        run = lambda audit: list(post_all([d], {"1": "UPI/1/R KUMAR"}, {"L1": 7}, fake, PAYMENT_TYPES,  # noqa: E731
                                          handled([], audit)))
        (first, _), = run([])
        self.assertEqual(first["action"], "queued")
        self.assertEqual(run([first]), [])

    def test_handled_is_the_ledger_plus_lines_queued_earlier(self):
        audit = [{"txn_id": "2", "action": "queued"}, {"txn_id": "3", "action": "failed"}]
        self.assertEqual(handled([{"txn_id": "1"}], audit), {"1", "2"})  # a failed post is tried again


class TrueLoanRefTest(unittest.TestCase):
    def test_the_answer_key_names_the_loan_that_really_paid(self):
        _, key = generate(STATEMENT_SCHEDULES)
        self.assertEqual(key, generate(STATEMENT_SCHEDULES)[1])
        self.assertEqual(list(key[0])[-1], "true_loan_ref")
        for row in key:
            self.assertEqual(bool(row["true_loan_ref"]), row["case"] not in ("unrelated", "missed"), row)
            if row["expected_outcome"] == "post":
                self.assertEqual(row["true_loan_ref"], row["expected_loan_ref"])
        self.assertTrue({"partial", "overpaid", "paid_by_other"} <= {r["case"] for r in key if r["true_loan_ref"]})


if __name__ == "__main__":
    unittest.main()
