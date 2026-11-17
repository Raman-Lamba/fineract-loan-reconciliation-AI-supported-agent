"""The page's Connect, Statement and Run tabs, over HTTP, on the demo's data and a temporary .env.

A fake Claude (an urlopen stand-in) answers the AI reader; nothing reaches Anthropic or a real Fineract.
"""
import csv
import http.client
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import posting.run as files  # noqa: E402
from review import build as queue_file  # noqa: E402
from statement.generate import generate, hdfc_export  # noqa: E402
from statement.import_bank import convert  # noqa: E402
from tests.test_ai import KEY, MAPS, Opener, model_says  # noqa: E402
from tests.test_ui import PASSWORD, Fake  # noqa: E402
from ui import serve  # noqa: E402

FIXTURES = Path(__file__).with_name("fixtures")
ENV = ("# my notes about this machine\r\nMIFOSX_BASE_URL=http://127.0.0.1:4200/fineract-provider/api/v1\r\n"
       "MIFOSX_TENANT_ID=default\r\nMIFOSX_USERNAME=mifos\r\nMIFOSX_PASSWORD=" + PASSWORD + "\r\n"
       "MIFOSX_READ_TIMEOUT_SECONDS=30\r\n")


class Page(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        self.env = self.data / "test.env"
        self.env.write_text(ENV, newline="")
        self.out, self.logged = io.StringIO(), []
        log = lambda handler, fmt, *args: self.logged.append(fmt % args)  # noqa: E731
        for target, name, value in ((files, "DATA", self.data), (queue_file, "QUEUE", self.data / "queue.csv"),
                                    (sys, "stdout", self.out), (serve.Handler, "log_message", log)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.fc = Fake(serve.demo(self.data), {})
        self.opener = Opener()
        self.server = serve.make_server(self.fc, 0, env=self.env, opener=self.opener)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, token=True, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        headers = {"X-Token": self.server.token} if token else {}
        conn.request(method, path, raw if raw is not None else json.dumps(body) if body is not None else None, headers)
        response = conn.getresponse()
        text = response.read().decode()
        conn.close()
        for secret in (PASSWORD, KEY):  # never in an answer
            self.assertNotIn(secret, text)
        return response.status, text

    def post(self, path, body=None):
        status, text = self.request("POST", path, body or {})
        self.assertEqual(status, 200, text)
        return json.loads(text)

    def get(self, path):
        status, text = self.request("GET", path)
        self.assertEqual(status, 200, text)
        return json.loads(text)

    def tearDown(self):
        for secret in (PASSWORD, KEY):  # never in the server's log or its printed output
            self.assertNotIn(secret, "\n".join(self.logged) + self.out.getvalue())


class ConnectTest(Page):
    def test_saving_merges_into_env_and_keeps_every_other_line(self):
        result = self.post("/api/settings", {"values": {
            "MIFOSX_BASE_URL": "https://fineract.example/fineract-provider/api/v1", "MIFOSX_TENANT_ID": "lender1",
            "MIFOSX_USERNAME": "agent", "MIFOSX_PASSWORD": "", "MIFOS_URL": "", "BANK": "sbi",
            "ANTHROPIC_API_KEY": "", "AI_MODEL": ""}})
        self.assertEqual(result["done"], "Saved.")
        text = self.env.read_text()
        self.assertIn("# my notes about this machine", text)
        self.assertIn("MIFOSX_READ_TIMEOUT_SECONDS=30", text)
        self.assertIn(f"MIFOSX_PASSWORD={PASSWORD}", text)  # an empty password box keeps the saved one
        self.assertIn("MIFOSX_TENANT_ID=lender1\r\n", self.env.read_bytes().decode())
        self.assertIn("BANK=sbi", text)
        self.assertEqual(text.count("MIFOSX_BASE_URL="), 1)
        self.assertNotIn("ANTHROPIC_API_KEY", text)
        # The running client follows at once: no restart.
        self.assertEqual((self.fc.base_url, self.fc.tenant_id, self.fc.username),
                         ("https://fineract.example/fineract-provider/api/v1", "lender1", "agent"))
        self.assertEqual(result["values"]["MIFOSX_USERNAME"], "agent")
        self.assertEqual(result["saved"], {"MIFOSX_PASSWORD": True, "ANTHROPIC_API_KEY": False})
        self.assertNotIn("MIFOSX_PASSWORD", result["values"])

    def test_a_key_is_write_only(self):
        result = self.post("/api/settings", {"values": {"ANTHROPIC_API_KEY": KEY}})
        self.assertTrue(result["saved"]["ANTHROPIC_API_KEY"])
        self.assertIn(f"ANTHROPIC_API_KEY={KEY}", self.env.read_text())
        for path in ("/api/settings", "/api/run", "/api/state"):
            self.get(path)  # request() checks the key is in none of them
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.post("/api/settings", {"forget": ["ANTHROPIC_API_KEY"]})
        self.assertNotIn("ANTHROPIC_API_KEY", self.env.read_text())
        self.assertIn(f"MIFOSX_PASSWORD={PASSWORD}", self.env.read_text())  # forget touches only the key

    def test_bad_values_are_refused_and_nothing_is_written(self):
        before = self.env.read_bytes()
        for values in ({"MIFOSX_USERNAME": "agent\nMIFOSX_PASSWORD=x"}, {"MIFOSX_BASE_URL": "fineract.example"},
                       {"BANK": "bank of nowhere"}, {"SOMETHING_ELSE": "1"}, {"ANTHROPIC_API_KEY": "sk ant"}):
            self.assertIn("problem", self.post("/api/settings", {"values": values}), values)
        self.assertEqual(self.env.read_bytes(), before)

    def test_saving_needs_the_token(self):
        status, _ = self.request("POST", "/api/settings", {"values": {"MIFOSX_USERNAME": "x"}}, token=False)
        self.assertEqual(status, 403)
        self.assertEqual(self.env.read_text(newline=""), ENV)

    def test_test_connection_uses_the_saved_settings(self):
        self.post("/api/settings", {"values": {"MIFOSX_USERNAME": "agent"}})
        self.get("/api/check")
        self.assertEqual(self.fc.posts[0][1]["username"], "agent")


class StatementTest(Page):
    def upload(self, name=None, text=None):
        text = text if text is not None else (FIXTURES / name).read_text(encoding="utf-8")
        return self.post("/api/upload", {"name": name, "text": text})

    def use_ai(self):
        self.post("/api/settings", {"values": {"ANTHROPIC_API_KEY": KEY}})

    def test_an_hdfc_export_imports_through_the_hdfc_path_without_a_key(self):
        loans = [{"loan_ref": f"L{i}", "loan_id": i, "account_no": f"{i:09d}", "client_id": i, "firstname": f"F{i}",
                  "lastname": "Kumar", "instalments": [{"due": "2026-09-05", "amount": 1000.0 + i}]} for i in range(1, 30)]
        lines, _ = generate(loans)
        out = io.StringIO(newline="")
        csv.writer(out).writerows(hdfc_export(lines))
        self.upload("hdfc.csv", out.getvalue())
        read = self.post("/api/read", {"ai": True})  # asked for, but HDFC is found first: no call
        self.assertEqual(read["source"], "hdfc")
        self.assertIn("counts", read)
        done = self.post("/api/import", {"map": read["map"], "bank": "hdfc"})
        self.assertEqual(done["done"]["credits"], len(lines))
        expected, _ = convert(io.StringIO(out.getvalue(), newline=""), "hdfc")
        with (self.data / "bank_statement.csv").open(newline="", encoding="utf-8") as f:
            self.assertEqual(list(csv.DictReader(f)), expected)
        self.assertFalse((self.data / "layouts.json").exists())
        self.assertIn("BANK=hdfc", self.env.read_text())
        self.assertEqual(self.opener.requests, [])

    def test_without_a_key_another_layout_is_chosen_by_hand(self):
        self.upload("sbi_style.csv")
        read = self.post("/api/read", {"ai": False})
        self.assertIsNone(read["map"])
        self.assertIn("Choose the columns", read["note"])
        self.assertIn("Claude API key", read["note"])
        preview = self.post("/api/preview", {"map": MAPS["sbi_style.csv"]})
        self.assertEqual(preview["counts"]["credits"], 4)
        done = self.post("/api/import", {"map": MAPS["sbi_style.csv"], "bank": "sbi"})
        self.assertEqual(done["done"]["credits"], 4)
        self.assertEqual(self.post("/api/read", {"ai": False})["source"], "saved")  # remembered for next time
        self.assertEqual(self.opener.requests, [])

    def test_no_ai_call_before_consent_then_one_call_then_the_cache(self):
        self.use_ai()
        self.upload("axis_style.csv")
        read = self.post("/api/read", {"ai": True})
        self.assertIn("consent", read["note"])
        self.assertEqual(self.opener.requests, [])
        self.assertIn("problem", self.post("/api/consent", {"agree": "yes"}))
        self.assertTrue(self.post("/api/consent", {"agree": True})["ai"]["consent"])
        self.assertTrue(json.loads((self.data / "ai_consent.json").read_text())["text"].startswith("I understand"))

        self.opener.answers.append(model_says(MAPS["axis_style.csv"]))
        read = self.post("/api/read", {"ai": True})
        self.assertEqual((read["source"], read["counts"]["credits"]), ("ai", 3))
        sent = self.opener.requests[0].data.decode()
        for money in ("8,450.00", "12,000.00", "XXXXXXXX9012", "507296189760"):
            self.assertNotIn(money, sent)
        self.post("/api/import", {"map": read["map"], "bank": "other"})
        read = self.post("/api/read", {"ai": True})
        self.assertEqual(read["source"], "saved")
        self.assertEqual(len(self.opener.requests), 1)  # the same layout is never asked about twice

    def test_a_map_from_the_model_that_does_not_fit_is_not_used(self):
        self.use_ai()
        self.post("/api/consent", {"agree": True})
        self.upload("sbi_style.csv")
        for bad in ({**MAPS["sbi_style.csv"], "credit": 9}, "here is the map you asked for"):
            self.opener.answers.append(model_says(bad))
            read = self.post("/api/read", {"ai": True})
            self.assertIsNone(read["map"])
            self.assertIn("not", read["note"])

    def test_a_refused_row_is_shown_with_its_number_and_nothing_is_imported(self):
        text = (FIXTURES / "sbi_style.csv").read_text().replace('"12,000.00"', '"12,000.00 only"')
        self.upload("sbi.csv", text)
        result = self.post("/api/import", {"map": MAPS["sbi_style.csv"], "bank": "sbi"})
        self.assertIn("row 10:", result["problem"])
        self.assertIn("Nothing was imported", result["problem"])
        self.assertFalse((self.data / "layouts.json").exists())
        self.assertEqual(len(files.read("bank_statement.csv")), 4)  # still the demo's statement

    def test_a_credit_with_no_reference_is_imported_and_reported_not_refused(self):
        text = (FIXTURES / "sbi_style.csv").read_text().replace("IMPS-612345678902-SUNIL VERMA-EMI", "CASH DEPOSIT")
        self.upload("sbi.csv", text)
        self.assertEqual(self.post("/api/preview", {"map": MAPS["sbi_style.csv"]})["counts"]["no_reference"], [10])
        done = self.post("/api/import", {"map": MAPS["sbi_style.csv"], "bank": "sbi"})
        self.assertEqual((done["done"]["credits"], done["done"]["no_reference"]), (4, [10]))
        self.assertTrue(files.read("bank_statement.csv")[2]["txn_id"].startswith("NOREF-"))

    def test_the_upload_has_a_size_limit(self):
        with mock.patch.object(serve, "MAX_UPLOAD", 2000):
            status, text = self.request("POST", "/api/upload", raw=json.dumps({"text": "x," * 2000}))
        self.assertEqual(status, 413)
        self.assertIn("too big", text)
        self.assertFalse((self.data / "bank_export.csv").exists())
        self.assertIn("not a CSV", self.upload("book.xlsx", "PK\x03\x04binary")["problem"])
        self.assertEqual(self.request("POST", "/api/decide", raw="x" * 70000)[0], 413)  # other requests: 64 KB

    def test_an_unknown_bank_is_refused(self):
        self.upload("sbi_style.csv")
        self.assertIn("Choose which bank", self.post("/api/import", {"map": MAPS["sbi_style.csv"], "bank": "x"})["problem"])


class RunTest(Page):
    def match(self, **body):
        return self.post("/api/match", body)

    def test_match_shows_what_would_happen_and_posts_nothing(self):
        result = self.match()
        self.assertEqual(result["match"]["counts"], {"post": 1, "queue": 3, "ignore": 0})
        self.assertEqual(result["match"]["pending"], {"count": 1, "total": "5200.00"})
        reasons = {r["reason"]: r["lines"] for r in result["match"]["reasons"]}
        self.assertEqual(reasons["'…' and the amount fit one loan; instalment due (date)"], 1)
        self.assertTrue(all("ANITA" not in reason for reason in reasons))  # names are not in the table
        self.assertEqual(self.fc.posts, [])
        self.assertTrue((self.data / "decisions.csv").exists())
        self.assertEqual(self.get("/api/run")["match"]["pending"]["count"], 1)

    def test_posting_needs_the_confirmed_numbers(self):
        self.match()
        for body in ({}, {"count": 1}, {"count": 2, "total": "5200.00"}, {"count": 1, "total": "5200"}):
            result = self.post("/api/post", body)
            self.assertIn("problem", result, body)
        self.assertEqual(self.fc.posts, [])
        self.assertFalse((self.data / "ledger.csv").exists())

    def test_posting_twice_posts_once(self):
        self.match()
        result = self.post("/api/post", {"count": 1, "total": "5200.00"})
        self.assertEqual(result["posted"], 1)
        self.assertIn("Checked the ledger against Fineract: 1 rows, 0 problems", " ".join(result["log"]))
        ledger, = files.read("ledger.csv")
        self.assertEqual((ledger["txn_id"], ledger["loan_ref"], ledger["partner_share"]), ("700000000004", "L9003", "4160.00"))
        again = self.post("/api/post", {"count": 1, "total": "5200.00"})
        self.assertEqual(again["posted"], 0)
        self.assertEqual(len([p for p in self.fc.posts if "repayment" in p[0]]), 1)
        self.assertEqual(self.match()["match"]["pending"]["count"], 0)

    def test_a_file_chosen_but_not_imported_does_not_change_how_the_statement_is_read(self):
        self.post("/api/upload", {"name": "sbi.csv", "text": (FIXTURES / "sbi_style.csv").read_text()})
        self.assertEqual(self.get("/api/run")["bank"], "synthetic")
        self.assertEqual(self.match()["match"]["counts"]["post"], 1)
        self.post("/api/import", {"map": MAPS["sbi_style.csv"], "bank": "sbi"})
        self.assertEqual(self.get("/api/run")["bank"], "sbi")

    def test_post_before_match_is_refused(self):
        self.assertIn("Press Match first", self.post("/api/post", {"count": 1, "total": "5200.00"})["problem"])

    def test_missing_loans_or_statement_say_what_to_do(self):
        (self.data / "schedules.json").unlink()
        self.assertIn("Read the loans", self.match()["problem"])
        self.assertEqual(self.post("/api/loans")["loans"], 3)  # read from the (fake) Fineract
        (self.data / "bank_statement.csv").write_text("txn_id,date,amount,narration\n")
        self.assertIn("Statement tab", self.match()["problem"])

    def test_the_ai_reader_fills_names_the_rules_cannot_read(self):
        (self.data / "bank_statement.csv").write_text(
            "txn_id,date,amount,narration\n900000000001,2026-10-10,5200.00,BY TRANSFER-UPI/CR/900000000001/ANITA SHARMA/x\n")
        self.post("/api/settings", {"values": {"ANTHROPIC_API_KEY": KEY}})
        result = self.match(bank="other", ai=True)  # no consent yet: the rules alone decide
        self.assertIn("consent", " ".join(result["log"]))
        self.assertEqual(result["match"]["counts"]["queue"], 1)
        self.post("/api/consent", {"agree": True})
        self.opener.answers.append(model_says({"items": [{"i": 0, "payer_name": "ANITA SHARMA", "channel": "UPI",
                                                          "reference": "900000000001", "loan_account": ""}]}))
        result = self.match(bank="other", ai=True)
        self.assertEqual(result["match"]["counts"]["post"], 1)
        self.assertEqual(json.loads(self.opener.requests[0].data)["messages"][0]["content"].count("ANITA"), 1)
        self.assertNotIn("5200", self.opener.requests[0].data.decode())
        self.match(bank="other", ai=True)
        self.assertEqual(len(self.opener.requests), 1)  # cached

    def test_without_ai_the_match_is_the_command_lines(self):
        from matcher.match import decide
        self.match(bank="synthetic")
        expected = decide(files.read("bank_statement.csv"), serve._schedules())
        self.assertEqual([(d["txn_id"], d["outcome"], d["loan_ref"], d["reason"]) for d in files.read("decisions.csv")],
                         [(d["txn_id"], d["outcome"], d["loan_ref"], d["reason"]) for d in expected])


if __name__ == "__main__":
    unittest.main()
