"""The review page and the setup guide, for this machine only.

Run:  python -m ui.serve [--port 8000] [--demo]     (or .\\start.ps1 -Ui; design: docs/superpowers/specs/2026-11-16-ui-design.md)

The page is a second way to fill in data/queue.csv. A click writes the person's decision into that file,
as Excel would, and then runs review.apply on that one row, so the duplicate guard, the 80:20 split, the
ledger and the audit log are the same code as for the CSV workflow.

--demo serves a made-up queue from a temporary folder and a made-up Fineract. It posts nowhere.
"""
# shortcut: one local user and no login; the approver is whatever name is typed in the box. Several
# reviewers, or any use beyond this machine, need real accounts, because that name is the audit trail.
import argparse
import csv
import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import posting.run as files
from matcher.match import decide as match
from posting.verify import check, read_fineract, totals
from review import apply as review_apply
from review import build as queue_file
from review import report
from statement.fetch_schedules import list_loans

PAGE = Path(__file__).with_name("index.html")
CHANNELS = ("UPI", "NEFT", "IMPS", "NACH")  # the payment type names posting.post.channel looks for
WRITING = threading.Lock()  # one decision at a time: each reads and rewrites queue.csv
NOT_RUN = "Not run: fix the failed check above first."

CHECKS = [
    ("Fineract answers at the URL",
     "Set MIFOSX_BASE_URL in .env to your Fineract API address, ending in /fineract-provider/api/v1, and make "
     "sure Fineract is running (the sandbox: .\\start.ps1). For an https address with a self-signed "
     "certificate also add MIFOSX_SKIP_TLS_VERIFY=true. Then restart this page."),
    ("The tenant exists",
     "Set MIFOSX_TENANT_ID in .env to your tenant's identifier. It is 'default' on a standard install; it is "
     "the value the Mifos login page sends as Fineract-Platform-TenantId. Then restart this page."),
    ("The login works",
     "Check MIFOSX_USERNAME and MIFOSX_PASSWORD in .env: the same pair must log in on the Mifos login page. "
     "Then restart this page."),
    ("INR is an enabled currency",
     "In Mifos open Admin > Organization > Currency Configuration and add Indian Rupee (INR). If Fineract "
     "refused the read instead (error 403), give this user the READ_CURRENCY permission."),
    ("Payment types UPI, NEFT, IMPS, NACH exist",
     "In Mifos open Admin > Organization > Payment Types and add the missing ones, named exactly UPI, NEFT, "
     "IMPS and NACH. Without one, repayments on that channel are still posted, but with no payment type. If "
     "Fineract refused the read instead (error 403), give this user the READ_PAYMENTTYPE permission."),
    ("Loans exist",
     "The agent never creates loans: it reconciles the ones already in Fineract. Give this user the READ_LOAN "
     "permission and access to the office that holds the loans. On the sandbox, run .\\start.ps1 -Seed."),
    ("A loan's schedule can be read",
     "Give this user the READ_LOAN permission. A loan with no instalments cannot be matched: the matcher "
     "needs the repayment schedule Fineract shows on the loan's Repayment Schedule tab."),
]


def ok(response, what: str):
    """Like the connector's ok(): the response, or a stop with Fineract's own message.

    Here the request handler catches the stop, so the page shows the message and the server carries on.
    """
    if isinstance(response, dict) and "error" in response:
        sys.exit(f"{what} failed: {response['error']}")
    return response


def check_connection(fc) -> list[dict]:
    """The Setup tab's checks of the Fineract named in .env, each with a plain-English fix if it fails.

    Read-only: one login call, then GETs. Nothing is created or changed in Fineract.
    """
    results = []

    def add(passed, detail):
        name, fix = CHECKS[len(results)]
        results.append({"check": name, "ok": passed, "detail": str(detail), "fix": fix if passed is False else ""})

    def attempt(read):
        try:
            add(*read())
        except (SystemExit, Exception) as stop:  # Fineract's refusal, or an answer in a shape we do not know
            add(False, stop)

    auth = fc.execute_post("authentication", {"username": fc.username, "password": fc.password})
    error, status = auth.get("error"), auth.get("status_code")
    admin = (" This user has ALL_FUNCTIONS (an admin account): fine for the sandbox; for a real system use a "
             "dedicated user with only the permissions listed below.") if "ALL_FUNCTIONS" in auth.get("permissions", []) else ""
    # Seen on the sandbox on 16 Nov 2026: no status or 404/405 for a wrong address, 400 for an unknown
    # tenant, 401 for a wrong user or password.
    for passed, detail in ((not error or status in (400, 401, 403), fc.base_url),
                           (not error or status in (401, 403), f"Tenant '{fc.tenant_id}'."),
                           (not error and auth.get("authenticated") is True, f"Logged in as '{fc.username}'.{admin}")):
        add(bool(passed), detail if passed else error or "Fineract did not authenticate this user.")
        if not passed:
            break
    if not results[-1]["ok"]:
        while len(results) < len(CHECKS):
            add(None, NOT_RUN)
        return results

    def currency():
        enabled = [c["code"] for c in ok(fc.execute_get("currencies"), "Read currencies")["selectedCurrencyOptions"]]
        return "INR" in enabled, f"Enabled: {', '.join(enabled) or 'none'}."

    def payment_types():
        names = {p["name"] for p in ok(fc.execute_get("paymenttypes"), "Read payment types")}
        missing = [name for name in CHANNELS if name not in names]
        return not missing, f"Missing: {', '.join(missing)}." if missing else "All four found."

    loans = []

    def loans_exist():
        loans.extend(list_loans(fc, ok))
        with_ref = sum(bool(loan.get("externalId")) for loan in loans)
        rest = len(loans) - with_ref
        return bool(loans), (f"{len(loans)} loans; {with_ref} of {len(loans)} have an externalId (the loan_ref)."
                             + (f" The other {rest} will be known by their account number." if rest else ""))

    def schedule():
        loan = ok(fc.execute_get(f"loans/{loans[0]['id']}", {"associations": "repaymentSchedule"}), "Read a loan")
        periods = [p for p in loan.get("repaymentSchedule", {}).get("periods", []) if p.get("period")]
        return bool(periods), f"Loan {loan.get('accountNo')} ({loan.get('clientName')}): {len(periods)} instalments."

    for read in (currency, payment_types, loans_exist):
        attempt(read)
    if loans:
        attempt(schedule)
    else:
        add(None, NOT_RUN)
    return results


def _schedules() -> list[dict]:
    path = files.DATA / "schedules.json"
    return json.loads(path.read_text()) if path.exists() else []


def _problem_and_note(note: str) -> tuple[str, str]:
    """review.apply writes a refused row's problem in front of the person's note."""
    if not note.startswith(review_apply.PROBLEM):
        return "", note
    problem, _, mine = note.partition(" | ")
    return problem.removeprefix(review_apply.PROBLEM), mine


def candidates(text: str, loans: dict) -> list[dict]:
    """queue.csv's candidates cell ('L0189 Rajesh Kumar: 26314.16 due 2026-02-20; ...') as one dict per loan."""
    out = []
    for item in filter(None, text.split("; ")):
        ref, _, rest = item.partition(" ")
        borrower, _, instalment = rest.rpartition(": ")
        amount, _, due = instalment.partition(" due ")
        loan = loans.get(ref, {})  # the ids are for the link that opens the loan in Mifos
        out.append({"loan_ref": ref, "borrower": borrower, "amount": amount, "due": due,
                    "loan_id": loan.get("loan_id"), "client_id": loan.get("client_id")})
    return out


def state() -> dict:
    """What the Review tab shows: queue.csv as it is now, and the ledger's totals."""
    loans = {loan["loan_ref"]: loan for loan in _schedules()}
    queue = []
    for row in queue_file.read_queue():
        problem, note = _problem_and_note(row["note"])
        queue.append({**row, "candidates": candidates(row["candidates"], loans), "problem": problem, "note": note})
    summary = {key: value if key == "payments" else str(value) for key, value in totals(files.read("ledger.csv")).items()}
    return {"queue": queue, "summary": {**summary, "queue": len(queue), "loans": len(loans)}}


def decide(fc, d: dict) -> dict:
    """One person's decision on one line: written into queue.csv as if typed in Excel, then applied.

    Returns {"done": posted | recovered | rejected | applied earlier} or {"problem": why it was refused}.
    """
    txn = str(d.get("txn_id", ""))
    with WRITING:
        queue = queue_file.read_queue()
        row = next((r for r in queue if r["txn_id"] == txn), None)
        if row is None:
            return {"problem": "this line is no longer in the queue: it was decided already. Reload the page."}
        row.update({field: str(d.get(field, "")).strip() for field in queue_file.EDITS})
        try:
            queue_file.write_queue(queue)
            kept, counts = review_apply.run(fc, ok, only=txn)
        except PermissionError:
            return {"problem": "queue.csv is open in another program (Excel?). Close it there and try again."}
        except SystemExit as stop:
            return {"problem": f"Your decision is saved in queue.csv but was not applied. {stop}"}
    left = next((r for r in kept if r["txn_id"] == txn), None)
    if left:
        return {"problem": _problem_and_note(left["note"])[0] or "no decision was given: approve or reject"}
    return {"done": next(iter(counts), "applied")}


def verify(fc) -> dict:
    """posting.verify's numbers: loans in arrears, and the ledger checked against Fineract. Reads every loan."""
    ledger, loans = files.read("ledger.csv"), _schedules()
    in_fineract, arrears = read_fineract(loans, fc, ok)
    return {"arrears": arrears, "loans": len(loans), "checked": len(ledger), "problems": check(ledger, in_fineract)}


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, body, kind: str = "application/json", download: str = ""):
        if kind == "application/json":
            body = json.dumps(body)
            # Belt and braces: no code path puts the password in an answer, and an error text from
            # Fineract or the HTTP library cannot either.
            if getattr(self.server.fc, "password", None):
                body = body.replace(self.server.fc.password, "***")
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(body)

    def local(self) -> bool:
        """Only a browser that asked for this machine by name: stops another site reaching the page through DNS."""
        if self.headers.get("Host", "").rsplit(":", 1)[0] in ("127.0.0.1", "localhost"):
            return True
        self.reply(403, {"error": "this page answers only on 127.0.0.1 or localhost"})
        return False

    def answer(self, work):
        try:
            self.reply(200, work())
        except SystemExit as stop:  # ok() or review.report refused; the message is the answer
            self.reply(502, {"error": str(stop)})
        except Exception as error:  # noqa: BLE001  one bad request must not take the page down
            self.reply(500, {"error": f"{type(error).__name__}: {error}"})

    def do_GET(self):
        if not self.local():
            return
        fc = self.server.fc
        if self.path == "/":
            mifos = os.getenv("MIFOS_URL") or (fc.base_url or "").split("/fineract-provider")[0]
            config = {"token": self.server.token, "demo": self.server.demo, "mifos": mifos.rstrip("/"),
                      "settings": {"MIFOSX_BASE_URL": fc.base_url, "MIFOSX_TENANT_ID": fc.tenant_id,
                                   "MIFOSX_USERNAME": fc.username, "BANK": os.getenv("BANK", "hdfc")}}
            page = PAGE.read_text(encoding="utf-8").replace("__CONFIG__", json.dumps(config).replace("<", "\\u003c"))
            self.reply(200, page, "text/html")
        elif self.path == "/api/state":
            self.answer(state)
        elif self.path == "/api/check":
            self.answer(lambda: check_connection(fc))
        elif self.path == "/api/verify":
            self.answer(lambda: verify(fc))
        elif self.path == "/partner_report.csv":
            try:
                report.main()  # rebuilt from the ledger each time; it stops if its totals differ from the ledger's
                self.reply(200, (files.DATA / "partner_report.csv").read_bytes(), "text/csv", "partner_report.csv")
            except SystemExit as stop:
                self.reply(502, str(stop), "text/plain")
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        if not self.local():
            return
        if not secrets.compare_digest(self.headers.get("X-Token", ""), self.server.token):
            self.reply(403, {"error": "this page is out of date: reload it"})
        elif self.path == "/api/decide":
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536)))
            except ValueError:
                return self.reply(400, {"error": "the request was not JSON"})
            self.answer(lambda: decide(self.server.fc, body if isinstance(body, dict) else {}))
        else:
            self.reply(404, {"error": "not found"})


def make_server(fc, port: int, demo: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # this machine only, never 0.0.0.0
    # A new token each run, written into the page: a change must come from a page this run served.
    server.fc, server.token, server.demo = fc, secrets.token_urlsafe(), demo
    return server


class DemoFineract:
    """Answers like the connector's HTTP adapter, from memory. Nothing leaves this process."""
    base_url, tenant_id, username, password = "http://demo.invalid/fineract-provider/api/v1", "default", "demo", ""

    def __init__(self, loans: list[dict]):
        self.loans = {loan["loan_id"]: loan for loan in loans}
        self.posted = {}  # loan id -> the repayments posted to it

    def execute_get(self, endpoint: str, params=None):
        if endpoint == "paymenttypes":
            return [{"id": n, "name": name} for n, name in enumerate(CHANNELS, 1)]
        if endpoint == "currencies":
            return {"selectedCurrencyOptions": [{"code": "INR"}]}
        summaries = [{"id": loan["loan_id"], "externalId": loan["loan_ref"], "accountNo": loan["account_no"],
                      "clientId": loan["client_id"], "clientName": f"{loan['firstname']} {loan['lastname']}"}
                     for loan in self.loans.values()]
        if endpoint == "loans":
            return {"totalFilteredRecords": len(summaries), "pageItems": summaries}
        loan_id = int(endpoint.split("/")[1])
        transactions = self.posted.get(loan_id, [])
        if "/transactions/external-id/" in endpoint:
            found = [t for t in transactions if endpoint.endswith("/" + t["externalId"])]
            return found[0] if found else {"error": "not found", "status_code": 404}
        periods = [{"period": n, "dueDate": [int(part) for part in inst["due"].split("-")],
                    "totalDueForPeriod": inst["amount"], "complete": False}
                   for n, inst in enumerate(self.loans[loan_id]["instalments"], 1)]
        return {**summaries[list(self.loans).index(loan_id)], "status": {"active": True}, "inArrears": False,
                "repaymentSchedule": {"periods": periods}, "transactions": transactions}

    def execute_post(self, endpoint: str, body: dict):
        if endpoint == "authentication":
            return {"authenticated": True, "username": self.username, "permissions": []}
        transaction = {"id": 9000 + sum(map(len, self.posted.values())), "externalId": body["externalId"],
                       "amount": float(body["transactionAmount"]), "manuallyReversed": False}
        self.posted.setdefault(int(endpoint.split("/")[1]), []).append(transaction)
        return {"resourceId": transaction["id"]}


def demo(folder: Path) -> list[dict]:
    """Points the data files at `folder` and fills it with three loans and a three-line queue. Returns the loans.

    The queue is the real matcher's and review.build's work on three made-up bank lines.
    """
    files.DATA, queue_file.QUEUE = folder, folder / "queue.csv"
    loans = [{"loan_ref": f"L900{n}", "loan_id": n, "account_no": f"00000000{n}", "client_id": n, "firstname": first,
              "lastname": last, "instalments": [{"due": f"2026-{month}-{day}", "amount": amount} for month in ("09", "10")]}
             for n, (first, last, day, amount) in enumerate((("Ramesh", "Kumar", "05", 8450.0),
                                                             ("Rajesh", "Kumar", "05", 8450.0),
                                                             ("Anita", "Sharma", "10", 5200.0)), 1)]
    lines = [dict(zip(("txn_id", "date", "amount", "narration"), line)) for line in (
        ("700000000001", "2026-09-05", "8450.00", "UPI/700000000001/R KUMAR/rkumar@okaxis/emi sept"),
        ("700000000002", "2026-09-11", "3000.00", "NEFT-700000000002-ANITA SHARMA-part payment"),
        ("700000000003", "2026-09-12", "1500.00", "IMPS/P2A/700000000003/SUNRISE TRADERS/refund"))]
    (folder / "schedules.json").write_text(json.dumps(loans))
    with (folder / "bank_statement.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(lines[0]))
        writer.writeheader()
        writer.writerows(lines)
    decisions = [{**d, "candidates": ";".join(d["candidates"])} for d in match(lines, loans)]
    queue_file.write_queue(queue_file.build(decisions, [], set(), lines, loans, []))
    return loans


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--demo", action="store_true", help="a made-up queue and a made-up Fineract; posts nowhere")
    args = parser.parse_args()
    if args.demo:
        import tempfile

        folder = Path(tempfile.mkdtemp(prefix="reconcile-demo-"))
        fc = DemoFineract(demo(folder))
        print(f"DEMO: made-up data in {folder}, made-up Fineract. Nothing is posted anywhere.")
    else:
        from seed.connector import fineract_client as fc
    try:
        server = make_server(fc, args.port, args.demo)
    except OSError as error:
        sys.exit(f"STOP: cannot listen on 127.0.0.1:{args.port} ({error.strerror}). Try --port {args.port + 1}.")
    print(f"Review page: http://127.0.0.1:{args.port}   (Ctrl+C stops it)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
