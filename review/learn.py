"""Learning from approvals: data/payer_links.csv, the payer accounts people approved for a loan.

review.apply calls record() after a person's approval posted; the review page lists and turns links on and off
with view() and toggle(). matcher.run reads the file. Every change is appended (the last row of a key is its
state) and logged in audit.csv. The file holds account identifiers: personal data, never sent to an AI, shown
masked (matcher.payer.mask). Rules: docs/superpowers/specs/2026-11-18-learning-design.md.
"""
import csv
import hashlib
from datetime import datetime
from pathlib import Path

from matcher.match import NO_REFERENCE
from matcher.payer import FIELDS, current, key, learn, mask
from matcher.run import LINKS
from posting.post import APPROVER


def read(data: Path) -> list[dict]:
    path = data / LINKS
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _append(data: Path, row: dict):
    path = data / LINKS
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            writer.writeheader()
        writer.writerow(row)


def record(data: Path, line: dict, bank: str, loan_ref: str, approver: str, remember: bool) -> dict | None:
    """After a person's approval of `line` posted to `loan_ref`: the audit row of what was learned, or None."""
    if line["txn_id"].startswith(NO_REFERENCE):
        return None
    at = datetime.now().isoformat(timespec="seconds")
    row, reason = learn(current(read(data)), key(line["narration"], bank), loan_ref, line["txn_id"], approver, at,
                        remember)
    if not row:
        return None
    _append(data, row)
    return {"txn_id": line["txn_id"], "action": "payer_" + ("learned" if row["status"] == "active" else "conflict"),
            "loan_ref": loan_ref, "reason": reason, "approver": approver}


def link_id(payer_key: str) -> str:
    """What the page uses to name a link: the account itself never leaves this machine's files."""
    return hashlib.sha256(payer_key.encode()).hexdigest()[:16]


def about(state: dict, payer_key: str) -> dict | None:
    """A queue line's payer account for the page: masked, and what was learned about it."""
    if not payer_key:
        return None
    now = state.get(payer_key)
    status = "new" if now is None else "conflict" if len(now["loans"]) > 1 and now["status"] != "active" else now["status"]
    return {"id": link_id(payer_key), "masked": mask(payer_key), "status": status,
            "loan_ref": now["loan_ref"] if now else "", "loans": now["loans"] if now else []}


def view(data: Path, audit: list[dict]) -> dict:
    """The Learned accounts panel: every key, masked, and how many lines were posted automatically by one."""
    links = [{**about(current(read(data)), k), "approver": r["approver"], "learned_at": r["learned_at"]}
             for k, r in current(read(data)).items()]
    auto = sum(a["action"] == "posted" and a["approver"] == APPROVER and a["reason"].startswith("payer account ")
               for a in audit)
    return {"links": links, "active": sum(link["status"] == "active" for link in links), "auto_matched": auto}


def toggle(data: Path, body: dict) -> tuple[dict | None, str]:
    """A person turns a link off, or on for one of the loans it was approved for. (audit row, problem)."""
    approver = str(body.get("approver") or "").strip()
    found = next((k for k in current(read(data)) if link_id(k) == body.get("id")), None)
    if not approver:
        return None, "Type your name in the Approver box at the top first."
    if found is None:
        return None, "This payer account is not in the learned list any more. Reload the page."
    now = current(read(data))[found]
    on = body.get("status") == "active"
    loan_ref = str(body.get("loan_ref") or now["loan_ref"])
    if on and loan_ref not in now["loans"]:
        return None, f"{loan_ref} was never approved for this payer account."
    if now["status"] == ("active" if on else "disabled") and now["loan_ref"] == loan_ref:
        return None, "Nothing to change."
    _append(data, {"payer_key": found, "loan_ref": loan_ref, "learned_from_txn": "", "approver": approver,
                   "learned_at": datetime.now().isoformat(timespec="seconds"), "status": "active" if on else "disabled"})
    what = f"turned on for {loan_ref}" if on else "turned off"
    return {"txn_id": "", "action": "payer_" + ("enabled" if on else "disabled"), "loan_ref": loan_ref,
            "reason": f"payer account {mask(found)} {what} by a person", "approver": approver}, ""
