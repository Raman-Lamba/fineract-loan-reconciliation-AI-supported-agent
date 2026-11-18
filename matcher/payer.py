"""The payer's account, read from a bank line, and the links people approved from it. Pure: no I/O.

A payer key names the account the money came from, never a person: `iban:<IBAN>`, `vpa:<upi id>` or
`acct:<digits>` (a UK sort code + account number, or another account number). Only what is unambiguous is read:
- a UPI line (narration starting UPI/ or UPI-) with exactly one field that is a whole VPA (name@handle);
- the account field of a camt.053 / MT940 line (statement.import_structured writes `payer | remittance | account |
  channel`): an IBAN only if its mod-97 check holds, else an all-digit account number.
NEFT/IMPS free text is never read: a long number there may be the payer's, the lender's or a reference.

Checked only against made-up data (UNVERIFIED): the HDFC and ICICI UPI layouts, the camt.053 DbtrAcct and MT940
?31 fields (statement.import_structured), and UK sort code + account in Othr/Id. The synthetic statement's VPAs
are the payer's first name + a bank, so in it one VPA can belong to several people.

Links (data/payer_links.csv, see review.learn) are a log: one row per change, the last row of a key is its state.
Rules: docs/superpowers/specs/2026-11-18-learning-design.md.
"""
from __future__ import annotations

import re

STRUCTURED = ("camt053", "mt940")
# shortcut: the HDFC and ICICI UPI layouts are unverified; add a bank (or drop one) once a real export shows its VPA field.
UPI_BANKS = ("synthetic", "hdfc", "icici")  # banks whose UPI narrations carry the payer's VPA as a field
VPA = re.compile(r"[a-z0-9][a-z0-9._-]{1,254}@[a-z][a-z0-9]{1,63}")
FIELDS = ["payer_key", "loan_ref", "learned_from_txn", "approver", "learned_at", "status"]


def iban_ok(iban: str) -> bool:
    """ISO 13616: two letters, two check digits, up to 30 letters/digits, and the number mod 97 is 1."""
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    moved = iban[4:] + iban[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def _account(field: str) -> str:
    compact = re.sub(r"[\s-]", "", field).upper()
    if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", compact):
        return f"iban:{compact}" if iban_ok(compact) else ""  # looks like an IBAN but fails the check: a typo
    if re.fullmatch(r"\d{6,34}", compact):
        return f"acct:{compact}"
    return ""


def key(narration: str, bank: str = "synthetic") -> str:
    """The payer key of a bank line in this bank's layout, or '' when there is none or it is ambiguous."""
    if bank in STRUCTURED:
        fields = narration.split(" | ")
        return _account(fields[2]) if len(fields) > 2 else ""
    if bank in UPI_BANKS and narration[:4].upper() in ("UPI/", "UPI-"):
        words = re.split(rf"[\s{re.escape(narration[3])}]+", narration.lower())
        found = {word for word in words if VPA.fullmatch(word)}  # a second VPA anywhere (a memo) makes it ambiguous
        return f"vpa:{found.pop()}" if len(found) == 1 else ""
    return ""


def mask(payer_key: str) -> str:
    """`iban:****3000`: the kind and the last 4 characters only, for every text a person or a log sees."""
    kind, _, value = payer_key.partition(":")
    return f"{kind}:****{value[-4:]}" if value else ""


def current(rows: list[dict]) -> dict:
    """payer_key -> {**last row, "loans": every loan this key was approved for, in order}."""
    out = {}
    for row in rows:
        loans = out.get(row["payer_key"], {}).get("loans", [])
        out[row["payer_key"]] = {**row, "loans": loans + [row["loan_ref"]] * (row["loan_ref"] not in loans)}
    return out


def learn(state: dict, payer_key: str, loan_ref: str, txn: str, approver: str, at: str,
          remember: bool = True) -> tuple[dict | None, str]:
    """What one posted human approval changes: (a row to append to the links log or None, the audit reason or '').

    New key: one active link, if `remember`. Same loan again, or a key a person disabled: nothing. A different loan
    for an active key: the key is disabled (a shared or family account), whatever `remember` says.
    """
    if not payer_key:
        return None, ""
    row = {"payer_key": payer_key, "loan_ref": loan_ref, "learned_from_txn": txn, "approver": approver,
           "learned_at": at}
    now = state.get(payer_key)
    if now is None:
        if not remember:
            return None, ""
        return {**row, "status": "active"}, f"learned payer account {mask(payer_key)} for {loan_ref}"
    if now["status"] != "active" or now["loan_ref"] == loan_ref:
        return None, ""
    return {**row, "status": "disabled"}, (f"payer account {mask(payer_key)} was approved for {now['loan_ref']} "
                                           f"before and now for {loan_ref}: link disabled (a shared account?) until "
                                           f"a person turns it on again")
