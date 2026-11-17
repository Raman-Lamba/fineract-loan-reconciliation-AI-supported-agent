"""The AI statement reader, with a fake Claude: no test here reaches Anthropic's API."""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai import client, layout, narration  # noqa: E402
from matcher.match import decide, payer_name  # noqa: E402
from statement.import_bank import convert  # noqa: E402

FIXTURES = Path(__file__).with_name("fixtures")
KEY = "sk-ant-test-NOT-A-REAL-KEY-0001"
# What a model would answer for each fixture's layout (row and column numbers from 0).
MAPS = {
    "icici_style.csv": {"header_row": 4, "date": 2, "date_format": "%d/%m/%Y", "narration": 4, "reference": 3,
                        "style": "two_columns", "credit": 6, "debit": 5, "amount": -1, "type": -1},
    "sbi_style.csv": {"header_row": 6, "date": 0, "date_format": "%d %b %Y", "narration": 2, "reference": 3,
                      "style": "two_columns", "credit": 5, "debit": 4, "amount": -1, "type": -1},
    "axis_style.csv": {"header_row": 3, "date": 0, "date_format": "%d-%m-%Y", "narration": 2, "reference": 1,
                       "style": "suffix", "credit": -1, "debit": -1, "amount": 3, "type": -1},
}
# The statement each fixture must import to: txn_id, date, amount, credits only, in the file's order.
EXPECTED = {
    "icici_style.csv": [("507296189749", "2026-09-05", "8450.00"), ("SBINR52026090600001", "2026-09-06", "3100.50"),
                        ("612345678901", "2026-09-07", "12000.00")],
    "sbi_style.csv": [("507296189750", "2026-09-05", "8450.00"), ("HDFCN52026090612345", "2026-09-06", "3100.50"),
                      ("612345678902", "2026-09-07", "12000.00"), ("507296189751", "2026-09-10", "5000.00")],
    "axis_style.csv": [("507296189760", "2026-09-05", "8450.00"), ("612345678903", "2026-09-07", "12000.00"),
                       ("HDFCN52026090899999", "2026-09-08", "3100.50")],
}
# Money in the fixtures: none of it may be in anything sent to the model.
MONEY = ["8,450.00", "3,100.50", "12,000.00", "5,000.00", "11,550.50", "22,960.50", "8,343.80", "106.20", "590.00",
         "XXXXXXXX1234", "_XXXXXXX5678", "XXXXXXXX9012"]
LOANS = [{"loan_ref": ref, "loan_id": n, "account_no": f"00000000{n}", "client_id": n, "firstname": first,
          "lastname": last, "instalments": [{"due": due, "amount": amount}]}
         for n, (ref, first, last, due, amount) in enumerate((
             ("L1", "Ramesh", "Kumar", "2026-09-05", 8450.0), ("L2", "Anita", "Sharma", "2026-09-06", 3100.5),
             ("L3", "Sunil", "Verma", "2026-09-07", 12000.0), ("L4", "Meena", "Iyer", "2026-09-10", 5000.0)), 1)]


def text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def model_says(answer):
    """A Messages API response whose text is `answer` (a dict as JSON, or raw text)."""
    body = answer if isinstance(answer, str) else json.dumps(answer)
    return {"type": "message", "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": body}]}


class FakeSend:
    """Stands in for ai.client.sender(...): records each request body, answers from a list or a function."""

    def __init__(self, *answers, by=None):
        self.answers, self.by, self.bodies = list(answers), by, []

    def __call__(self, body):
        self.bodies.append(body)
        if self.by:
            return self.by(body)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return client.answer(model_says(answer))


class LayoutCheckTest(unittest.TestCase):
    def assertRefused(self, raw, name, *words):
        with self.assertRaises(layout.LayoutError) as caught:
            layout.check(raw, text(name))
        for word in words:
            self.assertIn(word, str(caught.exception))

    def test_a_good_map_reads_the_whole_file(self):
        cmap, lines, counts = layout.check(MAPS["sbi_style.csv"], text("sbi_style.csv"))
        self.assertEqual(counts, {"rows": 5, "credits": 4, "debits": 1, "empty": 0})
        self.assertEqual(cmap["headers"][2], "Description")

    def test_an_index_outside_the_header_is_refused(self):
        self.assertRefused({**MAPS["sbi_style.csv"], "credit": 7}, "sbi_style.csv", "credit", "not one of the file's 7 columns")
        self.assertRefused({**MAPS["sbi_style.csv"], "date": -2}, "sbi_style.csv", "date")
        self.assertRefused({**MAPS["sbi_style.csv"], "header_row": 99}, "sbi_style.csv", "not a row of column names")
        self.assertRefused({**MAPS["sbi_style.csv"], "header_row": 5}, "sbi_style.csv", "not a row of column names")  # blank

    def test_a_date_that_does_not_parse_is_refused_with_its_row(self):
        self.assertRefused({**MAPS["sbi_style.csv"], "date_format": "%d/%m/%Y"}, "sbi_style.csv", "row 8:", "date")
        self.assertRefused({**MAPS["sbi_style.csv"], "date_format": "%m/%d/%Y"}, "sbi_style.csv", "date format")

    def test_an_amount_that_does_not_parse_is_refused_with_its_row(self):
        self.assertRefused({**MAPS["sbi_style.csv"], "credit": 1}, "sbi_style.csv", "row 8:", "amount", "5 Sep 2026")
        # Amounts with Cr/Dr read as plain numbers do not parse either.
        self.assertRefused({**MAPS["axis_style.csv"], "style": "signed"}, "axis_style.csv", "row 5:", "8,450.00 Cr")

    def test_a_missing_narration_column_is_refused(self):
        self.assertRefused({**MAPS["sbi_style.csv"], "narration": -1}, "sbi_style.csv", "no narration")
        self.assertRefused({k: v for k, v in MAPS["sbi_style.csv"].items() if k != "narration"}, "sbi_style.csv", "no narration")

    def test_malformed_answers_are_refused(self):
        for raw in (None, [], "two_columns", {**MAPS["sbi_style.csv"], "credit": "5"},
                    {**MAPS["sbi_style.csv"], "credit": True}, {**MAPS["sbi_style.csv"], "style": "guess"},
                    {**MAPS["sbi_style.csv"], "narration": 5}):  # the credit column twice
            with self.assertRaises(layout.LayoutError, msg=repr(raw)):
                layout.check(raw, text("sbi_style.csv"))

    def test_a_dr_cr_column_and_a_signed_amount(self):
        base = "Date,Details,Amount,Type\n05-09-2026,UPI/507296189749/RAMESH KUMAR,\"8,450.00\",CR\n06-09-2026,FEE,10.00,DR\n"
        cmap = {"header_row": 0, "date": 0, "date_format": "%d-%m-%Y", "narration": 1, "reference": -1,
                "style": "type_column", "amount": 2, "type": 3, "credit": -1, "debit": -1}
        _, lines, counts = layout.check(cmap, base)
        self.assertEqual([(l["txn_id"], l["amount"]) for l in lines], [("507296189749", "8450.00")])
        self.assertEqual(counts["debits"], 1)
        with self.assertRaises(layout.LayoutError) as caught:
            layout.check(cmap, base.replace(",DR", ",??"))
        self.assertIn("credit or a debit", str(caught.exception))
        signed = base.replace('"8,450.00",CR', "8450.00,").replace("10.00,DR", "-10.00,")
        _, lines, _ = layout.check({**cmap, "style": "signed", "type": -1}, signed)
        self.assertEqual([l["amount"] for l in lines], ["8450.00"])


class LayoutAskTest(unittest.TestCase):
    def test_the_model_sees_no_digit_from_the_file(self):
        send = FakeSend(MAPS["icici_style.csv"])
        raw = layout.ask(layout.rows_of(text("icici_style.csv")), send)
        self.assertEqual(raw, MAPS["icici_style.csv"])
        sent = json.dumps(send.bodies[0])
        for money in MONEY + ["507296189749", "SBINR52026090600001", "XXXXXXXX1234"]:
            self.assertNotIn(money, sent)
        self.assertIn("RAMESH KUMAR", sent)  # narration words do go: the consent says so
        self.assertIn("Transaction Remarks", sent)
        self.assertEqual(send.bodies[0]["output_config"]["format"]["type"], "json_schema")

    def test_only_the_first_rows_are_sent(self):
        many = "Date,Narration,Credit\n" + "05-09-2026,UPI-A B,1\n" * 200
        send = FakeSend(MAPS["icici_style.csv"])
        layout.ask(layout.rows_of(many), send)
        prompt = send.bodies[0]["messages"][0]["content"]
        self.assertEqual(prompt.count("UPI-A B"), 19)  # the header and 19 rows: the first 20 rows, not beyond
        self.assertIn("row 19:", prompt)
        self.assertNotIn("row 20:", prompt)

    def test_a_confirmed_map_is_found_again_without_asking(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Path(folder) / "layouts.json"
            self.assertIsNone(layout.saved(text("axis_style.csv"), store))
            cmap, _, _ = layout.check(MAPS["axis_style.csv"], text("axis_style.csv"))
            layout.save(cmap, store)
            again = layout.saved(text("axis_style.csv"), store)
            self.assertEqual(layout.check(again, text("axis_style.csv"))[1], layout.check(cmap, text("axis_style.csv"))[1])
            self.assertIsNone(layout.saved(text("sbi_style.csv"), store))  # another header, another bank


class NarrationTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cache = Path(folder.name) / "ai_cache.json"

    def test_a_reference_not_in_the_text_is_discarded(self):
        line = "BY TRANSFER-UPI/CR/507296189751/MEENA IYER/UTIB/meena@okaxis/emi"
        fields = narration.valid(line, {"payer_name": "MEENA IYER", "channel": "UPI", "reference": "507296189752",
                                        "loan_account": "000000004"})
        self.assertEqual(fields, {"payer_name": "MEENA IYER", "channel": "UPI", "reference": "", "loan_account": ""})
        fields = narration.valid(line, {"payer_name": "Meena  Iyer", "channel": "CHEQUE", "reference": "507296189751",
                                        "loan_account": ""})
        self.assertEqual(fields, {"payer_name": "Meena Iyer", "channel": "", "reference": "507296189751", "loan_account": ""})

    def test_a_name_the_model_made_up_is_dropped(self):
        fields = narration.valid("NEFT/HDFCN1/LOAN AC 000000002/ANITA S", {"payer_name": "ANITA SHARMA", "channel": "NEFT",
                                                                            "reference": "HDFCN1", "loan_account": "000000002"})
        self.assertEqual(fields["payer_name"], "")
        self.assertEqual(fields["loan_account"], "000000002")
        self.assertIsNone(narration.valid("X", {"payer_name": 7}))
        self.assertIsNone(narration.valid("X", "MEENA"))

    def test_malformed_answers_leave_the_narration_unread(self):
        lines = ["BY TRANSFER-UPI/CR/1/MEENA IYER", "BY TRANSFER-UPI/CR/2/RAMESH KUMAR"]
        for answer in ("not json at all", {"rows": []}, {"items": "MEENA"},
                       {"items": [{"i": 5, "payer_name": "MEENA IYER"}, {"i": "0"}, "x"]}):
            send = FakeSend(answer)
            out, stats = narration.extract(lines, send, self.cache)
            self.assertEqual(out, {}, answer)
            self.assertEqual(stats["unread"], 2)
        out, stats = narration.extract(lines, FakeSend(client.AIError("could not reach Anthropic's API")), self.cache)
        self.assertEqual((out, stats["errors"]), ({}, ["could not reach Anthropic's API"]))

    def test_the_cache_answers_a_rerun_without_a_call(self):
        lines = ["BY TRANSFER-UPI/CR/1/MEENA IYER/x", "BY TRANSFER-UPI/CR/2/RAMESH KUMAR/y"]
        answer = {"items": [{"i": 0, "payer_name": "MEENA IYER", "channel": "UPI", "reference": "", "loan_account": ""},
                            {"i": 1, "payer_name": "RAMESH KUMAR", "channel": "UPI", "reference": "", "loan_account": ""}]}
        send = FakeSend(answer)
        first, _ = narration.extract(lines + lines, send, self.cache)
        again, stats = narration.extract(lines, send, self.cache)
        self.assertEqual(len(send.bodies), 1)
        self.assertEqual(first, again)
        self.assertEqual((stats["from_cache"], stats["sent"]), (2, 0))

    def test_narrations_go_in_batches_of_forty_with_nothing_but_their_text(self):
        lines = [f"BY TRANSFER-UPI/CR/{i}/PERSON {chr(65 + i % 26)}" for i in range(85)]
        send = FakeSend(by=lambda body: {"items": []})
        narration.extract(lines, send, self.cache)
        self.assertEqual(len(send.bodies), 3)
        sent = json.dumps(send.bodies)
        for money in ("8450.00", "8,450.00", "Balance"):
            self.assertNotIn(money, sent)

    def test_the_cache_is_checked_again_when_read(self):
        line = "BY TRANSFER-UPI/CR/1/MEENA IYER"
        self.cache.write_text(json.dumps({narration.key(line): {"payer_name": "RAJESH KUMAR", "channel": "UPI",
                                                                 "reference": "", "loan_account": ""}}))
        out, _ = narration.extract([line], FakeSend(), self.cache)
        self.assertEqual(out[line]["payer_name"], "")


class MatcherWithReaderTest(unittest.TestCase):
    def line(self, text, amount="8450.00", day="2026-09-05"):
        return {"txn_id": "1", "date": day, "amount": amount, "narration": text}

    def test_a_name_read_by_the_model_fills_a_blank(self):
        text = "BY TRANSFER-UPI/CR/507296189751/RAMESH KUMAR/SBIN"
        self.assertEqual(payer_name(text, "sbi"), "")
        d, = decide([self.line(text)], LOANS, "sbi", {text: {"payer_name": "RAMESH KUMAR"}})
        self.assertEqual((d["outcome"], d["loan_ref"]), ("post", "L1"))
        self.assertIn("read by the AI reader", d["reason"])
        d, = decide([self.line(text)], LOANS, "sbi")
        self.assertEqual(d["outcome"], "queue")

    def test_the_model_never_overrides_a_rule(self):
        text = "UPI/507296189749/emi/RAMESH KUMAR/SUNIL VERMA"  # icici: the rules read RAMESH KUMAR
        d, = decide([self.line(text)], LOANS, "icici", {text: {"payer_name": "SUNIL VERMA", "loan_account": "000000003"}})
        self.assertEqual((d["outcome"], d["loan_ref"]), ("post", "L1"))
        self.assertNotIn("AI", d["reason"])
        text = "NACH-CR-SCPL000000001-SEP EMI 000000003"  # the SCPL rule wins over a model's loan account
        d, = decide([self.line(text)], LOANS, "other", {text: {"payer_name": "", "loan_account": "000000003"}})
        self.assertEqual(d["loan_ref"], "L1")

    def test_a_name_or_account_not_in_the_narration_is_ignored(self):
        text = "BY TRANSFER-UPI/CR/507296189751/R K/SBIN"
        d, = decide([self.line(text)], LOANS, "sbi", {text: {"payer_name": "RAMESH KUMAR", "loan_account": "000000001"}})
        self.assertEqual((d["outcome"], d["reason"]), ("queue", "no payer name or loan account in the narration"))

    def test_an_unknown_loan_account_falls_back_to_the_name(self):
        text = "NEFT/X/LOAN 999999999/RAMESH KUMAR"
        d, = decide([self.line(text)], LOANS, "other", {text: {"payer_name": "RAMESH KUMAR", "loan_account": "999999999"}})
        self.assertEqual(d["loan_ref"], "L1")

    def test_the_model_still_cannot_post_an_amount_that_is_not_due(self):
        text = "BY TRANSFER-UPI/CR/1/RAMESH KUMAR"
        d, = decide([self.line(text, amount="8000.00")], LOANS, "sbi", {text: {"payer_name": "RAMESH KUMAR"}})
        self.assertEqual(d["outcome"], "queue")
        self.assertIn("amount differs", d["reason"])

    def test_without_fields_nothing_changes(self):
        lines = [self.line(f"UPI/{n}/RAMESH KUMAR/x", day=f"2026-09-0{n}") for n in range(1, 9)]
        self.assertEqual(decide(lines, LOANS), decide(lines, LOANS, "synthetic", None))
        self.assertEqual(decide(lines, LOANS), decide(lines, LOANS, "synthetic", {}))


class FixtureEndToEndTest(unittest.TestCase):
    """Each made-up bank file through the reader with a fake model: layout, import, narrations, matcher."""

    def run_fixture(self, name, bank, answers):
        with tempfile.TemporaryDirectory() as folder:
            raw = layout.ask(layout.rows_of(text(name)), FakeSend(MAPS[name]))
            cmap, lines, _ = layout.check(raw, text(name))
            self.assertEqual([(l["txn_id"], l["date"], l["amount"]) for l in lines], EXPECTED[name])
            # The import path the page and start.ps1 use reads the same, from the confirmed map.
            again, _ = convert(io.StringIO(text(name), newline=""), cmap=cmap)
            self.assertEqual(again, lines)
            todo = [l["narration"] for l in lines if not payer_name(l["narration"], bank) and "SCPL" not in l["narration"]]
            send = FakeSend(by=lambda body: answers(body))
            extracted, _ = narration.extract(todo, send, Path(folder) / "ai_cache.json")
            sent = json.dumps(send.bodies)
            for money in MONEY:
                self.assertNotIn(money, sent)
            return lines, decide(lines, LOANS, bank, extracted), len(send.bodies)

    @staticmethod
    def reads(*fields):
        """A fake model that answers each narration in the batch with the given fields, in order."""
        def answer(body):
            listing = body["messages"][0]["content"].split("\n\n", 1)[1].splitlines()
            return {"items": [{"i": i, **{"payer_name": "", "channel": "", "reference": "", "loan_account": ""},
                               **next((f for key, f in fields if key in row), {})} for i, row in enumerate(listing)]}
        return answer

    def test_icici_rules_read_every_name_and_the_model_is_not_asked(self):
        _, decisions, calls = self.run_fixture("icici_style.csv", "icici", self.reads())
        self.assertEqual(calls, 0)
        self.assertEqual([(d["outcome"], d["loan_ref"]) for d in decisions], [("post", "L1"), ("post", "L2"), ("post", "L3")])

    def test_sbi_upi_transfers_are_read_by_the_model(self):
        _, decisions, calls = self.run_fixture("sbi_style.csv", "sbi", self.reads(
            ("RAMESH", {"payer_name": "RAMESH KUMAR", "channel": "UPI", "reference": "507296189750"}),
            ("MEENA", {"payer_name": "MEENA IYER", "channel": "UPI", "reference": "999999999999"})))
        self.assertEqual(calls, 1)
        self.assertEqual([(d["outcome"], d["loan_ref"]) for d in decisions],
                         [("post", "L1"), ("post", "L2"), ("post", "L3"), ("post", "L4")])
        self.assertIn("AI reader", decisions[0]["reason"])
        self.assertNotIn("AI reader", decisions[1]["reason"])  # NEFT-CR- is in FORMATS: the rule read it

    def test_axis_names_and_a_loan_account_come_from_the_model(self):
        _, decisions, _ = self.run_fixture("axis_style.csv", "other", self.reads(
            ("RAMESH", {"payer_name": "RAMESH KUMAR"}), ("SUNIL", {"payer_name": "SUNIL VERMA"}),
            ("ANITA", {"payer_name": "ANITA S", "loan_account": "000000002"})))
        self.assertEqual([(d["txn_id"], d["outcome"], d["loan_ref"]) for d in decisions],
                         [("507296189760", "post", "L1"), ("612345678903", "post", "L3"),
                          ("HDFCN52026090899999", "post", "L2")])

    def test_axis_with_a_model_that_answers_nothing_queues_everything(self):
        _, decisions, _ = self.run_fixture("axis_style.csv", "other", lambda body: {"items": []})
        self.assertEqual({d["outcome"] for d in decisions}, {"queue"})


class Opener:
    """Stands in for urllib's urlopen: records requests, answers from a list (a dict, or an HTTPError)."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


def http_error(code, message="busy"):
    return urllib.error.HTTPError(client.API_URL, code, message, {}, io.BytesIO(json.dumps(
        {"type": "error", "error": {"type": "x", "message": message}}).encode()))


class ClientTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        self.env = self.data / ".env"
        self.env.write_text(f"MIFOSX_PASSWORD=pw\nANTHROPIC_API_KEY={KEY}\n")
        sleep = mock.patch.object(client.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_no_call_without_a_key_or_without_consent(self):
        opener = Opener()
        with self.assertRaises(client.AIError) as caught:
            client.sender(self.env, self.data, opener)
        self.assertIn("consent", str(caught.exception))
        self.env.write_text("MIFOSX_PASSWORD=pw\n")
        client.record_consent(self.data)
        with self.assertRaises(client.AIError) as caught:
            client.sender(self.env, self.data, opener)
        self.assertIn("no Claude API key", str(caught.exception))
        self.assertEqual(opener.requests, [])

    def test_the_request(self):
        client.record_consent(self.data)
        opener = Opener(model_says({"items": []}))
        send = client.sender(self.env, self.data, opener)
        self.assertEqual(send(client.request("sys", "hello", {"type": "object"})), {"items": []})
        req, = opener.requests
        body = json.loads(req.data)
        self.assertEqual(req.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual((body["model"], body["output_config"]["effort"]), ("claude-sonnet-5-5", "low"))  # the default quality reads narrations with Sonnet
        self.assertEqual(body["fallbacks"], "default")  # Sonnet has the refusal fallback; Haiku does not (test_ai_quality)
        self.assertEqual(req.get_header("X-api-key"), KEY)
        self.assertEqual(req.get_header("Anthropic-version"), "2026-10-21")
        self.assertNotIn(KEY, req.data.decode())  # the key is a header, never in the body
        self.assertNotIn("pw", json.dumps(body["messages"]))


    def test_a_stronger_model_gets_the_refusal_fallback(self):
        client.record_consent(self.data)
        self.env.write_text(f"MIFOSX_PASSWORD=pw\nANTHROPIC_API_KEY={KEY}\nAI_MODEL=claude-opus-5-5\n")
        opener = Opener(model_says({"items": []}))
        client.sender(self.env, self.data, opener)(client.request("s", "p", {"type": "object"}))
        body = json.loads(opener.requests[0].data)
        self.assertEqual((body["model"], body["fallbacks"]), ("claude-opus-5-5", "default"))
    def test_another_model_from_env(self):
        client.record_consent(self.data)
        self.env.write_text(f"ANTHROPIC_API_KEY={KEY}\nAI_MODEL=claude-haiku-5-5\n")
        opener = Opener(model_says({"items": []}))
        client.sender(self.env, self.data, opener)(client.request("s", "p", {}))
        body = json.loads(opener.requests[0].data)
        self.assertEqual(body["model"], "claude-haiku-5-5")
        self.assertNotIn("fallbacks", body)

    def test_one_retry_on_busy_then_a_clear_error(self):
        client.record_consent(self.data)
        opener = Opener(http_error(529), model_says({"ok": 1}))
        self.assertEqual(client.sender(self.env, self.data, opener)(client.request("s", "p", {})), {"ok": 1})
        opener = Opener(http_error(429), http_error(429))
        with self.assertRaises(client.AIError) as caught:
            client.sender(self.env, self.data, opener)(client.request("s", "p", {}))
        self.assertEqual(len(opener.requests), 2)
        self.assertIn("429", str(caught.exception))
        opener = Opener(http_error(401, "invalid x-api-key"))
        with self.assertRaises(client.AIError) as caught:
            client.sender(self.env, self.data, opener)(client.request("s", "p", {}))
        self.assertEqual(len(opener.requests), 1)  # not retried
        self.assertIn("Connect tab", str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))

    def test_answers_that_are_not_usable(self):
        for response, word in (({"stop_reason": "refusal", "content": []}, "declined"),
                               ({"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{\"items\": ["}]}, "cut off"),
                               (model_says("here you go: {}"), "not JSON"), (model_says("[1, 2]"), "not a JSON object")):
            with self.assertRaises(client.AIError) as caught:
                client.answer(response)
            self.assertIn(word, str(caught.exception))

    def test_read_env_skips_comments(self):
        self.env.write_text("# ANTHROPIC_API_KEY=old\nBANK = sbi\nnot a setting\nMIFOS_URL=\"http://x\"\n")
        self.assertEqual(client.read_env(self.env), {"BANK": "sbi", "MIFOS_URL": "http://x"})


if __name__ == "__main__":
    unittest.main()
