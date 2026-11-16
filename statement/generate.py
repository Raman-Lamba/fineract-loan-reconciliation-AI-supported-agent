"""Turns loan schedules into a messy bank statement plus an answer key. Pure: no I/O in generate().

Run:  python -m statement.generate [--hdfc]     (reads data/schedules.json, writes data/*.csv)

--hdfc also writes the same statement laid out like an HDFC net-banking export (data/bank_export_hdfc.csv),
to test statement.import_bank and the matcher's HDFC narration formats against the answer key.

The statement is all the agent may see. The answer key says, per line, what a careful person would do:
post (to expected_loan_ref), queue (a person decides), ignore (duplicate), or none (missed: no line).
true_loan_ref is the loan whose borrower really paid, whatever the outcome: only the demo reviewer
(review.simulate) reads it.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

CUTOFF = date(2026, 10, 31)
AMBIGUITY_DAYS = 5

# Case weights for each instalment. `ambiguous` is not drawn: it is detected from the data afterwards.
WEIGHTS = {"clean": 62, "messy_name": 10, "loan_ref": 5, "early_late": 7, "partial": 4, "overpaid": 3,
           "paid_by_other": 3, "missed": 3, "duplicate": 3}
OUTCOME = {"clean": "post", "messy_name": "post", "loan_ref": "post", "early_late": "post",
           "partial": "queue", "overpaid": "queue", "paid_by_other": "queue", "ambiguous": "queue",
           "unrelated": "queue", "duplicate": "ignore", "missed": "none"}
UNRELATED_SHARE = 0.01  # extra credits from non-borrowers, as a share of instalments

OTHER_FIRST = ["Geeta", "Mohan", "Rekha", "Harish", "Usha", "Prakash", "Shanti", "Gopal"]
STRANGERS = ["ACME TRADERS", "SHREE BALAJI ENT", "INTEREST CREDIT", "REFUND FLIPKART", "R K STORES",
             "CASH DEP BRANCH 0412", "SALARY ADV", "NEW INDIA ASSUR"]
BANKS = ["okaxis", "oksbi", "okhdfcbank", "okicici", "ybl", "paytm"]
MEMOS = ["emi", "loan emi", "emi {mon}", "instalment", "repayment", "{mon} emi", ""]
MON = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _messy(first: str, last: str, rng: random.Random) -> str:
    return rng.choice([
        f"{first} {last[:3]}",            # truncated surname: RAMESH KUM
        f"{first[0]} {last}",             # initial: R KUMAR
        f"{last} {first}",                # swapped: KUMAR RAMESH
        f"{first}{last}"[:12],            # run together and cut, as banks do
    ]).upper()


def _narration(name: str, due: date, txn: str, rng: random.Random) -> tuple[str, str]:
    memo = rng.choice(MEMOS).format(mon=MON[due.month - 1])
    channel = rng.choice(["UPI", "UPI", "UPI", "NEFT", "IMPS"])
    if channel == "UPI":
        handle = name.split()[0].lower()
        return channel, f"UPI/{txn[-6:]}/{name[:10]}/{handle}@{rng.choice(BANKS)}/{memo}".rstrip("/")
    if channel == "NEFT":
        return channel, f"NEFT-{txn}-{name}-{memo}".rstrip("-").upper()
    return channel, f"IMPS/P2A/{txn[-6:]}/{name}/{memo}".rstrip("/").upper()


def _ambiguous(loan: dict, amount: float, day: date, index: dict) -> bool:
    """True if another loan has the same surname, first initial and instalment amount near this date."""
    key = (loan["lastname"].lower(), loan["firstname"][:1].lower(), amount)
    for other_ref, due in index.get(key, []):
        if other_ref != loan["loan_ref"] and abs((due - day).days) <= AMBIGUITY_DAYS:
            return True
    return False


def generate(schedules: list[dict], seed: int = 20261116) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    index: dict = {}
    for loan in schedules:
        for inst in loan["instalments"]:
            key = (loan["lastname"].lower(), loan["firstname"][:1].lower(), inst["amount"])
            index.setdefault(key, []).append((loan["loan_ref"], date.fromisoformat(inst["due"])))

    used: set[str] = set()

    def new_txn() -> str:
        while True:
            txn = f"{rng.randrange(10**11, 10**12)}"
            if txn not in used:
                used.add(txn)
                return txn

    lines, key = [], []
    cases, weights = list(WEIGHTS), list(WEIGHTS.values())
    n_instalments = 0
    for loan in schedules:
        first, last = loan["firstname"], loan["lastname"]
        for inst in loan["instalments"]:
            due = date.fromisoformat(inst["due"])
            if due > CUTOFF:
                continue
            n_instalments += 1
            case = rng.choices(cases, weights)[0]
            amount, day, txn = inst["amount"], due, new_txn()
            if case == "missed":
                key.append({"txn_id": "", "date": due.isoformat(), "case": case, "expected_outcome": "none",
                            "expected_loan_ref": loan["loan_ref"], "true_loan_ref": ""})
                continue
            name = f"{first} {last}".upper()
            if case == "messy_name":
                name = _messy(first, last, rng)
            elif case == "paid_by_other":
                name = f"{rng.choice(OTHER_FIRST)} {last}".upper()
            elif case == "early_late":
                day = due + timedelta(days=rng.choice([-4, -3, -2, -1, 1, 2, 3, 4]))
            elif case == "partial":
                amount = round(amount * rng.uniform(0.5, 0.9), 2)
            elif case == "overpaid":
                amount = round(amount + rng.choice([500, 1000, 2000, amount]), 2)
            if case == "loan_ref":
                narration = f"NACH/ACH DR/SCPL{loan['account_no']}/{MON[due.month - 1].upper()} EMI/{txn[-8:]}"
            else:
                _, narration = _narration(name, due, txn, rng)
            if case in ("clean", "messy_name", "early_late", "duplicate") and _ambiguous(loan, amount, day, index):
                case = "ambiguous"
            row = {"txn_id": txn, "date": day.isoformat(), "amount": f"{amount:.2f}", "narration": narration}
            lines.append(row)
            outcome_case = "clean" if case == "duplicate" else case  # the first copy of a duplicate is real
            key.append({"txn_id": txn, "date": row["date"], "case": outcome_case,
                        "expected_outcome": OUTCOME[outcome_case],
                        "expected_loan_ref": loan["loan_ref"] if OUTCOME[outcome_case] == "post" else "",
                        "true_loan_ref": loan["loan_ref"]})
            if case == "duplicate":
                lines.append(dict(row))  # the bank feed repeats the line: same txn_id
                key.append({"txn_id": txn, "date": row["date"], "case": "duplicate", "expected_outcome": "ignore",
                            "expected_loan_ref": "", "true_loan_ref": loan["loan_ref"]})

    for _ in range(max(1, round(n_instalments * UNRELATED_SHARE))):
        txn = new_txn()
        day = date(2026, 6, 1) + timedelta(days=rng.randrange((CUTOFF - date(2026, 6, 1)).days + 1))
        row = {"txn_id": txn, "date": day.isoformat(), "amount": f"{rng.randrange(200, 50000)}.00",
               "narration": f"NEFT-{txn}-{rng.choice(STRANGERS)}"}
        lines.append(row)
        key.append({"txn_id": txn, "date": row["date"], "case": "unrelated", "expected_outcome": "queue",
                    "expected_loan_ref": "", "true_loan_ref": ""})

    lines.sort(key=lambda r: (r["date"], r["txn_id"]))
    key.sort(key=lambda r: (r["date"], r["txn_id"], r["case"]))
    return lines, key


HDFC_HEADER = ["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]
DEBIT_EVERY = 40  # one debit row (bank charges) after every 40 credits


def _indian(amount: Decimal) -> str:
    """121940.65 -> 1,21,940.65: the last three digits, then groups of two."""
    whole, paise = f"{amount:.2f}".split(".")
    return re.sub(r"(?<=\d)(?=(\d\d)*\d{3}$)", ",", whole) + "." + paise


def _hdfc_narration(txn: str, narration: str) -> str:
    """A synthetic narration rewritten in HDFC's layout (unverified, from secondary sources)."""
    if narration.startswith("UPI/"):
        _, _, name, vpa, *memo = narration.split("/")
        return f"UPI-{name}-{vpa}-UTIB0000001-{txn}-{memo[0] if memo else 'UPI'}"
    if narration.startswith("NEFT-"):  # the comma is on purpose: the cell has to be quoted
        return f"NEFT CR-SBIN0000001-{narration.split('-')[2]}-SCPL COLLECTIONS A/C, MUMBAI-SBINR5{txn}"
    if narration.startswith("IMPS/"):
        _, _, _, name, *memo = narration.split("/")
        return f"IMPS-{txn}-{name}-SBIN-XXXXXXXX{txn[-4:]}-{memo[0] if memo else 'IMPS'}"
    _, _, account, memo, mandate = narration.split("/")  # NACH
    return f"NACH-CR-{account}-{memo}-UMRN HDFC70200000{mandate}"


def hdfc_export(lines: list[dict]) -> list[list[str]]:
    """The statement as the rows of an HDFC-style export: preamble, header, credits with debits mixed in,
    footer. Pure, and draws nothing from the random stream, so the statement itself does not change."""
    ddmmyy = lambda iso: date.fromisoformat(iso).strftime("%d/%m/%y")  # noqa: E731
    stars = ["*" * 8] * len(HDFC_HEADER)
    rows = [["SYNTHETIC DATA - not a real bank statement"], ["Account Branch : SAMPLE BRANCH"],
            ["Account No : 50200000000000 CURRENT ACCOUNT"],
            [f"Statement From : {ddmmyy(lines[0]['date'])} To : {ddmmyy(lines[-1]['date'])}"], [],
            HDFC_HEADER, stars]
    balance, debits, charge = Decimal(0), Decimal(0), Decimal("590.00")
    for i, line in enumerate(lines, 1):
        day, amount = ddmmyy(line["date"]), Decimal(line["amount"])
        balance += amount
        # The reference column is zero-padded to 16, as HDFC's is. Some UPI rows leave it all zeros, so
        # the importer has to fall back to the reference inside the narration.
        in_column = not (line["narration"].startswith("UPI/") and line["txn_id"].endswith("0"))
        rows.append([day, _hdfc_narration(line["txn_id"], line["narration"]),
                     line["txn_id"].zfill(16) if in_column else "0" * 16, day, "", _indian(amount), _indian(balance)])
        if i % DEBIT_EVERY == 0:
            balance, debits = balance - charge, debits + charge
            rows.append([day, "SMS ALERT CHARGES, GST INCL", "0" * 16, day, _indian(charge), "", _indian(balance)])
    credits = sum(Decimal(line["amount"]) for line in lines)
    rows += [stars, ["STATEMENT SUMMARY :-"],
             ["Opening Balance", "Dr Count", "Cr Count", "Debits", "Credits", "Closing Bal"],
             ["0.00", str(len(lines) // DEBIT_EVERY), str(len(lines)), _indian(debits), _indian(credits), _indian(balance)]]
    return rows


def _write(path: Path, rows: list[dict]):
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hdfc", action="store_true", help="also write data/bank_export_hdfc.csv")
    args = parser.parse_args()
    data = Path(__file__).resolve().parents[1] / "data"
    schedules = json.loads((data / "schedules.json").read_text())
    lines, key = generate(schedules)
    _write(data / "bank_statement.csv", lines)
    _write(data / "answer_key.csv", key)
    counts: dict = {}
    for row in key:
        counts[row["case"]] = counts.get(row["case"], 0) + 1
    print(f"statement lines: {len(lines)} answer key rows: {len(key)}")
    print("cases: " + ", ".join(f"{c}={n}" for c, n in sorted(counts.items(), key=lambda x: -x[1])))
    if args.hdfc:
        with (data / "bank_export_hdfc.csv").open("w", newline="") as f:
            csv.writer(f).writerows(hdfc_export(lines))
        print("HDFC-layout export of the same statement: data/bank_export_hdfc.csv")


if __name__ == "__main__":
    main()
