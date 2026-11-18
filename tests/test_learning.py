"""Learning from approvals: payer keys, the links log, the matcher's account rule, review.apply, the page, the replay.

Made-up data only; nothing reaches Fineract or an AI provider (fakes stand in for both).
"""
import csv
import io
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import posting.run as files  # noqa: E402
from locale_settings import DEFAULT  # noqa: E402
from matcher import payer  # noqa: E402
from matcher import run as matcher_run  # noqa: E402
from matcher.match import decide  # noqa: E402
from review import apply as review_apply  # noqa: E402
from review import build as queue_file  # noqa: E402
from review import learn  # noqa: E402
from review.measure_learning import replay  # noqa: E402
from review.simulate import APPROVER as SIMULATED  # noqa: E402
from statement import import_structured  # noqa: E402
from tests.test_matcher import SCHEDULES  # noqa: E402
from tests.test_ai import KEY, model_says  # noqa: E402
from tests.test_ui_connect import Page  # noqa: E402
from ui import serve  # noqa: E402

FIXTURES = Path(__file__).with_name("fixtures")
TXN = "700000000001"  # the demo's line from rkumar@okaxis that fits two loans, L9001 and L9002
VPA = "vpa:rkumar@okaxis"
IBAN = "DE89370400440532013000"  # the standard textbook example: a valid check, no real account


class KeyTest(unittest.TestCase):
    def test_upi_lines_give_their_vpa(self):
        self.assertEqual(payer.key("UPI/189749/SANJAY PAT/sanjay@oksbi"), "vpa:sanjay@oksbi")
        self.assertEqual(payer.key("UPI/908839/KAVITA NAI/Kavita.N@okSBI/emi feb"), "vpa:kavita.n@oksbi")
        self.assertEqual(payer.key("UPI-SANJAY PAT-sanjay@oksbi-UTIB0000001-507296189749-emi", "hdfc"), "vpa:sanjay@oksbi")
        self.assertEqual(payer.key("UPI/507296189749/SANJAY PAT/sanjay@oksbi/emi", "icici"), "vpa:sanjay@oksbi")

    def test_anything_ambiguous_gives_nothing(self):
        self.assertEqual(payer.key("UPI/1/A B/ab@okaxis/to cd@ybl"), "")  # two VPAs
        self.assertEqual(payer.key("UPI/1/A B/no vpa here/emi"), "")
        self.assertEqual(payer.key("NEFT-123456789012-RAMESH KUMAR-50100012345678"), "")  # NEFT text: not trusted
        self.assertEqual(payer.key("IMPS-123456789012-RAMESH-SBIN-XXXXXXXX9012-emi", "hdfc"), "")
        self.assertEqual(payer.key("UPI/1/A B/ab@okaxis/emi", "sbi"), "")  # no UPI layout for that bank
        self.assertEqual(payer.key("UPI/1/A B/ab@okaxis/emi", "other"), "")
        self.assertEqual(payer.key("a@gmail.com paid", "synthetic"), "")

    def test_structured_lines_give_the_account_field(self):
        self.assertEqual(payer.key(f"Anna Weber | rent | {IBAN} | SEPA", "camt053"), f"iban:{IBAN}")
        self.assertEqual(payer.key("Anna Weber | rent | DE89 3704 0044 0532 0130 00", "mt940"), f"iban:{IBAN}")
        self.assertEqual(payer.key("John Smith | ref | 12-34-56 12345678 | FPS", "camt053"), "acct:12345612345678")
        self.assertEqual(payer.key("Anna Weber | rent", "camt053"), "")
        self.assertEqual(payer.key("Anna Weber | rent |  | SEPA", "camt053"), "")
        self.assertEqual(payer.key("Anna Weber | rent | ABC123 | SEPA", "camt053"), "")

    def test_an_iban_needs_its_mod_97_check(self):
        self.assertTrue(payer.iban_ok(IBAN))
        self.assertTrue(payer.iban_ok("GB82WEST12345698765432"))
        self.assertFalse(payer.iban_ok("GB82WEST12345698765433"))  # one digit off
        self.assertEqual(payer.key("Anna | x | DE89370400440532013001", "camt053"), "")
        self.assertEqual(payer.key("Anna | x | DE00999988880000000011", "camt053"), "")  # the fixtures' made-up IBAN

    def test_read_from_imported_files(self):
        gbp = replace(DEFAULT, currency="GBP")
        _, lines, _ = import_structured.read((FIXTURES / "camt053_uk.xml").read_text(encoding="utf-8"), gbp)
        self.assertIn("acct:00000012345678", [payer.key(line["narration"], "camt053") for line in lines])

    def test_mask_shows_the_kind_and_the_last_four_only(self):
        self.assertEqual(payer.mask(f"iban:{IBAN}"), "iban:****3000")
        self.assertEqual(payer.mask(VPA), "vpa:****axis")
        self.assertEqual(payer.mask(""), "")


def link(loan_ref="L1", status="active", key="vpa:rk@okaxis", loans=None):
    return {key: {"payer_key": key, "loan_ref": loan_ref, "learned_from_txn": "1", "approver": "Asha",
                  "learned_at": "2026-08-05T10:00:00", "status": status, "loans": loans or [loan_ref]}}


class LearnTest(unittest.TestCase):
    def test_a_first_approval_makes_one_active_link(self):
        row, why = payer.learn({}, VPA, "L1", "t1", "Asha", "2026-11-18T10:00:00")
        self.assertEqual(row, {"payer_key": VPA, "loan_ref": "L1", "learned_from_txn": "t1", "approver": "Asha",
                               "learned_at": "2026-11-18T10:00:00", "status": "active"})
        self.assertEqual(why, "learned payer account vpa:****axis for L1")

    def test_the_same_loan_again_changes_nothing(self):
        state = payer.current([payer.learn({}, VPA, "L1", "t1", "Asha", "x")[0]])
        self.assertEqual(payer.learn(state, VPA, "L1", "t2", "Ravi", "y"), (None, ""))

    def test_another_loan_disables_the_key_whatever_remember_says(self):
        state = payer.current([payer.learn({}, VPA, "L1", "t1", "Asha", "x")[0]])
        for remember in (True, False):
            row, why = payer.learn(state, VPA, "L2", "t2", "Ravi", "y", remember)
            self.assertEqual((row["status"], row["loan_ref"]), ("disabled", "L2"))
            self.assertIn("approved for L1 before and now for L2: link disabled", why)
        disabled = payer.current([state[VPA], row])
        self.assertEqual((disabled[VPA]["status"], disabled[VPA]["loans"]), ("disabled", ["L1", "L2"]))
        self.assertEqual(payer.learn(disabled, VPA, "L1", "t3", "Asha", "z"), (None, ""))  # never silently back on

    def test_no_key_or_no_remember_learns_nothing(self):
        self.assertEqual(payer.learn({}, "", "L1", "t1", "Asha", "x"), (None, ""))
        self.assertEqual(payer.learn({}, VPA, "L1", "t1", "Asha", "x", remember=False), (None, ""))


def line(narration, amount="8450.00", day="2026-09-05", txn="100000000001"):
    return {"txn_id": txn, "date": day, "amount": amount, "narration": narration}


class MatcherRuleTest(unittest.TestCase):
    LINES = [line("UPI/1/RK/rk@okaxis/emi"), line("UPI/2/R KUMAR/rk@okaxis/emi", txn="2"),
             line("NEFT-3-LAKSHMI SHARMA-emi", "3100.50", txn="3"), line("NACH/ACH DR/SCPL000000005/x/1", "7000.00",
                                                                         "2026-09-20", "4"),
             line("UPI/5/ANITA NAIR/anita@ybl", "5000.00", "2026-09-11", "5"), line("UPI/1/RK/rk@okaxis/emi")]

    def test_no_links_is_exactly_the_rules_alone(self):
        alone = decide(self.LINES, SCHEDULES)
        self.assertEqual(decide(self.LINES, SCHEDULES, links=None), alone)
        self.assertEqual(decide(self.LINES, SCHEDULES, links={}), alone)
        self.assertEqual(decide(self.LINES, SCHEDULES, links=link(key="vpa:nobody@ybl")), alone)

    def test_an_active_link_posts_when_the_instalment_is_due(self):
        d, = decide([line("UPI/1/RK/rk@okaxis/emi")], SCHEDULES, links=link())
        self.assertEqual((d["outcome"], d["loan_ref"]), ("post", "L1"))
        self.assertEqual(d["reason"], "payer account vpa:****axis was approved for L1 by Asha on 2026-08-05; "
                                      "instalment due 2026-09-05")
        self.assertEqual(decide([line("UPI/1/RK/rk@okaxis/emi")], SCHEDULES)[0]["outcome"], "queue")  # without it

    def test_amount_or_date_that_does_not_fit_queues_with_the_linked_loan_first(self):
        for amount, day in (("9000.00", "2026-09-05"), ("8450.00", "2026-09-20")):
            d, = decide([line("UPI/1/R KUMAR/rk@okaxis/emi", amount, day)], SCHEDULES, links=link("L2"))
            self.assertEqual((d["outcome"], d["candidates"][0]), ("queue", "L2"))
            self.assertIn("approved for L2 before, but the amount/date does not fit", d["reason"])

    def test_a_name_that_fits_another_borrower_and_not_this_one_queues(self):
        d, = decide([line("UPI/1/LAKSHMI SHARMA/rk@okaxis/emi")], SCHEDULES, links=link())
        self.assertEqual((d["outcome"], d["candidates"]), ("queue", ["L1", "L2"]))
        self.assertIn("'LAKSHMI SHARMA' fits another borrower and not this one", d["reason"])
        self.assertEqual(decide([line("UPI/1/R KUMAR/rk@okaxis/emi")], SCHEDULES, links=link())[0]["outcome"], "post")

    def test_another_loan_the_name_could_be_for_owing_the_same_queues(self):
        # L5 Suresh Patel and L6 Sanjay Patel owe 7000 on the same day: a shared family account is not proof.
        for name in ("S PATEL", "XYZ"):
            d, = decide([line(f"UPI/1/{name}/patel@ybl", "7000.00", "2026-09-20")], SCHEDULES,
                        links=link("L5", key="vpa:patel@ybl"))
            self.assertEqual((d["outcome"], d["candidates"]), ("queue", ["L5", "L6"]))
            self.assertIn("approved for L5 before, but 1 other loan(s) fit the name and the amount too", d["reason"])
        d, = decide([line("UPI/1/SURESH PATEL/patel@ybl", "7000.00", "2026-09-20")], SCHEDULES,
                    links=link("L5", key="vpa:patel@ybl"))
        self.assertEqual(d["outcome"], "queue")  # the name rule would queue it too (same initial, same amount)

    def test_a_disabled_link_or_one_to_an_unknown_loan_is_ignored(self):
        alone = decide([line("UPI/1/RK/rk@okaxis/emi")], SCHEDULES)
        self.assertEqual(decide([line("UPI/1/RK/rk@okaxis/emi")], SCHEDULES, links=link(status="disabled")), alone)
        self.assertEqual(decide([line("UPI/1/RK/rk@okaxis/emi")], SCHEDULES, links=link("L99")), alone)

    def test_the_older_rules_still_come_first_and_a_noref_line_still_queues(self):
        d, = decide([line("NACH/ACH DR/SCPL000000005/x/1 rk@okaxis", "7000.00", "2026-09-20")], SCHEDULES, links=link())
        self.assertEqual(d["loan_ref"], "L5")
        d, = decide([line("UPI/1/RK/rk@okaxis/emi", txn="NOREF-2026-09-05-abcd1234")], SCHEDULES, links=link())
        self.assertEqual(d["outcome"], "queue")
        dup = decide([line("UPI/1/RK/rk@okaxis/emi")] * 2, SCHEDULES, links=link())
        self.assertEqual([d["outcome"] for d in dup], ["post", "ignore"])

    def test_matcher_run_reads_the_links_file_only_if_it_exists(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / "schedules.json").write_text(json.dumps(SCHEDULES))
            with (data / "bank_statement.csv").open("w", newline="") as f:
                csv.writer(f).writerows([["txn_id", "date", "amount", "narration"], ["1", "2026-09-05", "8450.00",
                                                                                     "UPI/1/RK/rk@okaxis/emi"]])
            self.assertEqual(matcher_run.run(data=data)[0]["outcome"], "queue")
            before = (data / "decisions.csv").read_bytes()
            (data / matcher_run.LINKS).write_text(
                "payer_key,loan_ref,learned_from_txn,approver,learned_at,status\n"
                "vpa:rk@okaxis,L1,0,Asha,2026-08-05T10:00:00,active\n", encoding="utf-8")
            self.assertEqual(matcher_run.run(data=data)[0]["outcome"], "post")
            self.assertNotIn(b"rk@okaxis", (data / "decisions.csv").read_bytes())  # masked in the reason
            (data / matcher_run.LINKS).unlink()
            matcher_run.run(data=data)
            self.assertEqual((data / "decisions.csv").read_bytes(), before)


class ApplyLearnsTest(unittest.TestCase):
    """review.apply over the demo's data: the CSV (Excel) way of deciding."""

    def setUp(self):
        import tempfile
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        for target, name, value in ((files, "DATA", self.data), (queue_file, "QUEUE", self.data / "queue.csv"),
                                    (sys, "stdout", io.StringIO())):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.fc = serve.DemoFineract(serve.demo(self.data))

    def apply(self, **edits):
        rows = queue_file.read_queue()
        rows[0].update({"decision": "post", "loan_ref": "L9002", "approver": "Asha", "note": "called", **edits})
        queue_file.write_queue(rows)
        return review_apply.run(self.fc, serve.ok, only=TXN)

    def links(self):
        return learn.read(self.data)

    def audit(self):
        return files.read("audit.csv")

    def test_a_person_s_posted_approval_teaches_the_account(self):
        self.apply()
        row, = self.links()
        self.assertEqual((row["payer_key"], row["loan_ref"], row["learned_from_txn"], row["approver"], row["status"]),
                         (VPA, "L9002", TXN, "Asha", "active"))
        posted, learned = self.audit()
        self.assertEqual((posted["action"], learned["action"], learned["loan_ref"], learned["approver"]),
                         ("posted", "payer_learned", "L9002", "Asha"))
        self.assertEqual(learned["reason"], "learned payer account vpa:****axis for L9002")
        self.assertNotIn("rkumar", (self.data / "audit.csv").read_text())

    def test_remember_no_in_the_queue_file_opts_out(self):
        self.apply(remember="No")
        self.assertEqual(self.links(), [])
        self.assertEqual([a["action"] for a in self.audit()], ["posted"])

    def test_nothing_is_learned_from_a_reject_a_refusal_the_demo_reviewer_or_a_recovered_post(self):
        self.apply(decision="reject", loan_ref="")
        self.assertEqual(self.links(), [])
        self.setUp()
        self.apply(loan_ref="NOPE")  # a typed loan_ref that is no loan: refused, nothing posted
        self.assertEqual((self.links(), self.fc.posted), ([], {}))
        self.setUp()
        self.apply(approver=SIMULATED)
        self.assertEqual(self.links(), [])
        self.setUp()
        self.fc.posted[2] = [{"id": 1, "externalId": TXN, "amount": 8450.0, "manuallyReversed": False}]
        self.apply()
        self.assertEqual(self.audit()[0]["action"], "recovered")
        self.assertEqual(self.links(), [])

    def test_a_line_without_a_bank_reference_teaches_nothing(self):
        noref = {"txn_id": "NOREF-2026-09-05-abcd1234", "narration": "UPI/x/R KUMAR/rkumar@okaxis/emi"}
        self.assertIsNone(learn.record(self.data, noref, "synthetic", "L9002", "Asha", True))
        self.assertEqual(self.links(), [])

    def test_a_later_approval_to_another_loan_disables_and_the_matcher_stops_using_it(self):
        self.apply()
        self.assertEqual(matcher_run.run(data=self.data)[0]["reason"][:24], "payer account vpa:****ax")
        line = {"txn_id": "700000000099", "narration": "UPI/700000000099/R KUMAR/rkumar@okaxis/emi"}
        audit = learn.record(self.data, line, "synthetic", "L9001", "Ravi", False)
        self.assertEqual((audit["action"], self.links()[-1]["status"]), ("payer_conflict", "disabled"))
        self.assertIn("2 loans fit", matcher_run.run(data=self.data)[0]["reason"])


class PageLearnsTest(Page):
    """The Review tab: the Remember box, the line's payer account, the Learned accounts panel."""

    def setUp(self):
        super().setUp()
        # A second payment from the same account, a part payment: it stays queued.
        later = {"txn_id": "700000000005", "date": "2026-10-06", "amount": "8000.00",
                 "narration": "UPI/700000000005/RAMESH KUMA/rkumar@okaxis/part"}
        rows = queue_file.read_queue()
        rows.append({**later, "reason": "no loan fits", "candidates": "", "decision": "", "loan_ref": "",
                     "approver": "", "note": "", "remember": ""})
        queue_file.write_queue(rows)
        with (self.data / "bank_statement.csv").open("a", newline="") as f:
            f.write(",".join(later.values()) + "\r\n")

    def decide(self, **extra):
        return self.post("/api/decide", {"txn_id": TXN, "decision": "post", "loan_ref": "L9002", "approver": "Asha",
                                         "note": "", **extra})

    def row(self, txn="700000000005"):
        return next(r for r in self.get("/api/state")["queue"] if r["txn_id"] == txn)

    def test_approve_with_the_box_ticked_learns_and_the_page_shows_it_masked(self):
        self.assertEqual(self.row()["payer"]["status"], "new")
        self.assertEqual(self.decide(remember=True), {"done": "posted"})
        payer_row = self.row()["payer"]
        self.assertEqual((payer_row["masked"], payer_row["status"], payer_row["loan_ref"]), ("vpa:****axis", "active", "L9002"))
        panel = self.get("/api/links")
        self.assertEqual((panel["active"], panel["auto_matched"], panel["links"][0]["loans"]), (1, 0, ["L9002"]))
        for path in ("/api/links", "/api/state"):
            self.assertNotIn("vpa:rkumar", self.request("GET", path)[1])
        self.assertNotIn("rkumar", self.request("GET", "/api/links")[1])

    def test_approve_without_the_box_learns_nothing(self):
        self.assertEqual(self.decide(remember=False), {"done": "posted"})
        self.assertEqual(self.get("/api/links")["links"], [])
        self.assertEqual(learn.read(self.data), [])
        self.assertEqual(self.post("/api/decide", {"txn_id": "700000000003", "decision": "reject", "approver": "Asha",
                                                   "note": "", "remember": True}), {"done": "rejected"})
        self.assertEqual(learn.read(self.data), [])

    def test_a_link_is_turned_off_and_on_by_a_named_person_and_logged(self):
        self.decide(remember=True)
        link_id = self.get("/api/links")["links"][0]["id"]
        self.assertIn("Approver", self.post("/api/link", {"id": link_id, "status": "disabled"})["problem"])
        self.assertIn("never approved", self.post("/api/link", {"id": link_id, "status": "active", "loan_ref": "L9001",
                                                                "approver": "Ravi"})["problem"])
        off = self.post("/api/link", {"id": link_id, "status": "disabled", "approver": "Ravi"})
        self.assertEqual((off["done"], off["active"]), ("payer account vpa:****axis turned off by a person", 0))
        self.assertEqual(self.row()["payer"]["status"], "disabled")
        self.post("/api/link", {"id": link_id, "status": "active", "loan_ref": "L9002", "approver": "Ravi"})
        actions = [(a["action"], a["approver"]) for a in files.read("audit.csv")]
        self.assertEqual(actions[-2:], [("payer_disabled", "Ravi"), ("payer_enabled", "Ravi")])
        self.assertEqual(learn.read(self.data)[-1]["status"], "active")

    def test_a_conflict_shows_on_the_line(self):
        self.decide(remember=True)
        line = {"txn_id": "700000000077", "narration": "UPI/700000000077/R KUMAR/rkumar@okaxis/emi"}
        learn.record(self.data, line, "synthetic", "L9001", "Ravi", True)
        payer_row = self.row()["payer"]
        self.assertEqual((payer_row["status"], payer_row["loans"]), ("conflict", ["L9002", "L9001"]))

    def test_auto_matched_lines_are_counted_from_the_audit(self):
        to_audit = files.appender("audit.csv", files.AUDIT)
        to_audit({"at": "x", "txn_id": "1", "action": "posted", "loan_ref": "L9002", "approver": "auto (matcher rules)",
                  "reason": "payer account vpa:****axis was approved for L9002 by Asha on 2026-11-18; instalment due x"})
        to_audit.close()
        self.assertEqual(self.get("/api/links")["auto_matched"], 1)

    def test_the_ai_ranker_is_sent_exactly_the_same_with_a_learned_account(self):
        self.post("/api/settings", {"values": {"ANTHROPIC_API_KEY": KEY}})
        self.post("/api/consent", {"agree": True})
        self.post("/api/consent", {"agree": True, "kind": "suggest"})
        sent = []
        for learned in (False, True):
            if learned:
                (self.data / "ai_suggest_cache.json").unlink()  # so the second ask is sent, not cached
                self.decide(remember=True)
            self.opener.answers.append(model_says({"ranked": [{"loan_ref": "L9001", "reason": "same name"}]}))
            self.post("/api/suggest", {"txn_id": "700000000005"})
            sent.append(self.opener.requests[-1].data.decode())
        self.assertEqual(sent[0], sent[1])
        for text in ("vpa:", "****", "payer account", "L9002 by"):
            self.assertNotIn(text, sent[1])

    def test_the_page_has_the_remember_box_and_the_panel(self):
        page = self.request("GET", "/")[1]
        for text in ("Remember this payer account", "Learned payer accounts", "/api/links", "lines auto-matched this way"):
            self.assertIn(text, page)


class RepeatPayersTest(unittest.TestCase):
    """statement.generate's repeat_payers: a second statement; the default one is untouched."""

    def test_only_upi_narrations_change_and_families_share_one_account(self):
        from statement.generate import generate
        from tests.test_statement import SCHEDULES as LOANS
        plain, key = generate(LOANS)
        self.assertEqual((plain, key), generate(LOANS, repeat_payers=False))
        repeat, repeat_key = generate(LOANS, repeat_payers=True)
        self.assertEqual(repeat_key, key)
        for a, b in zip(plain, repeat):
            self.assertEqual({**a, "narration": ""}, {**b, "narration": ""})
            self.assertTrue(a["narration"] == b["narration"] or a["narration"].startswith("UPI/"))
        by_loan = {}
        truth = {k["txn_id"]: (k["true_loan_ref"], k["case"]) for k in key if k["case"] not in ("duplicate", "missed")}
        for line in repeat:
            ref, case = truth.get(line["txn_id"], ("", ""))
            if ref and case != "paid_by_other" and line["narration"].startswith("UPI/"):
                by_loan.setdefault(ref, set()).add(payer.key(line["narration"]))
        self.assertTrue(all(len(keys) == 1 for keys in by_loan.values()))  # one account per borrower
        shared = [k for k in set().union(*by_loan.values()) if sum(k in v for v in by_loan.values()) > 1]
        self.assertTrue(shared and all(".family@" in k for k in shared))


class ReplayTest(unittest.TestCase):
    """review.measure_learning's replay on a tiny made-up statement."""

    def test_an_approval_lets_the_next_payment_from_that_account_post_and_never_wrongly(self):
        schedules = [{"loan_ref": "L1", "loan_id": 1, "account_no": "1", "client_id": 1, "firstname": "Ramesh",
                      "lastname": "Kumar", "instalments": [{"due": f"2026-{m:02d}-05", "amount": 8450.0} for m in (8, 9, 10)]},
                     {"loan_ref": "L2", "loan_id": 2, "account_no": "2", "client_id": 2, "firstname": "Anita",
                      "lastname": "Nair", "instalments": [{"due": f"2026-{m:02d}-05", "amount": 8450.0} for m in (8, 9, 10)]}]
        lines = [line("UPI/1/GEETA K/fam@okaxis/emi", "8450.00", "2026-08-05", "1"),  # a relative: queued, approved L1
                 line("UPI/2/GEETA K/fam@okaxis/emi", "8450.00", "2026-09-05", "2"),  # now posts by the account
                 line("UPI/3/ANITA NAIR/fam@okaxis/emi", "8450.00", "2026-10-05", "3"),  # the name says L2: queued
                 line("UPI/4/ZZ/zz@ybl", "100.00", "2026-10-06", "4")]  # unrelated: rejected, nothing learned
        answer = [{"txn_id": t, "case": "x", "true_loan_ref": ref} for t, ref in (("1", "L1"), ("2", "L1"), ("3", "L2"),
                                                                                    ("4", ""))]
        r = replay(lines, answer, schedules)
        self.assertEqual((r["queue_before"], r["queue_after"], r["auto"], r["wrong"], r["rescued"]), (3, 3, 1, 0, 1))
        self.assertEqual((r["learned"], r["conflicts"], r["with_key"], r["distinct_keys"]), (1, 1, 4, 2))
        # The shared account costs one line the name rule alone would have posted: counted, never hidden.
        self.assertEqual((r["queued_by_link"], r["was_posted_before"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
