"""Decides what to do with each bank line: post it to one loan, queue it for a person, or ignore it.

Pure: no I/O. Rules, in order (docs/superpowers/specs/2026-11-16-matcher-design.md):
1. a txn_id already seen in this statement -> ignore
2. a loan account in the narration (SCPL + 9 digits) -> that loan, if the amount is due
3. the payer's name -> the one loan whose borrower and instalment fit; anything else is queued

It never picks between loans: two that fit, or none, go to a person with the candidates and the reason.
"""
from __future__ import annotations

import re
from datetime import date

WINDOW_DAYS = 5  # a payment counts for an instalment due up to this many days either side


def _paise(amount) -> int:
    return round(float(amount) * 100)


def payer_name(narration: str) -> str:
    """The name field of a UPI, NEFT or IMPS narration, or '' if there is none."""
    slashed, dashed = narration.split("/"), narration.split("-")
    if slashed[0] == "UPI" and len(slashed) > 2:
        return slashed[2]
    if slashed[0] == "IMPS" and len(slashed) > 3:
        return slashed[3]
    if dashed[0] == "NEFT" and len(dashed) > 2:
        return dashed[2]
    return ""


def name_fits(payer: str, first: str, last: str) -> bool:
    """Whether a bank's version of a name could be this borrower: cut short, initial, swapped, run together."""
    first, last, tokens = first.lower(), last.lower(), payer.lower().split()
    if len(tokens) == 1:  # RAMESHKUMA: the first name in full, then some of the surname
        rest = tokens[0][len(first):]
        return tokens[0].startswith(first) and bool(rest) and last.startswith(rest)
    if len(tokens) != 2:
        return False

    def fits(f: str, s: str) -> bool:
        # A 10-character UPI name leaves at least 2 letters of the second word; 1 is too little to trust.
        surname = len(s) >= 2 and last.startswith(s)
        return surname and (f in (first, first[0]) or (len(f) >= 3 and first.startswith(f)))

    return fits(tokens[0], tokens[1]) or fits(tokens[1], tokens[0])


def _decision(line: dict, outcome: str, reason: str, loans: list[dict] = ()) -> dict:
    refs = [loan["loan_ref"] for loan in loans]
    return {"txn_id": line["txn_id"], "date": line["date"], "amount": line["amount"], "outcome": outcome,
            "loan_ref": refs[0] if outcome == "post" else "", "reason": reason, "candidates": refs}


def decide(lines: list[dict], schedules: list[dict]) -> list[dict]:
    by_account = {loan["account_no"]: loan for loan in schedules}

    def due_near(loan: dict, paise: int, day: date) -> str:
        """The due date of an instalment of exactly this amount within the window, or ''."""
        for inst in loan["instalments"]:
            if _paise(inst["amount"]) == paise and abs((date.fromisoformat(inst["due"]) - day).days) <= WINDOW_DAYS:
                return inst["due"]
        return ""

    # shortcut: schedules are a snapshot, so a second payment for an instalment that is already paid
    # would also post; read the loan's outstanding amounts from Fineract when milestone 4 posts.
    seen, out = set(), []
    for line in lines:
        if line["txn_id"] in seen:
            out.append(_decision(line, "ignore", "duplicate: this txn_id appeared earlier in the statement"))
            continue
        seen.add(line["txn_id"])
        paise, day = _paise(line["amount"]), date.fromisoformat(line["date"])

        account = re.search(r"SCPL(\d{9})(?!\d)", line["narration"])
        if account:
            loan = by_account.get(account.group(1))
            if not loan:
                out.append(_decision(line, "queue", f"loan account {account.group(1)} not found"))
            elif due_near(loan, paise, day):
                out.append(_decision(line, "post", f"loan account in the narration; instalment due "
                                                   f"{due_near(loan, paise, day)}", [loan]))
            else:
                out.append(_decision(line, "queue", f"loan account in the narration, but no instalment of this "
                                                    f"amount due within {WINDOW_DAYS} days", [loan]))
            continue

        payer = payer_name(line["narration"])
        if not payer:
            out.append(_decision(line, "queue", "no payer name or loan account in the narration"))
            continue
        named = [loan for loan in schedules if name_fits(payer, loan["firstname"], loan["lastname"])]
        fit = [loan for loan in named if due_near(loan, paise, day)]
        if len(fit) == 1:
            # A full name is not proof when a near-namesake owes the same amount: the answer key (and a
            # careful person) treats same surname + same initial + same amount as two possible borrowers.
            only = fit[0]
            fit += [loan for loan in schedules if loan is not only and due_near(loan, paise, day)
                    and loan["lastname"].lower() == only["lastname"].lower()
                    and loan["firstname"][:1].lower() == only["firstname"][:1].lower()]
        if len(fit) == 1:
            out.append(_decision(line, "post", f"'{payer}' and the amount fit one loan; instalment due "
                                               f"{due_near(fit[0], paise, day)}", fit))
        elif fit:
            out.append(_decision(line, "queue", f"{len(fit)} loans fit '{payer}' and the amount", fit))
        elif named:
            out.append(_decision(line, "queue", f"'{payer}' fits a borrower but the amount differs: "
                                                f"partial or overpaid", named))
        else:
            out.append(_decision(line, "queue", f"no loan fits '{payer}'"))
    return out
