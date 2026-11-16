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
from review import apply as review_apply  # noqa: E402
from review import build as queue_file  # noqa: E402
from statement.fetch_schedules import fetch  # noqa: E402
from ui import serve  # noqa: E402

PASSWORD = "s3cret-Fineract-pw"
TXN = "700000000001"  # the demo line that fits two loans, L9001 and L9002


class Fake(serve.DemoFineract):
    """The demo's made-up Fineract, with chosen answers swapped in and every POST remembered."""
    password = PASSWORD

    def __init__(self, loans, answers):
        super().__init__(loans)
        self.answers, self.posts = answers, []

    def execute_get(self, endpoint, params=None):
        return self.answers[endpoint] if endpoint in self.answers else super().execute_get(endpoint, params)

    def execute_post(self, endpoint, body):
        self.posts.append((endpoint, body))
        return self.answers[endpoint] if endpoint in self.answers else super().execute_post(endpoint, body)


class DemoData(unittest.TestCase):
    """Each test gets the demo's data folder, so nothing touches data/."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        quiet = lambda *args: None  # noqa: E731  review.apply and the server print what they do
        for target, name, value in ((files, "DATA", self.data), (queue_file, "QUEUE", self.data / "queue.csv"),
                                    (sys, "stdout", io.StringIO()), (serve.Handler, "log_message", quiet)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.loans = serve.demo(self.data)

    def fake(self, **answers):
        return Fake(self.loans, answers)

    def rows(self, name):
        with (self.data / name).open(newline="") as f:
            return list(csv.DictReader(f))


class CheckTest(DemoData):
    def check(self, **answers):
        self.fc = self.fake(**answers)
        results = serve.check_connection(self.fc)
        for result in results:  # every failure tells the person what to do
            self.assertEqual(bool(result["fix"]), result["ok"] is False, result)
        return {result["check"]: result for result in results}

    def assertFails(self, results, name, *words):
        self.assertIs(results[name]["ok"], False, results[name])
        for word in words:
            self.assertIn(word, results[name]["fix"])

    def test_a_working_fineract_passes_every_check(self):
        results = self.check()
        self.assertEqual(list(results), ["Fineract answers at the URL", "The tenant exists", "The login works",
                                         "INR is an enabled currency", "Payment types UPI, NEFT, IMPS, NACH exist",
                                         "Loans exist", "A loan's schedule can be read"])
        self.assertEqual([r["ok"] for r in results.values()], [True] * 7)
        self.assertIn("3 of 3 have an externalId", results["Loans exist"]["detail"])

    def test_it_writes_nothing_to_fineract(self):
        self.check()
        self.assertEqual([endpoint for endpoint, _ in self.fc.posts], ["authentication"])

    def test_no_answer_at_the_url(self):
        results = self.check(authentication={"error": "Connection failed: refused"})
        self.assertFails(results, "Fineract answers at the URL", "MIFOSX_BASE_URL", "/fineract-provider/api/v1")
        self.assertEqual([r["ok"] for r in results.values()][1:], [None] * 6)  # not run

    def test_a_web_page_instead_of_the_api(self):
        results = self.check(authentication={"error": "HTTP 405: Failed to parse error response.", "status_code": 405})
        self.assertFails(results, "Fineract answers at the URL", "MIFOSX_BASE_URL")

    def test_unknown_tenant(self):
        results = self.check(authentication={"error": "Error 400: Bad Request", "status_code": 400})
        self.assertIs(results["Fineract answers at the URL"]["ok"], True)
        self.assertFails(results, "The tenant exists", "MIFOSX_TENANT_ID")
        self.assertIsNone(results["The login works"]["ok"])

    def test_wrong_password(self):
        results = self.check(authentication={"error": "Error 401: Unauthorized", "status_code": 401})
        self.assertIs(results["The tenant exists"]["ok"], True)
        self.assertFails(results, "The login works", "MIFOSX_USERNAME", "MIFOSX_PASSWORD")
        self.assertIsNone(results["INR is an enabled currency"]["ok"])

    def test_the_admin_account_is_pointed_out(self):
        results = self.check(authentication={"authenticated": True, "username": "mifos", "permissions": ["ALL_FUNCTIONS"]})
        self.assertIs(results["The login works"]["ok"], True)
        self.assertIn("dedicated user", results["The login works"]["detail"])

    def test_inr_not_enabled(self):
        results = self.check(currencies={"selectedCurrencyOptions": [{"code": "USD"}]})
        self.assertFails(results, "INR is an enabled currency", "Indian Rupee")
        self.assertIs(results["Loans exist"]["ok"], True)  # the later checks still run

    def test_a_missing_payment_type_is_named(self):
        results = self.check(paymenttypes=[{"id": 1, "name": "UPI"}, {"id": 2, "name": "NEFT"}, {"id": 3, "name": "IMPS"}])
        self.assertFails(results, "Payment types UPI, NEFT, IMPS, NACH exist", "NACH")
        self.assertIn("NACH", results["Payment types UPI, NEFT, IMPS, NACH exist"]["detail"])

    def test_a_check_the_user_may_not_read(self):
        results = self.check(paymenttypes={"error": "Error 403: Forbidden", "status_code": 403})
        self.assertFails(results, "Payment types UPI, NEFT, IMPS, NACH exist", "READ_PAYMENTTYPE")

    def test_no_loans(self):
        results = self.check(loans={"totalFilteredRecords": 0, "pageItems": []})
        self.assertFails(results, "Loans exist", "never creates")
        self.assertIsNone(results["A loan's schedule can be read"]["ok"])

    def test_loans_without_an_external_id_are_counted(self):
        page = {"totalFilteredRecords": 2, "pageItems": [{"id": 1, "accountNo": "000000001", "externalId": "L9001"},
                                                         {"id": 2, "accountNo": "000000002"}]}
        detail = self.check(loans=page)["Loans exist"]["detail"]
        self.assertIn("1 of 2 have an externalId", detail)
        self.assertIn("account number", detail)

    def test_a_schedule_that_cannot_be_read(self):
        results = self.check(**{"loans/1": {"error": "Error 403: Forbidden", "status_code": 403}})
        self.assertFails(results, "A loan's schedule can be read", "READ_LOAN")


class FetchTest(DemoData):
    def test_a_loan_without_an_external_id_is_known_by_its_account_number(self):
        page = {"totalFilteredRecords": 2, "pageItems": [{"id": 1, "accountNo": "000000001", "externalId": "L9001"},
                                                         {"id": 2, "accountNo": "000000002"}]}
        loans = fetch(self.fake(loans=page, **{"loans/2": {
            "id": 2, "accountNo": "000000002", "clientId": 2, "clientName": "Rajesh Kumar",
            "repaymentSchedule": {"periods": [{"dueDate": [2026, 8, 5]},
                                              {"period": 1, "dueDate": [2026, 9, 5], "totalDueForPeriod": 8450.0}]}}}), serve.ok)
        self.assertEqual([(loan["loan_ref"], loan["loan_id"]) for loan in loans], [("000000002", 2), ("L9001", 1)])
        self.assertEqual(loans[0]["instalments"], [{"due": "2026-09-05", "amount": 8450.0}])


class ServerTest(DemoData):
    def setUp(self):
        super().setUp()
        self.fc = self.fake()
        self.server = serve.make_server(self.fc, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, token=None, host=None):
        port = self.server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{port}"}
        if token:
            headers["X-Token"] = token
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = conn.getresponse()
        text = response.read().decode()
        conn.close()
        self.assertNotIn(PASSWORD, text)
        return response.status, text

    def get(self, path):
        status, text = self.request("GET", path)
        self.assertEqual(status, 200, text)
        return json.loads(text)

    def decide(self, **decision):
        body = {"txn_id": TXN, "decision": "post", "loan_ref": "L9002", "approver": "Asha", "note": "called him",
                **decision}
        status, text = self.request("POST", "/api/decide", body, token=self.server.token)
        return status, json.loads(text)

    def test_the_page_carries_the_token_and_the_settings_but_not_the_password(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.server.token, page)
        self.assertIn(self.fc.base_url, page)

    def test_a_host_that_is_not_this_machine_is_refused(self):
        for path in ("/", "/api/state"):
            self.assertEqual(self.request("GET", path, host="evil.example")[0], 403)
        self.assertEqual(self.request("GET", "/api/state", host="localhost:8000")[0], 200)
        status, _ = self.request("POST", "/api/decide", {"txn_id": TXN}, token=self.server.token, host="evil.example")
        self.assertEqual(status, 403)

    def test_a_change_needs_the_token_and_a_post(self):
        body = {"txn_id": TXN, "decision": "post", "loan_ref": "L9002", "approver": "Asha", "note": ""}
        before = (self.data / "queue.csv").read_bytes()
        self.assertEqual(self.request("POST", "/api/decide", body)[0], 403)
        self.assertEqual(self.request("POST", "/api/decide", body, token="guess")[0], 403)
        self.assertEqual(self.request("GET", "/api/decide")[0], 404)
        self.assertEqual((self.data / "queue.csv").read_bytes(), before)
        self.assertEqual(self.fc.posts, [])

    def test_the_queue_shows_each_line_with_its_candidates(self):
        state = self.get("/api/state")
        row = state["queue"][0]
        self.assertEqual((row["txn_id"], row["date"], row["amount"]), (TXN, "2026-09-05", "8450.00"))
        self.assertIn("2 loans fit", row["reason"])
        self.assertEqual(row["candidates"], [
            {"loan_ref": "L9001", "borrower": "Ramesh Kumar", "amount": "8450.00", "due": "2026-09-05",
             "loan_id": 1, "client_id": 1},
            {"loan_ref": "L9002", "borrower": "Rajesh Kumar", "amount": "8450.00", "due": "2026-09-05",
             "loan_id": 2, "client_id": 2}])
        self.assertEqual(state["queue"][2]["candidates"], [])
        self.assertEqual(state["summary"], {"payments": 0, "amount": "0.00", "partner_share": "0.00",
                                            "lender_share": "0.00", "queue": 3, "loans": 3})

    def test_approve_writes_the_decision_into_queue_csv_then_runs_review_apply(self):
        with mock.patch.object(review_apply, "run", return_value=([], {"posted": 1})) as run:
            status, result = self.decide()
        self.assertEqual((status, result), (200, {"done": "posted"}))
        run.assert_called_once_with(self.fc, serve.ok, only=TXN)
        row = queue_file.read_queue()[0]  # as a person would have left it in Excel
        self.assertEqual((row["txn_id"], row["decision"], row["loan_ref"], row["approver"], row["note"]),
                         (TXN, "post", "L9002", "Asha", "called him"))
        self.assertTrue((self.data / "queue.csv").read_text().splitlines()[1].startswith("UTR" + TXN))

    def test_approve_posts_once_through_the_posting_code(self):
        self.assertEqual(self.decide(), (200, {"done": "posted"}))
        (endpoint, body), = [p for p in self.fc.posts if p[0] != "authentication"]
        self.assertEqual(endpoint, "loans/2/transactions?command=repayment")
        self.assertEqual((body["externalId"], body["receiptNumber"], body["transactionAmount"], body["paymentTypeId"]),
                         (TXN, TXN, "8450.00", 1))
        ledger, = self.rows("ledger.csv")
        self.assertEqual((ledger["txn_id"], ledger["loan_ref"], ledger["amount"], ledger["partner_share"],
                          ledger["lender_share"]), (TXN, "L9002", "8450.00", "6760.00", "1690.00"))
        audit, = self.rows("audit.csv")
        self.assertEqual((audit["action"], audit["approver"], audit["reason"]), ("posted", "Asha", "manual: called him"))
        state = self.get("/api/state")
        self.assertNotIn(TXN, [row["txn_id"] for row in state["queue"]])
        self.assertEqual((state["summary"]["payments"], state["summary"]["partner_share"]), (1, "6760.00"))
        # A second click, or an old page: the line has left the queue and nothing is posted again.
        status, result = self.decide()
        self.assertIn("no longer in the queue", result["problem"])
        self.assertEqual(len(self.fc.posts), 1)

    def test_a_row_apply_refuses_comes_back_with_its_problem(self):
        status, result = self.decide(loan_ref="NOPE")
        self.assertEqual((status, result), (200, {"problem": "loan_ref 'NOPE' is not a known loan"}))
        self.assertEqual(self.fc.posts, [])
        row = self.get("/api/state")["queue"][0]
        self.assertEqual((row["problem"], row["note"]), ("loan_ref 'NOPE' is not a known loan", "called him"))
        self.assertEqual(self.decide(approver=" ")[1], {"problem": "approver is required"})

    def test_what_fineract_refuses_comes_back_too(self):
        self.fc.answers["loans/2/transactions?command=repayment"] = {"error": f"Validation Error: no ({PASSWORD})"}
        status, result = self.decide()
        self.assertIn("Validation Error: no", result["problem"])
        self.assertFalse((self.data / "ledger.csv").exists() and self.rows("ledger.csv"))

    def test_reject_is_logged_and_posts_nothing(self):
        self.assertEqual(self.decide(decision="reject", loan_ref="", note="not ours"), (200, {"done": "rejected"}))
        self.assertEqual(self.fc.posts, [])
        audit, = self.rows("audit.csv")
        self.assertEqual((audit["action"], audit["approver"], audit["reason"]), ("rejected", "Asha", "manual: not ours"))

    def test_fineract_being_down_is_reported_and_the_decision_stays_in_the_file(self):
        self.fc.answers["paymenttypes"] = {"error": f"Connection failed: {PASSWORD}@host refused"}
        status, result = self.decide()
        self.assertEqual(status, 200)
        self.assertIn("Read payment types failed", result["problem"])
        self.assertEqual(queue_file.read_queue()[0]["decision"], "post")

    def test_the_connection_check_over_http(self):
        self.fc.answers["currencies"] = {"error": f"Connection failed for {PASSWORD}"}
        results = self.get("/api/check")
        self.assertEqual(len(results), 7)
        self.assertIs(results[3]["ok"], False)

    def test_arrears_and_the_ledger_check_come_from_posting_verify(self):
        self.decide()
        self.assertEqual(self.get("/api/verify"), {"arrears": 0, "loans": 3, "checked": 1, "problems": []})
        self.fc.posted.clear()  # as if someone deleted the repayment in Fineract
        self.assertEqual(self.get("/api/verify")["problems"], [f"{TXN}: not in Fineract"])

    def test_the_partner_report_is_rebuilt_for_the_download(self):
        self.decide()
        status, text = self.request("GET", "/partner_report.csv")
        self.assertEqual(status, 200)
        rows = list(csv.DictReader(text.splitlines()))
        self.assertEqual((rows[0]["month"], rows[0]["loan_ref"], rows[0]["partner_share"]), ("2026-09", "L9002", "6760.00"))
        self.assertEqual((rows[-1]["month"], rows[-1]["amount"]), ("TOTAL", "8450.00"))


if __name__ == "__main__":
    unittest.main()
