"""The no-code page: connect, bring a statement, match and post, review the queue. For this machine only.

Run:  python -m ui.serve [--port 8000] [--demo] [--open] [--env FILE] [--data DIR]
      (or double-click Start.bat; designs: docs/superpowers/specs/2026-11-16-ui-design.md and
      2026-11-17-ai-reader-design.md)

Tabs: Connect writes .env; Statement uploads a bank CSV, finds its columns and imports it; Run reads the
loans, matches and (after an explicit confirmation) posts the clear lines; Review is a second way to fill in
data/queue.csv: a click writes the person's decision into that file, as Excel would, and then runs
review.apply on that one row. Every step calls the same code as the command-line modules.

--demo serves a made-up queue from a temporary folder and a made-up Fineract. It posts nowhere.
--env and --data point the page at another .env and data folder (a copy, for a trial).
"""
# shortcut: one local user and no login; the approver is whatever name is typed in the box. Several
# reviewers, or any use beyond this machine, need real accounts, because that name is the audit trail.
import argparse
import csv
import io
import json
import re
import secrets
import sys
import threading
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import posting.run as files
from ai import client as ai_client
from ai import layout as ai_layout
from ai.narration import extract
from matcher import run as matcher_run
from matcher.match import FORMATS, payer_name
from matcher.match import decide as match
from posting.post import handled, post_all
from posting.verify import check, read_fineract, totals
from review import apply as review_apply
from review import build as queue_file
from review import report
from statement.fetch_schedules import fetch, list_loans
from statement.import_bank import convert, record, recorded_bank

ROOT = Path(__file__).resolve().parents[1]
MAX_UPLOAD = 5_000_000  # bytes of statement text; a year of a busy account's CSV is well under this
MAX_BODY = 65536
# The Connect form's fields: (name in .env, write-only?). Secrets are never sent back, only "saved" or not.
SETTINGS = [("MIFOSX_BASE_URL", False), ("MIFOSX_TENANT_ID", False), ("MIFOSX_USERNAME", False),
            ("MIFOSX_PASSWORD", True), ("MIFOS_URL", False), ("BANK", False), ("ANTHROPIC_API_KEY", True),
            ("AI_MODEL", False), ("AI_PROVIDER", False), ("AI_BASE_URL", False)]
# "AI_API_KEY" is the form's one key box: it is saved under the chosen provider's own name (ai.client.PROVIDERS),
# so a key typed for one provider is never sent to another.
KEY_BOX = "AI_API_KEY"
BANKS = [bank for bank in FORMATS if bank != "synthetic"]

PAGE = Path(__file__).with_name("index.html")
CHANNELS = ("UPI", "NEFT", "IMPS", "NACH")  # the payment type names posting.post.channel looks for
WRITING = threading.Lock()  # one decision at a time: each reads and rewrites queue.csv
NOT_RUN = "Not run: fix the failed check above first."

CHECKS = [
    ("Fineract answers at the URL",
     "On the Connect tab, set the Fineract API address (MIFOSX_BASE_URL) to your Fineract's, ending in "
     "/fineract-provider/api/v1, press Save, and make sure Fineract is running (the sandbox: Start.bat). For an "
     "https address with a self-signed certificate also add MIFOSX_SKIP_TLS_VERIFY=true to .env."),
    ("The tenant exists",
     "On the Connect tab, set the tenant (MIFOSX_TENANT_ID) to your tenant's identifier and press Save. It is "
     "'default' on a standard install; it is the value the Mifos login page sends as Fineract-Platform-TenantId."),
    ("The login works",
     "On the Connect tab, check the user name and password (MIFOSX_USERNAME, MIFOSX_PASSWORD) and press Save: the "
     "same pair must log in on the Mifos login page."),
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


# ---- Connect: .env, written from the form -------------------------------------------------------------

def settings_view(env: Path) -> dict:
    """What the Connect tab may show: plain values, and for a password or key only whether one is saved."""
    values = ai_client.read_env(env)
    return {"values": {name: values.get(name, "") for name, secret in SETTINGS if not secret},
            "saved": {name: bool(values.get(name)) for name, secret in SETTINGS if secret},
            "banks": BANKS, "env_exists": env.exists(),
            "ai": ai_view(values)}


def ai_view(values: dict) -> dict:
    try:
        cfg = ai_client.config(values)
    except ai_client.AIError:  # a hand-edited bad AI_PROVIDER: show Claude's setup until it is chosen again
        cfg = ai_client.config({})
    return {"provider": cfg["provider"], "model": cfg["model"], "base": cfg["base"], "local": cfg["local"],
            "key": bool(cfg["key"]), "ready": ai_client.available_for(cfg), "consent": ai_client.consent_given(files.DATA, cfg),
            "consent_text": ai_client.consent_text(cfg), "label": cfg["label"],
            "providers": {name: {"name": p["name"], "model": p["model"], "models": p["models"], "base": p.get("base", ""),
                                 "verified": p["verified"]} for name, p in ai_client.PROVIDERS.items()}}


def clean_settings(body: dict, current_provider: str = "") -> dict:
    """The form's values that may go into .env: name -> text, or None to remove the line. Raises ValueError.

    An empty password or key box means "keep the saved one", so a secret never has to be shown to be kept.
    """
    out, names = {}, {**dict(SETTINGS), KEY_BOX: True}
    given = body.get("values") or {}
    provider = given.get("AI_PROVIDER", current_provider) or "claude"
    if provider not in ai_client.PROVIDERS:
        raise ValueError(f"AI_PROVIDER must be one of {', '.join(ai_client.PROVIDERS)}")
    key_name = ai_client.PROVIDERS[provider]["key"]
    for name, value in (body.get("values") or {}).items():
        if name not in names or not isinstance(value, str):
            raise ValueError(f"'{name}' is not a setting this page writes")
        value = value.strip()
        if any(c in value for c in "\r\n\0") or len(value) > 500:
            raise ValueError(f"{name} must be one line of text")
        if names[name] and not value:
            continue
        if name in ("MIFOSX_BASE_URL", "MIFOS_URL") and value and not re.match(r"https?://", value):
            raise ValueError(f"{name} must start with http:// or https://")
        if name == "BANK" and value and value not in BANKS:
            raise ValueError(f"BANK must be one of {', '.join(BANKS)}")
        if name == "AI_MODEL" and value:
            if provider == "claude" and not re.fullmatch(r"claude-[a-z0-9.-]+", value):
                raise ValueError("AI_MODEL must be a Claude model id such as claude-haiku-5-5")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value):
                raise ValueError("AI_MODEL must be a model name such as gemini-3.8-flash or llama3.1")
        if name == "AI_BASE_URL" and value and not re.match(r"https?://\S+$", value):
            raise ValueError("AI_BASE_URL must start with http:// or https://, such as http://localhost:11434/v1")
        if name in ("ANTHROPIC_API_KEY", KEY_BOX) and re.search(r"\s", value):
            raise ValueError("the API key has a space in it: paste it again")
        out[key_name if name == KEY_BOX else name] = value
    for name in body.get("forget") or []:
        if names.get(name):  # only a password or a key can be forgotten from the form
            out[key_name if name == KEY_BOX else name] = None
    return out


def merge_env(env: Path, updates: dict):
    """Writes `updates` into .env: a key's line is replaced in place (or removed for None), new keys are
    added at the end, and every other line (comments, settings this page does not know) is kept as it was."""
    text = env.read_bytes().decode("utf-8-sig") if env.exists() else ""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines, done = [], set()
    for line in text.splitlines():
        name = line.partition("=")[0].strip()
        if "=" in line and not name.startswith("#") and name in updates:
            if name not in done and updates[name] is not None:
                lines.append(f"{name}={updates[name]}")
            done.add(name)
            continue
        lines.append(line)
    lines += [f"{name}={value}" for name, value in updates.items() if name not in done and value]
    env.write_text(newline.join(lines) + newline, encoding="utf-8", newline="")


def consent(env: Path, body: dict) -> dict:
    """Records the person's tick (in data/, so it holds for every browser). Only the exact box counts."""
    if body.get("agree") is not True:
        return {"problem": "Tick the box first.", **settings_view(env)}
    try:
        ai_client.record_consent(files.DATA, ai_client.config(ai_client.read_env(env)))
    except ai_client.AIError as problem:
        return {"problem": str(problem), **settings_view(env)}
    return {"done": "Your consent is saved.", **settings_view(env)}


def apply_settings(fc, env: Path):
    """Points the running Fineract client at .env's values, so a saved form needs no restart."""
    values = ai_client.read_env(env)
    for attribute, name in (("base_url", "MIFOSX_BASE_URL"), ("tenant_id", "MIFOSX_TENANT_ID"),
                            ("username", "MIFOSX_USERNAME"), ("password", "MIFOSX_PASSWORD")):
        if name in values:
            setattr(fc, attribute, values[name] or ("default" if attribute == "tenant_id" else values[name]))
    if "MIFOSX_SKIP_TLS_VERIFY" in values and hasattr(fc, "verify_ssl"):
        fc.verify_ssl = values["MIFOSX_SKIP_TLS_VERIFY"].lower() != "true"


# ---- Statement: upload, find the columns, import -------------------------------------------------------

def upload(body: dict) -> dict:
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        return {"problem": "The file is empty."}
    if "\0" in text or text.startswith("PK"):
        return {"problem": "This is not a CSV file (it looks like an Excel or zip file). In your bank's net banking "
                           "choose CSV, or open it in Excel and use Save As > CSV UTF-8."}
    rows = ai_layout.rows_of(text)
    (files.DATA / "bank_export.csv").write_text(text, encoding="utf-8", newline="")
    return {"done": f"Saved {body.get('name') or 'the file'} as data\\bank_export.csv ({len(rows)} rows).",
            "rows": len(rows)}


def _export() -> str:
    path = files.DATA / "bank_export.csv"
    return path.read_text(encoding="utf-8-sig") if path.exists() else ""


def _lines(cmap: dict, text: str) -> tuple[list[dict], dict, bool]:
    """(statement lines, counts, through the HDFC path?) for this map, or ValueError with the row number.

    The HDFC layout, unchanged, goes through import_bank's own HDFC path exactly as the command line does.
    """
    hdfc = ai_layout.hdfc_map(ai_layout.rows_of(text))
    if hdfc and all(cmap.get(field) == value for field, value in hdfc.items()):
        lines, counts = convert(io.StringIO(text, newline=""), "hdfc")
        return lines, counts, True
    try:
        _, lines, counts = ai_layout.check(cmap, text)
    except ai_layout.LayoutError as problem:
        raise ValueError(str(problem)) from None
    return lines, counts, False


def preview(cmap: dict | None, text: str) -> dict:
    """The counts and the first imported lines for a map, or the importer's refusal in plain words."""
    if not isinstance(cmap, dict) or not cmap:
        return {}
    try:
        lines, counts, _ = _lines(cmap, text)
    except ValueError as problem:
        return {"problem": str(problem)}
    if not lines:
        return {"problem": "no credit rows found"}
    return {"counts": counts, "sample": lines[:5]}


def read_statement(env: Path, use_ai: bool, opener=None) -> dict:
    """Finds the columns of data/bank_export.csv: HDFC, a map confirmed before, or (if asked) the AI reader."""
    text = _export()
    if not text:
        return {"problem": "Choose your bank statement file first (step 1)."}
    rows = ai_layout.rows_of(text)
    layouts = files.DATA / "layouts.json"
    out = {"rows": rows[:60], "map": ai_layout.hdfc_map(rows), "source": "hdfc", "note": ""}
    if out["map"] is None:
        out.update(map=ai_layout.saved(text, layouts), source="saved")
    if out["map"] is None and use_ai:
        try:
            raw = ai_layout.ask(rows, ai_client.sender(env, files.DATA, opener))
            out.update(map=ai_layout.check(raw, text)[0], source="ai")
        except ai_client.AIError as problem:
            out.update(source=None, note=f"The AI reader did not run: {problem}.")
        except ai_layout.LayoutError as problem:
            out.update(source=None, note=f"The AI reader's column map did not fit the file, so it is not used: {problem}.")
    if out["map"] is None:
        out["source"] = None
        out["note"] = out["note"] or ("This is not the HDFC layout and no column map is saved for it. Choose the "
                                      "columns below, starting with the row that holds the column names" +
                                      ("." if use_ai else ", or save a Claude API key (or set up another AI service) on the Connect tab to have "
                                                          "them read for you."))
    return {**out, **preview(out["map"], text)}


def import_statement(env: Path, body: dict) -> dict:
    """Writes data/bank_statement.csv from the confirmed map, remembers the map, and the bank in .env."""
    cmap, bank = body.get("map"), body.get("bank")
    if bank not in BANKS:
        return {"problem": f"Choose which bank this is ({', '.join(BANKS)})."}
    text = _export()
    if not text or not isinstance(cmap, dict):
        return {"problem": "Choose your bank statement file and read it first."}
    try:
        lines, counts, hdfc = _lines(cmap, text)
    except ValueError as problem:
        return {"problem": f"Nothing was imported. {problem}"}
    if not lines:
        return {"problem": "Nothing was imported: no credit rows found."}
    out = files.DATA / "bank_statement.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(lines[0]))
        writer.writeheader()
        writer.writerows(lines)
    if not hdfc:
        ai_layout.save(ai_layout.check(cmap, text)[0], files.DATA / "layouts.json")
    merge_env(env, {"BANK": bank})
    record(out, bank)  # which bank's narrations this statement holds, for the Run tab
    return {"done": counts, "bank": bank}


# ---- Run: loans, match, post ---------------------------------------------------------------------------

def statement_bank(env: Path) -> str:
    """The narration layout of data/bank_statement.csv, as noted when it was imported (page or command
    line); a statement nobody imported is the synthetic one. The Run tab shows it and it can be changed."""
    return recorded_bank(files.DATA / "bank_statement.csv")


def _generic(reason: str) -> str:
    """A reason without the payer's name, dates or numbers, so lines can be counted by kind."""
    reason = re.sub(r"'[^']*'", "'…'", reason)
    reason = re.sub(r"\d{4}-\d{2}-\d{2}", "(date)", reason)
    return re.sub(r"loan account \d+", "loan account …", reason)


def summary(decisions: list[dict]) -> dict:
    """How many lines would post, queue or be ignored, and why; and what posting would do now."""
    kinds = {}
    for d in decisions:
        kind = kinds.setdefault((d["outcome"], _generic(d["reason"])), {"lines": 0, "amount": Decimal("0")})
        kind["lines"] += 1
        kind["amount"] += Decimal(d["amount"])
    skip = handled(files.read("ledger.csv"), files.read("audit.csv"))
    pending = [d for d in decisions if d["outcome"] == "post" and d["txn_id"] not in skip]
    return {"lines": len(decisions),
            "counts": {o: sum(d["outcome"] == o for d in decisions) for o in ("post", "queue", "ignore")},
            "reasons": [{"outcome": o, "reason": r, "lines": k["lines"], "amount": str(k["amount"])}
                        for (o, r), k in sorted(kinds.items(), key=lambda item: ("post", "queue", "ignore").index(item[0][0]))],
            "pending": {"count": len(pending), "total": str(sum((Decimal(d["amount"]) for d in pending), Decimal("0.00")))},
            "done_before": sum(d["outcome"] == "post" and d["txn_id"] in skip for d in decisions)}


def run_state(env: Path) -> dict:
    """What the Run tab shows before any button: the statement, the loans, and the last match."""
    lines, decisions = files.read("bank_statement.csv"), files.read("decisions.csv")
    current = {line["txn_id"] for line in lines}
    return {"statement": len(lines), "loans": len(_schedules()), "bank": statement_bank(env), "banks": list(FORMATS),
            "match": summary(decisions) if decisions and all(d["txn_id"] in current for d in decisions) else None,
            "ai": settings_view(env)["ai"]}


def read_loans(fc) -> dict:
    """statement.fetch_schedules from the page: every loan and its instalments, read-only."""
    loans = fetch(fc, ok)
    (files.DATA / "schedules.json").write_text(json.dumps(loans, indent=1))
    return {"log": [f"Read {len(loans)} loans and {sum(len(loan['instalments']) for loan in loans)} instalments "
                    f"from Fineract (nothing was changed there)."], "loans": len(loans)}


def unread(lines: list[dict], bank: str) -> list[str]:
    """Narrations the rules find no loan account and no name in: the only ones the AI reader is sent."""
    return [line["narration"] for line in lines
            if not re.search(r"SCPL\d{9}(?!\d)", line["narration"]) and not payer_name(line["narration"], bank)]


def run_match(env: Path, body: dict, opener=None) -> dict:
    """matcher.run from the page. Writes data/decisions.csv; posts nothing."""
    lines, loans = files.read("bank_statement.csv"), _schedules()
    if not lines:
        return {"problem": "There is no statement to match yet: import one on the Statement tab."}
    if not loans:
        return {"problem": "The loans have not been read from Fineract yet: press 'Read the loans' first."}
    bank = body.get("bank") if body.get("bank") in FORMATS else statement_bank(env)
    log, extracted = [f"Statement: {len(lines)} credit lines, narrations read as {bank}. Loans: {len(loans)}."], None
    if body.get("ai"):
        todo = unread(lines, bank)
        try:
            send = ai_client.sender(env, files.DATA, opener)
            extracted, stats = extract(todo, send, files.DATA / "ai_cache.json")
            log.append(f"AI reader: {stats['narrations']} narrations the rules could not read; {stats['from_cache']} "
                       f"answered from the cache, {stats['sent']} sent to {ai_view(ai_client.read_env(env))['label']}; {stats['read']} gave a name or "
                       f"loan account, {stats['unread']} could not be read (they go to a person).")
            log += [f"AI reader problem: {problem}" for problem in stats["errors"]]
        except ai_client.AIError as problem:
            log.append(f"AI reader not used: {problem}. The rules alone decided.")
    decisions = matcher_run.run(bank, extracted, files.DATA)
    result = summary(decisions)
    log.append(f"Matched: {result['counts']['post']} would post, {result['counts']['queue']} go to a person, "
               f"{result['counts']['ignore']} ignored (repeated). Nothing has been posted.")
    if result["done_before"]:
        log.append(f"{result['done_before']} of the clear lines are already in the ledger and will not be posted again.")
    with WRITING:
        queue_file.main()  # review.build: the lines for a person appear on the Review tab now; what was typed is kept
    log.append(f"Review queue: {len(queue_file.read_queue())} lines wait for a person (Review tab).")
    return {"log": log, "match": result}


def run_post(fc, body: dict) -> dict:
    """posting.run + posting.verify + review.build from the page, only after the person confirmed the
    number of payments and the rupee total that are about to be posted. Safe to press twice."""
    with WRITING:
        decisions, lines = files.read("decisions.csv"), files.read("bank_statement.csv")
        narrations = {line["txn_id"]: line["narration"] for line in lines}
        if not decisions or any(d["txn_id"] not in narrations for d in decisions):
            return {"problem": "Press Match first: there is no match for the statement as it is now."}
        now = summary(decisions)["pending"]
        if now["count"] == 0:
            return {"log": ["Nothing to post: every clear payment is already in the ledger."], "posted": 0}
        if body.get("count") is None or body.get("total") is None:
            return {"problem": "Posting needs your confirmation of the number of payments and the total."}
        if (body["count"], str(body["total"])) != (now["count"], now["total"]):
            return {"problem": "The numbers changed since you confirmed them: press Match again and read them."}
        loan_ids = {loan["loan_ref"]: loan["loan_id"] for loan in _schedules()}
        if any(d["loan_ref"] not in loan_ids for d in decisions if d["outcome"] == "post"):
            return {"problem": "The loans changed since the match: press Match again."}
        payment_types = {p["name"]: p["id"] for p in ok(fc.execute_get("paymenttypes"), "Read payment types")}
        skip = handled(files.read("ledger.csv"), files.read("audit.csv"))
        counts = dict.fromkeys(("posted", "recovered", "queued", "failed"), 0)
        log, to_ledger, to_audit = [], files.appender("ledger.csv", files.LEDGER), files.appender("audit.csv", files.AUDIT)
        try:
            for audit, ledger in post_all(decisions, narrations, loan_ids, fc, payment_types, skip):
                if ledger:
                    to_ledger(ledger)
                to_audit({"at": datetime.now().isoformat(timespec="seconds"), **audit})
                counts[audit["action"]] += 1
                if audit["action"] == "failed":
                    log.append(f"Failed: UTR {audit['txn_id']} to {audit['loan_ref']}: {audit['reason']}")
        finally:
            to_ledger.close()
            to_audit.close()
        log.insert(0, f"Posted {counts['posted']} payments to Fineract and split each 80:20 in the ledger. "
                      f"Already paid, sent to a person: {counts['queued']}. Found already in Fineract and recorded: "
                      f"{counts['recovered']}. Failed: {counts['failed']}.")
        ledger = files.read("ledger.csv")
        in_fineract, arrears = read_fineract(_schedules(), fc, ok)
        problems = check(ledger, in_fineract)
        total = totals(ledger)
        log.append(f"Checked the ledger against Fineract: {len(ledger)} rows, {len(problems)} problems"
                   + (": " + "; ".join(problems[:5]) if problems else ".") +
                   f" Rupees posted in all: {total['amount']:,.2f} (partner {total['partner_share']:,.2f}, lender "
                   f"{total['lender_share']:,.2f}). Loans in arrears: {arrears} of {len(_schedules())}.")
        queue_file.main()  # review.build: the unclear lines, for the Review tab
        log.append(f"Review queue rebuilt: {len(queue_file.read_queue())} lines wait for a person (Review tab).")
    return {"log": log, "posted": counts["posted"], "problems": problems}


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, body, kind: str = "application/json", download: str = ""):
        if kind == "application/json":
            body = json.dumps(body)
            # Belt and braces: no code path puts the password or the API key in an answer, and an error
            # text from Fineract, Anthropic or the HTTP library cannot either.
            saved = ai_client.read_env(self.server.env)
            for secret in {getattr(self.server.fc, "password", None), *(saved.get(n) for n in ai_client.SECRETS)}:
                if secret and len(secret) >= 4:
                    body = body.replace(secret, "***").replace(json.dumps(secret)[1:-1], "***")
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
        fc, env = self.server.fc, self.server.env
        if self.path == "/":
            mifos = ai_client.read_env(env).get("MIFOS_URL") or (fc.base_url or "").split("/fineract-provider")[0]
            config = {"token": self.server.token, "demo": self.server.demo, "mifos": mifos.rstrip("/"),
                      "settings": {"MIFOSX_BASE_URL": fc.base_url, "MIFOSX_TENANT_ID": fc.tenant_id,
                                   "MIFOSX_USERNAME": fc.username,
                                   "BANK": ai_client.read_env(env).get("BANK", "hdfc")}}
            page = PAGE.read_text(encoding="utf-8").replace("__CONFIG__", json.dumps(config).replace("<", "\\u003c"))
            self.reply(200, page, "text/html")
        elif self.path == "/api/state":
            self.answer(state)
        elif self.path == "/api/settings":
            self.answer(lambda: settings_view(env))
        elif self.path == "/api/run":
            self.answer(lambda: run_state(env))
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
            return self.reply(403, {"error": "this page is out of date: reload it"})
        fc, env, opener = self.server.fc, self.server.env, self.server.opener
        routes = {
            "/api/decide": lambda body: decide(fc, body),
            "/api/settings": lambda body: self.save_settings(body),
            "/api/consent": lambda body: consent(env, body),
            "/api/testai": lambda body: ai_client.try_it(env, files.DATA, opener),
            "/api/upload": upload,
            "/api/read": lambda body: read_statement(env, bool(body.get("ai")), opener),
            "/api/preview": lambda body: preview(body.get("map"), _export()),
            "/api/import": lambda body: import_statement(env, body),
            "/api/loans": lambda body: read_loans(fc),
            "/api/match": lambda body: run_match(env, body, opener),
            "/api/post": lambda body: run_post(fc, body),
        }
        if self.path not in routes:
            return self.reply(404, {"error": "not found"})
        limit = MAX_UPLOAD if self.path == "/api/upload" else MAX_BODY
        size = int(self.headers.get("Content-Length") or 0)
        if size > limit:
            self.close_connection = True
            return self.reply(413, {"error": f"The file is too big: the limit is {limit // 1_000_000 or 1} MB. "
                                             f"Export a shorter period from your bank and try again."})
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            return self.reply(400, {"error": "the request was not JSON"})
        self.answer(lambda: routes[self.path](body if isinstance(body, dict) else {}))

    def save_settings(self, body: dict) -> dict:
        try:
            updates = clean_settings(body, ai_client.read_env(self.server.env).get("AI_PROVIDER", ""))
        except ValueError as problem:
            return {"problem": str(problem), **settings_view(self.server.env)}
        merge_env(self.server.env, updates)
        apply_settings(self.server.fc, self.server.env)
        return {"done": "Saved." if updates else "Nothing to change.", **settings_view(self.server.env)}


def make_server(fc, port: int, demo: bool = False, env: Path | None = None, opener=None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # this machine only, never 0.0.0.0
    # A new token each run, written into the page: a change must come from a page this run served.
    server.fc, server.token, server.demo = fc, secrets.token_urlsafe(), demo
    # opener: urlopen for the AI reader, or a fake in the tests
    server.env, server.opener = env or ROOT / ".env", opener
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
        ("700000000003", "2026-09-12", "1500.00", "IMPS/P2A/700000000003/SUNRISE TRADERS/refund"),
        ("700000000004", "2026-10-10", "5200.00", "UPI/700000000004/ANITA SHARMA/anita@okaxis/emi oct"))]  # clear: the Run tab posts it
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
    parser.add_argument("--open", action="store_true", help="open the page in the browser once it is up")
    parser.add_argument("--env", type=Path, default=ROOT / ".env", help="the .env the Connect tab reads and writes")
    parser.add_argument("--data", type=Path, help="the data folder (default data/); a copy, for a trial")
    args = parser.parse_args()
    env = args.env.resolve()
    if args.data:
        files.DATA, queue_file.QUEUE = args.data.resolve(), args.data.resolve() / "queue.csv"
        files.DATA.mkdir(parents=True, exist_ok=True)
    if args.demo:
        import tempfile

        folder = Path(tempfile.mkdtemp(prefix="reconcile-demo-"))
        fc = DemoFineract(demo(folder))
        env = folder / ".env" if args.env == ROOT / ".env" else env  # the demo never writes the real .env
        print(f"DEMO: made-up data in {folder}, made-up Fineract. Nothing is posted anywhere.")
    else:
        from seed.connector import fineract_client as fc
        apply_settings(fc, env)
    files.DATA.mkdir(exist_ok=True)
    try:
        server = make_server(fc, args.port, args.demo, env)
    except OSError as error:
        sys.exit(f"STOP: cannot listen on 127.0.0.1:{args.port} ({error.strerror}). Try --port {args.port + 1}.")
    print(f"Page: http://127.0.0.1:{args.port}   (Ctrl+C stops it)   settings: {env}   data: {files.DATA}", flush=True)
    if args.open:
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
