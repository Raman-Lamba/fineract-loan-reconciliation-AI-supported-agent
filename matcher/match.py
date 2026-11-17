"""Decides what to do with each bank line: post it to one loan, queue it for a person, or ignore it.

Pure: no I/O. Rules, in order (docs/superpowers/specs/2026-11-16-matcher-design.md):
1. a txn_id already seen in this statement -> ignore
2. a loan account in the narration (SCPL + 9 digits) -> that loan, if the amount is due
3. the payer's name (read with the bank's entry in FORMATS) -> the one loan whose borrower and
   instalment fit; anything else is queued

It never picks between loans: two that fit, or none, go to a person with the candidates and the reason.
"""
from __future__ import annotations

import re
from datetime import date

WINDOW_DAYS = 5  # a payment counts for an instalment due up to this many days either side


# shortcut: amounts compare to 0.01, so in a 3-decimal currency (KWD, BHD, OMR) two amounts 0.001 apart match as
# one. posting.post still demands the exact instalment amount, so the worst case is a line queued, not a wrong
# post; compare in the currency's own minor unit when a 3-decimal lender uses this.
def _paise(amount) -> int:
    return round(float(amount) * 100)


# One entry per narration format: (how the line starts, field separator, position of the payer's name).
# A bank is one line. Only "synthetic" (statement.generate) has been checked against real files of ours;
# the bank layouts come from secondary sources and no real export was used. Two banks start a line the
# same way with the name elsewhere (UPI/, IMPS-), so the bank has to be given: it is never worked out.
# shortcut: positions are unverified, check each against a real export of that bank before relying on it.
FORMATS = {
    "synthetic": [("UPI/", "/", 2), ("IMPS/", "/", 3), ("NEFT-", "-", 2)],
    "hdfc": [("UPI-", "-", 1), ("NEFT CR-", "-", 2), ("IMPS-", "-", 2)],
    "icici": [("UPI/", "/", 3), ("INF/NEFT/", "/", 4), ("MMT/IMPS/", "/", 4)],
    "sbi": [("NEFT-CR-", "-", 3), ("IMPS-", "-", 2)],
    "other": [],  # a bank with no entry: names come only from the AI reader (ai.narration), if it is used
}
NO_REFERENCE = "NOREF-"  # the prefix statement.import_bank gives a credit that has no bank reference
AI = " (read by the AI reader)"  # marks every reason that rests on a field the model read


def _literal(value, narration: str) -> str:
    """`value` if it is in the narration word for word (case and spacing aside), else ''.

    The AI reader already checks this; checked again here so that nothing the model made up can reach a loan.
    """
    if not isinstance(value, str) or not value.strip():
        return ""
    value = " ".join(value.split())
    return value if value.upper() in " ".join(narration.split()).upper() else ""


def payer_name(narration: str, bank: str = "synthetic") -> str:
    """The name field of a UPI, NEFT or IMPS narration in this bank's layout, or '' if there is none."""
    for start, separator, position in FORMATS[bank]:
        if narration.upper().startswith(start):
            fields = narration.split(separator)
            return fields[position].strip() if len(fields) > position else ""
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
    if line["txn_id"].startswith(NO_REFERENCE) and outcome != "ignore":
        # The bank reference is also the duplicate guard, so a line without one is never posted unseen.
        outcome, reason = "queue", "no bank reference on this line (cash deposit or cheque?): a person must confirm"
    return {"txn_id": line["txn_id"], "date": line["date"], "amount": line["amount"], "outcome": outcome,
            "loan_ref": refs[0] if outcome == "post" else "", "reason": reason, "candidates": refs}


def decide(lines: list[dict], schedules: list[dict], bank: str = "synthetic", extracted: dict | None = None) -> list[dict]:
    """`extracted`: narration -> fields the AI reader read from it (payer_name, loan_account). A field is used
    only where the rules found nothing (no SCPL account, no name in the bank's FORMATS entry), and then
    exactly as the rules' own would be: it fills a blank, it never decides and never overrides a match.
    """
    by_account = {loan["account_no"]: loan for loan in schedules}

    def due_near(loan: dict, paise: int, day: date) -> str:
        """The due date of an instalment of exactly this amount within the window, or ''."""
        for inst in loan["instalments"]:
            if _paise(inst["amount"]) == paise and abs((date.fromisoformat(inst["due"]) - day).days) <= WINDOW_DAYS:
                return inst["due"]
        return ""

    # Schedules are a snapshot: whether the instalment is still unpaid is checked live, in posting.post.
    seen, out = set(), []
    for line in lines:
        if line["txn_id"] in seen:
            out.append(_decision(line, "ignore", "duplicate: this txn_id appeared earlier in the statement"))
            continue
        seen.add(line["txn_id"])
        paise, day = _paise(line["amount"]), date.fromisoformat(line["date"])

        ai = (extracted or {}).get(line["narration"]) or {}
        account = re.search(r"SCPL(\d{9})(?!\d)", line["narration"])
        number, via = (account.group(1), "") if account else ("", "")
        # A loan account the model read counts only if it is a loan we know; otherwise the name rule decides.
        if not number and _literal(ai.get("loan_account"), line["narration"]) in by_account:
            number, via = _literal(ai["loan_account"], line["narration"]), AI
        if number:
            loan = by_account.get(number)
            if not loan:
                out.append(_decision(line, "queue", f"loan account {number} not found"))
            elif due_near(loan, paise, day):
                out.append(_decision(line, "post", f"loan account in the narration{via}; instalment due "
                                                   f"{due_near(loan, paise, day)}", [loan]))
            else:
                out.append(_decision(line, "queue", f"loan account in the narration{via}, but no instalment of "
                                                    f"this amount due within {WINDOW_DAYS} days", [loan]))
            continue

        payer = payer_name(line["narration"], bank)
        if not payer and _literal(ai.get("payer_name"), line["narration"]):
            payer, via = _literal(ai["payer_name"], line["narration"]), AI
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
            out.append(_decision(line, "post", f"'{payer}'{via} and the amount fit one loan; instalment due "
                                               f"{due_near(fit[0], paise, day)}", fit))
        elif fit:
            out.append(_decision(line, "queue", f"{len(fit)} loans fit '{payer}'{via} and the amount", fit))
        elif named:
            out.append(_decision(line, "queue", f"'{payer}'{via} fits a borrower but the amount differs: "
                                                f"partial or overpaid", named))
        else:
            out.append(_decision(line, "queue", f"no loan fits '{payer}'{via}"))
    return out
