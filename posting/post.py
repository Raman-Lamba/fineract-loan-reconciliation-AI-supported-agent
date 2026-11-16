"""Posts matched payments to Fineract and splits each between the partner bank and the lender.

No file I/O and no network of its own: `fc` is the connector's HTTP adapter (or a fake in the tests).
Steps per decision: docs/superpowers/specs/2026-11-16-posting-design.md.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from matcher.match import WINDOW_DAYS

PARTNER_SHARE = Decimal("0.80")  # the partner bank's part of every repayment; the lender keeps the rest
APPROVER = "auto (matcher rules)"
FMT = {"locale": "en", "dateFormat": "yyyy-MM-dd"}


def split(amount: str) -> tuple[Decimal, Decimal]:
    """(partner share, lender share). The lender gets the remainder, so the two always add up."""
    total = Decimal(amount)
    partner = (total * PARTNER_SHARE).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return partner, total - partner


def channel(narration: str) -> str:
    """UPI, NEFT, IMPS or NACH: the first field of every narration format, and a payment type's name."""
    return re.split(r"[/-]", narration, maxsplit=1)[0].upper()


def _recover(d: dict, loan_id: int, fc) -> tuple:
    txn = fc.execute_get(f"loans/{loan_id}/transactions/external-id/{d['txn_id']}")
    if "error" in txn:
        return "failed", f"this txn_id is already in Fineract, but not on this loan: {txn['error']}", None
    if txn.get("manuallyReversed"):
        return "failed", "this txn_id was posted earlier and then reversed in Fineract", None
    return "recovered", f"{d['reason']}; already in Fineract from an earlier run", txn["id"]


def process(d: dict, narration: str, loan_id: int, fc, payment_types: dict) -> tuple:
    """One `post` decision -> (action, reason, Fineract transaction id or None)."""
    loan = fc.execute_get(f"loans/{loan_id}", {"associations": "repaymentSchedule,transactions"})
    if "error" in loan:
        return "failed", loan["error"], None
    # An earlier run may have posted this line and stopped before the ledger write. Look for it before
    # the paid check, or its own payment would read as "already paid" and never reach the ledger.
    if any(t.get("externalId") == d["txn_id"] for t in loan["transactions"]):
        return _recover(d, loan_id, fc)

    amount, day = Decimal(d["amount"]), date.fromisoformat(d["date"])
    # The same instalment the matcher chose from its snapshot, read live.
    inst = next((p for p in loan["repaymentSchedule"]["periods"]
                 if p.get("period") and Decimal(str(p["totalDueForPeriod"])) == amount
                 and abs((date(*p["dueDate"]) - day).days) <= WINDOW_DAYS), None)
    if inst is None:
        return "queued", "the matched instalment is no longer on the loan", None
    if inst["complete"] or not loan["status"]["active"]:
        return "queued", f"instalment already paid (due {date(*inst['dueDate']).isoformat()})", None

    body = {"transactionDate": d["date"], "transactionAmount": d["amount"], "externalId": d["txn_id"],
            "receiptNumber": d["txn_id"], "note": d["reason"], **FMT}
    payment_type = payment_types.get(channel(narration))
    if payment_type:
        body["paymentTypeId"] = payment_type
    posted = fc.execute_post(f"loans/{loan_id}/transactions?command=repayment", body)
    if "error" not in posted:
        return "posted", d["reason"], posted["resourceId"]
    # Fineract's text for a second use of an externalId, seen on the sandbox on 16 Nov 2026:
    # "Failed data validation due to: value.must.be.unique."
    if "must.be.unique" in posted["error"]:
        return _recover(d, loan_id, fc)
    return "failed", posted["error"], None


def post_all(decisions, narrations: dict, loan_ids: dict, fc, payment_types: dict, ledger_ids: set):
    """Yields (audit row, ledger row or None) for each attempt. A txn_id already in the ledger is skipped."""
    for d in decisions:
        if d["outcome"] != "post" or d["txn_id"] in ledger_ids:
            continue
        action, reason, fineract_txn_id = process(d, narrations[d["txn_id"]], loan_ids[d["loan_ref"]], fc,
                                                  payment_types)
        ledger = None
        if fineract_txn_id is not None:
            partner, lender = split(d["amount"])
            ledger = {"txn_id": d["txn_id"], "date": d["date"], "loan_ref": d["loan_ref"],
                      "fineract_txn_id": fineract_txn_id, "amount": d["amount"],
                      "partner_share": str(partner), "lender_share": str(lender)}
        yield {"txn_id": d["txn_id"], "action": action, "loan_ref": d["loan_ref"], "reason": reason,
               "approver": APPROVER if ledger else ""}, ledger
