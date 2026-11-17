"""Suggestions for unclear lines (stage two): the local shortlist, and the optional AI ranking with fakes only.

No test here reaches any provider: FakeSend stands in for a sender, Opener for urlopen.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import posting.run as files  # noqa: E402
from ai import client, rank  # noqa: E402
from review import build as queue_file  # noqa: E402
from review.suggest import suggest  # noqa: E402
from tests.test_ai import KEY, FakeSend, model_says  # noqa: E402
from tests.test_ui_connect import Page  # noqa: E402


def loan(ref, first, last, *instalments, n=1):
    return {"loan_ref": ref, "loan_id": n, "account_no": f"{n:09d}", "client_id": n, "firstname": first, "lastname": last,
            "instalments": [{"due": due, "amount": amount} for due, amount in instalments]}


LOANS = [loan("L1", "Ramesh", "Kumar", ("2026-09-05", 8450.0), ("2026-10-05", 8450.0), n=1),
         loan("L2", "Rajesh", "Kumar", ("2026-09-05", 6100.0), n=2),
         loan("L3", "Anita", "Sharma", ("2026-09-10", 5200.0), n=3),
         loan("L4", "Sunil", "Verma", ("2026-09-07", 12000.0), n=4),
         loan("L5", "Meena", "Iyer", ("2026-09-10", 5000.0), n=5),
         loan("L6", "Priya", "Nair", ("2026-03-01", 3300.0), n=6)]


def line(narration, amount="8450.00", day="2026-09-05"):
    return {"txn_id": "1", "date": day, "amount": amount, "narration": narration}


def refs(suggestions):
    return [s["loan_ref"] for s in suggestions]


class ShortlistTest(unittest.TestCase):
    def test_a_relative_with_another_first_name_ranks_the_loan_whose_instalment_matches(self):
        out = suggest(line("UPI/123456/GEETA KUMA/geeta@okaxis/emi"), LOANS)
        self.assertEqual(refs(out)[:2], ["L1", "L2"])
        top = out[0]
        self.assertEqual(top["similarity"], "same surname, different first name (a relative?)")
        self.assertEqual((top["due"], top["instalment"], top["equal"], top["difference"]), ("2026-09-05", "8450.00", True, "0.00"))
        self.assertEqual(top["confidence"], "high")
        self.assertEqual((out[1]["equal"], out[1]["difference"]), (False, "2350.00"))
        self.assertNotEqual(out[1]["confidence"], "high")

    def test_a_misspelt_surname(self):
        out = suggest(line("NEFT-1-ANITA SHRAMA-EMI", "5200.00", "2026-09-10"), LOANS)
        self.assertEqual(refs(out)[0], "L3")
        self.assertIn("similar surname", out[0]["similarity"])
        self.assertTrue(out[0]["equal"])

    def test_an_initial_only(self):
        out = suggest(line("UPI/1/M IYER/m@ybl", "5000.00", "2026-09-10"), LOANS)
        self.assertEqual(refs(out), ["L5"])
        self.assertEqual(out[0]["similarity"], "same surname, first initial matches")

    def test_the_amount_equal_to_an_instalment_is_said_so_and_a_difference_is_shown(self):
        out = suggest(line("UPI/1/SUNIL VERMA/x", "9000.00", "2026-09-08"), LOANS)
        self.assertEqual(refs(out), ["L4"])
        self.assertEqual((out[0]["equal"], out[0]["difference"], out[0]["days"]), (False, "-3000.00", 1))
        same = suggest(line("UPI/1/SUNIL VERMA/x", "12000.00", "2026-09-08"), LOANS)[0]
        self.assertEqual((same["equal"], same["difference"]), (True, "0.00"))

    def test_no_plausible_loan_gives_nothing(self):
        for text in ("NEFT-1-ACME TRADERS", "NEFT-2-R K STORES", "NEFT-3-INTEREST CREDIT", "NEFT-4-SALARY ADV"):
            self.assertEqual(suggest(line(text, "4645.00", "2026-09-20"), LOANS), [], text)
        # ADV is as close to YADAV as a misspelling (0.75), but three letters are too few to say so.
        yadav = [loan("L7", "Amit", "Yadav", ("2026-09-20", 4000.0))]
        self.assertEqual(suggest(line("NEFT-4-SALARY ADV", "4645.00", "2026-09-20"), yadav), [])

    def test_at_most_five_and_never_a_loan_already_shown(self):
        many = [loan(f"K{n}", f"First{n}", "Kumar", ("2026-09-05", 1000.0 + n), n=n) for n in range(1, 12)]
        out = suggest(line("UPI/1/GEETA KUMAR/x", "1003.00"), many)
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0]["loan_ref"], "K3")
        self.assertNotIn("K3", refs(suggest(line("UPI/1/GEETA KUMAR/x", "1003.00"), many, exclude={"K3"})))

    def test_a_name_written_together(self):
        self.assertEqual(refs(suggest(line("UPI/1/RAMESHKUMA/r@ybl"), LOANS))[0], "L1")

    def test_a_bank_with_no_format_uses_the_narrations_words(self):
        out = suggest(line("BY TRANSFER-UPI/CR/507296189751/GEETA KUMAR/SBIN/emi"), LOANS, bank="other")
        self.assertEqual(refs(out)[0], "L1")

    def test_an_instalment_far_from_the_payment_is_not_offered_as_the_fact(self):
        out = suggest(line("UPI/1/PRIYA NAIR/x", "3300.00", "2026-09-05"), LOANS)
        self.assertEqual(refs(out), ["L6"])  # the name alone: a person may still want it
        self.assertEqual((out[0]["due"], out[0]["equal"], out[0]["confidence"]), ("", False, "low"))


SHORTLIST = suggest(line("UPI/123456/GEETA KUMA/geeta@okaxis/emi"), LOANS)


class AnswerTest(unittest.TestCase):
    def test_a_ref_not_in_the_shortlist_is_discarded_and_repeats_are_dropped(self):
        ranked = rank.valid({"ranked": [{"loan_ref": "L9", "reason": "made up"}, {"loan_ref": "L2", "reason": "first"},
                                        {"loan_ref": "L2", "reason": "again"}, {"loan_ref": "L1", "reason": "x"}]}, SHORTLIST)
        self.assertEqual(ranked, [{"loan_ref": "L2", "reason": "first"}, {"loan_ref": "L1", "reason": "x"}])

    def test_malformed_answers_are_none(self):
        for answer in (None, [], {"ranked": "L1"}, {"items": []}, {"ranked": [{"loan_ref": 1}, "L1"]},
                       {"ranked": [{"loan_ref": "L9", "reason": ""}]}):
            self.assertIsNone(rank.valid(answer, SHORTLIST), answer)

    def test_reasons_are_one_short_line_of_plain_text(self):
        (item,) = rank.valid({"ranked": [{"loan_ref": "L1", "reason": "<b>same\n\nsurname</b>\x00 " + "x" * 500}]}, SHORTLIST)
        self.assertLessEqual(len(item["reason"]), rank.MAX_REASON)
        self.assertNotIn("\n", item["reason"])
        self.assertNotIn("\x00", item["reason"])
        self.assertTrue(item["reason"].startswith("<b>same surname</b>"))  # kept as text; the page sets it as text
        self.assertEqual(rank.valid({"ranked": [{"loan_ref": "L1", "reason": 7}]}, SHORTLIST), [{"loan_ref": "L1", "reason": ""}])

    def test_the_payload_has_the_narration_and_the_shortlisted_names_only(self):
        body = rank.payload("UPI/123456/GEETA KUMA/geeta@okaxis/emi", SHORTLIST)
        sent = json.dumps(body)
        self.assertIn("GEETA KUMA", sent)
        for s in SHORTLIST:
            self.assertIn(s["loan_ref"], sent)
            self.assertIn(s["borrower"], sent)
        for fact in ("8450", "6100", "2026-09-05", "2350", "account_no", "000000001", "client_id", "phone", "balance"):
            self.assertNotIn(fact, sent)
        self.assertNotIn("Anita", sent)  # not shortlisted, so not sent
        self.assertLessEqual(len(rank.listing(SHORTLIST * 3)), 5)

    def test_the_ranking_puts_the_models_order_first_and_keeps_our_facts(self):
        merged = rank.merge(SHORTLIST, [{"loan_ref": "L2", "reason": "the narration says so"}])
        self.assertEqual(refs(merged), ["L2", "L1"])
        self.assertEqual((merged[0]["ai_reason"], merged[0]["instalment"]), ("the narration says so", "6100.00"))
        self.assertEqual(merged[1]["ai_reason"], "")


class RankTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cache = Path(folder.name) / "ai_suggest_cache.json"

    def test_malformed_or_failed_answers_leave_the_local_order(self):
        for answer in ("not json", {"ranked": "L1"}, client.AIError("could not reach the AI service", 0)):
            ranking, problem = rank.rank("UPI/1/GEETA KUMA", SHORTLIST, FakeSend(answer), self.cache)
            self.assertIsNone(ranking)
            self.assertTrue(problem)
        self.assertFalse(self.cache.exists() and json.loads(self.cache.read_text()))

    def test_the_cache_keys_on_provider_model_narration_and_shortlist(self):
        good = {"ranked": [{"loan_ref": "L2", "reason": "r"}]}
        send = FakeSend(good, good, good, good)
        send.scope = "claude:claude-haiku-5-5"
        rank.rank("UPI/1/GEETA KUMA", SHORTLIST, send, self.cache)
        rank.rank("UPI/1/GEETA KUMA", SHORTLIST, send, self.cache)
        self.assertEqual(len(send.bodies), 1)
        rank.rank("UPI/2/GEETA KUMA", SHORTLIST, send, self.cache)  # another narration
        rank.rank("UPI/1/GEETA KUMA", SHORTLIST[:1], send, self.cache)  # another shortlist (L2 not in it: discarded)
        self.assertEqual(len(send.bodies), 3)
        send.scope = "openai:gpt-6.1-sol"
        rank.rank("UPI/1/GEETA KUMA", SHORTLIST, send, self.cache)
        self.assertEqual(len(send.bodies), 4)
        self.assertEqual(rank.cached("UPI/1/GEETA KUMA", SHORTLIST, "claude:claude-haiku-5-5", self.cache),
                         [{"loan_ref": "L2", "reason": "r"}])

    def test_an_edited_cache_is_checked_again(self):
        self.cache.write_text(json.dumps({rank.key("UPI/1/G", SHORTLIST, "s"): [{"loan_ref": "L9", "reason": "x"}]}))
        self.assertIsNone(rank.cached("UPI/1/G", SHORTLIST, "s", self.cache))


class ConsentTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        self.env = self.data / ".env"
        self.env.write_text(f"ANTHROPIC_API_KEY={KEY}\n")

    def test_suggestions_need_their_own_consent_as_well_as_the_readers(self):
        cfg = client.config(client.read_env(self.env))
        client.record_consent(self.data, cfg)
        client.sender(self.env, self.data, lambda *a, **k: None)  # the reader may run
        with self.assertRaises(client.AIError) as caught:
            client.sender(self.env, self.data, lambda *a, **k: None, kind="suggest")
        self.assertIn("consent", str(caught.exception))
        client.record_consent(self.data, cfg, "suggest")
        client.sender(self.env, self.data, lambda *a, **k: None, kind="suggest")
        self.assertTrue(client.consent_given(self.data, cfg))  # recording the second kept the first

    def test_the_second_consent_alone_is_not_enough(self):
        cfg = client.config(client.read_env(self.env))
        client.record_consent(self.data, cfg, "suggest")
        self.assertFalse(client.consent_given(self.data, cfg))
        with self.assertRaises(client.AIError):
            client.sender(self.env, self.data, lambda *a, **k: None, kind="suggest")

    def test_a_change_of_provider_or_address_asks_again(self):
        cfg = client.config(client.read_env(self.env))
        client.record_consent(self.data, cfg)
        client.record_consent(self.data, cfg, "suggest")
        other = client.config({"AI_PROVIDER": "other", "AI_BASE_URL": "http://localhost:11434/v1", "AI_MODEL": "llama3.1"})
        self.assertFalse(client.consent_given(self.data, other, "suggest"))
        client.record_consent(self.data, other, "suggest")
        moved = client.config({"AI_PROVIDER": "other", "AI_BASE_URL": "https://ai.example.com/v1", "AI_MODEL": "llama3.1"})
        self.assertFalse(client.consent_given(self.data, moved, "suggest"))

    def test_the_wording(self):
        cfg = client.config({"AI_PROVIDER": "gemini"})
        self.assertEqual(client.consent_text(cfg, "suggest"),
                         "I understand that, for lines I ask about, the narration and up to 5 possible borrowers' names "
                         "are sent to Google's Gemini API (model gemini-3.8-flash)")
        local = client.config({"AI_PROVIDER": "other", "AI_BASE_URL": "http://localhost:11434/v1", "AI_MODEL": "llama3.1"})
        self.assertIn("nothing leaves it", client.consent_text(local, "suggest"))


RELATIVE = ("700000000009", "2026-10-05", "8450.00", "UPI/700000000009/GEETA KUMAR/geeta@okaxis/emi oct")


class PageTest(Page):
    """The Review tab's suggestions over HTTP, on the demo's data plus one line paid by a relative."""

    def setUp(self):
        super().setUp()
        # Anita Sharma's September instalment paid by a relative: no loan fits the name, so no candidates.
        line = dict(zip(("txn_id", "date", "amount", "narration"),
                        ("700000000009", "2026-09-10", "5200.00", "UPI/700000000009/SUNITA SHARMA/s@ybl/emi sep")))
        rows = queue_file.read_queue()
        rows.append({**line, "reason": "no loan fits 'SUNITA SHARMA'", "candidates": "", "decision": "", "loan_ref": "",
                     "approver": "", "note": ""})
        queue_file.write_queue(rows)
        with (self.data / "bank_statement.csv").open("a", newline="") as f:
            f.write(",".join(line.values()) + "\r\n")
        self.txn = line["txn_id"]

    def row(self):
        return next(r for r in self.get("/api/state")["queue"] if r["txn_id"] == self.txn)

    def ai_on(self, second=True):
        self.post("/api/settings", {"values": {"ANTHROPIC_API_KEY": KEY}})
        self.post("/api/consent", {"agree": True})
        if second:
            self.post("/api/consent", {"agree": True, "kind": "suggest"})

    def test_a_line_with_no_candidate_shows_local_suggestions_without_any_call(self):
        row = self.row()
        self.assertEqual(row["candidates"], [])
        top = row["suggestions"][0]
        self.assertEqual((top["loan_ref"], top["borrower"], top["equal"], top["loan_id"]), ("L9003", "Anita Sharma", True, 3))
        self.assertEqual(self.opener.requests, [])
        unrelated = next(r for r in self.get("/api/state")["queue"] if "SUNRISE" in r["narration"])
        self.assertEqual(unrelated["suggestions"], [])

    def test_ask_ai_needs_both_consents_and_falls_back_to_the_local_list(self):
        self.assertFalse(self.get("/api/settings")["ai"]["suggest_ready"])
        self.ai_on(second=False)
        settings = self.get("/api/settings")["ai"]
        self.assertFalse(settings["suggest_ready"])
        self.assertTrue(settings["suggest_consent_text"].startswith("I understand that, for lines I ask about"))
        result = self.post("/api/suggest", {"txn_id": self.txn})
        self.assertEqual(self.opener.requests, [])
        self.assertIn("consent", result["note"])
        self.assertEqual(result["suggestions"], self.row()["suggestions"])
        self.assertEqual(result["ai"], "")

    def test_ask_ai_sends_the_narration_and_the_names_only_and_never_changes_the_queue(self):
        self.ai_on()
        self.assertTrue(self.get("/api/settings")["ai"]["suggest_ready"])
        before = (self.data / "queue.csv").read_bytes()
        shortlist = self.row()["suggestions"]
        self.opener.answers.append(model_says({"ranked": [{"loan_ref": "L9003", "reason": "<i>same surname</i>"},
                                                          {"loan_ref": "L0001", "reason": "not on the list"}]}))
        result = self.post("/api/suggest", {"txn_id": self.txn})
        self.assertEqual(result["ai"], "claude/claude-haiku-5-5")
        self.assertEqual((result["suggestions"][0]["loan_ref"], result["suggestions"][0]["ai_reason"]),
                         ("L9003", "<i>same surname</i>"))
        self.assertEqual(sorted(s["loan_ref"] for s in result["suggestions"]), sorted(s["loan_ref"] for s in shortlist))
        sent = self.opener.requests[0].data.decode()
        self.assertIn("SUNITA SHARMA", sent)
        self.assertIn("Anita Sharma", sent)
        for fact in ("5200", "2026-09-10", "client_id", "loan_id", "000000003"):
            self.assertNotIn(fact, sent)
        self.assertEqual((self.data / "queue.csv").read_bytes(), before)  # no decision written
        self.assertEqual(self.fc.posts, [])
        self.post("/api/suggest", {"txn_id": self.txn})
        self.assertEqual(len(self.opener.requests), 1)  # cached

    def test_a_malformed_ai_answer_shows_the_local_suggestions_with_a_note(self):
        self.ai_on()
        self.opener.answers.append(model_says("I think it is Anita"))
        result = self.post("/api/suggest", {"txn_id": self.txn})
        self.assertEqual(result["suggestions"], self.row()["suggestions"])
        self.assertIn("local suggestions", result["note"])

    def decide(self, **extra):
        body = {"txn_id": self.txn, "decision": "post", "loan_ref": "L9003", "approver": "Asha", "note": "her sister", **extra}
        return self.post("/api/decide", body)

    def test_the_audit_records_a_local_suggestion_as_its_origin(self):
        self.assertEqual(self.decide(origin="local"), {"done": "posted"})
        audit, = files.read("audit.csv")
        self.assertEqual((audit["approver"], audit["reason"]), ("Asha", "manual: [suggested by local similarity] her sister"))

    def test_the_audit_records_the_ai_and_its_model(self):
        self.ai_on()
        self.opener.answers.append(model_says({"ranked": [{"loan_ref": "L9003", "reason": "same surname"}]}))
        self.post("/api/suggest", {"txn_id": self.txn})
        self.assertEqual(self.decide(origin="ai"), {"done": "posted"})
        audit, = files.read("audit.csv")
        self.assertEqual(audit["reason"], "manual: [suggested by AI (claude/claude-haiku-5-5)] her sister")

    def test_an_origin_that_does_not_hold_is_not_recorded(self):
        # "ai" without the AI having been asked counts as the local list; a loan not on the list has no origin.
        self.decide(origin="ai", note="first")
        self.assertIn("[suggested by local similarity] first", files.read("audit.csv")[0]["reason"])
        result = self.post("/api/decide", {"txn_id": "700000000002", "decision": "post", "loan_ref": "L9003",
                                           "approver": "Asha", "note": "part", "origin": "local"})
        self.assertEqual(result, {"done": "posted"})
        self.assertEqual(files.read("audit.csv")[1]["reason"], "manual: part")

    def test_the_page_never_puts_text_in_as_html(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertNotIn("innerHTML", page)
        self.assertIn("AI suggestion, check it", page)


if __name__ == "__main__":
    unittest.main()
