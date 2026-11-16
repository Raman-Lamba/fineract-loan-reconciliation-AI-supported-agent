"""Turns a bank's CSV export into the statement the matcher reads. convert() is pure: no files.

Run:  python -m statement.import_bank <export.csv> [--bank hdfc] [--out data/bank_statement.csv]

Writes `txn_id, date, amount, narration`, credits only, in the export's order. It never drops a money
line quietly: a credit it cannot read stops the import with the row number, and nothing is written.
Rules: docs/superpowers/specs/2026-11-16-bank-import-design.md.

The HDFC layout comes from secondary sources and no real export was used: unverified.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# The export's column headers per bank, found by name (case and spaces ignored) wherever they are.
# One line adds a bank.
LAYOUTS = {
    "hdfc": {"date": "Date", "narration": "Narration", "ref": "Chq./Ref.No.",
             "debit": "Withdrawal Amt.", "credit": "Deposit Amt."},
}
DATE_FORMATS = ("%d/%m/%y", "%d/%m/%Y")
# Digits, plain or with Indian (1,21,940) or western (121,940) thousands separators, and up to 2 decimals.
AMOUNT = re.compile(r"(\d+|\d{1,3}(,\d{3})*|\d{1,2}(,\d{2})*,\d{3})(\.\d{1,2})?")
# shortcut: the fallback only takes an all-digit reference (UPI, IMPS: 12 digits). A NEFT UTR has letters;
# read it from the narration per format if a bank is found to leave the reference column empty for NEFT.
REFERENCE = re.compile(r"(?<![A-Za-z0-9])\d{12,}(?![A-Za-z0-9])")


def _norm(header: str) -> str:
    return "".join(header.lower().split())


def _date(cell: str) -> str:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cell, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _amount(cell: str, row: int, column: str) -> Decimal:
    """An empty cell is 0."""
    if cell and not AMOUNT.fullmatch(cell):
        raise ValueError(f"row {row}: cannot read the amount {cell!r} in '{column}'")
    return Decimal(cell.replace(",", "") or 0)


def convert(export, bank: str = "hdfc") -> tuple[list[dict], dict]:
    """export: an open CSV file (newline=""). Returns (statement lines, counts) or raises ValueError."""
    layout = LAYOUTS[bank]
    at, ended, lines, raw_refs = None, False, [], {}
    counts = {"rows": 0, "credits": 0, "debits": 0, "empty": 0}
    # Row numbers count every row of the file from 1, as Excel shows them.
    for n, row in enumerate(csv.reader(export), 1):
        cells = [cell.strip() for cell in row]
        if at is None:  # still in the preamble: look for the header row
            names = [_norm(cell) for cell in cells]
            if all(_norm(name) in names for name in layout.values()):
                at = {field: names.index(_norm(name)) for field, name in layout.items()}
            continue
        date, narration, ref, debit, credit = (cells[at[f]] if at[f] < len(cells) else "" for f in layout)
        day = _date(date)

        # Where the table stops: a blank or ******* row after the first transaction, or a row with no
        # date and no amounts. What follows is the footer, unless it is dated: then lines were cut off.
        if not any(cell.strip("*") for cell in cells):
            ended = ended or counts["rows"] > 0
            continue
        if ended:
            if day:
                raise ValueError(f"row {n}: a dated row after the end of the table (a blank row or footer "
                                 f"text comes before it). Remove what is in between and import again")
            continue
        if not day and not debit and not credit:
            ended = True
            continue

        counts["rows"] += 1
        out, money = _amount(debit, n, layout["debit"]), _amount(credit, n, layout["credit"])
        if out and money:
            raise ValueError(f"row {n}: both a withdrawal ({debit}) and a deposit ({credit})")
        if not money:
            counts["debits" if out else "empty"] += 1
            continue
        if not day:
            raise ValueError(f"row {n}: credit of {credit} has an unreadable date {date!r} (expected dd/mm/yy)")

        narration = " ".join(narration.split())  # a line break inside the cell becomes one space
        # Leading zeros are padding (0000507296189749) and are always removed, from the column and from
        # the narration alike, so one payment has one txn_id wherever it was read from.
        # shortcut: a cheque number is short and not unique across payers; Fineract then refuses the
        # second one at posting. Add the date or payer to the id if cheque deposits are common.
        raw = ref if ref.strip("0") else ""
        if not raw:
            found = set(REFERENCE.findall(narration))
            raw = found.pop() if len(found) == 1 else ""
        txn_id = raw.lstrip("0")
        if not txn_id:
            raise ValueError(f"row {n}: credit of {credit} has no usable reference: '{layout['ref']}' is "
                             f"{ref!r} and the narration does not hold exactly one number of 12+ digits")
        if raw_refs.setdefault(txn_id, raw) != raw:
            raise ValueError(f"row {n}: references {raw_refs[txn_id]!r} and {raw!r} differ only in leading "
                             f"zeros, so they cannot be told apart once the padding is removed")
        lines.append({"txn_id": txn_id, "date": day, "amount": f"{money:.2f}", "narration": narration})
        counts["credits"] += 1

    if at is None:
        raise ValueError("no header row with the columns " + ", ".join(layout.values()))
    return lines, counts


def main():
    parser = argparse.ArgumentParser(description="Bank CSV export -> data/bank_statement.csv (credits only).")
    parser.add_argument("export", type=Path)
    parser.add_argument("--bank", default="hdfc", choices=sorted(LAYOUTS))
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "bank_statement.csv")
    args = parser.parse_args()
    try:
        # shortcut: UTF-8 only (with or without BOM); add an --encoding option if a bank exports cp1252.
        with args.export.open(newline="", encoding="utf-8-sig") as f:
            lines, counts = convert(f, args.bank)
        if not lines:
            raise ValueError("no credit rows found")
    except ValueError as problem:  # includes a file that is not UTF-8
        sys.exit(f"REFUSED, nothing written. {args.export}: {problem}")
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(lines[0]))
        writer.writeheader()
        writer.writerows(lines)
    print(f"rows read: {counts['rows']} credits kept: {counts['credits']} debits skipped: {counts['debits']}"
          + (f" rows with no amount skipped: {counts['empty']}" if counts["empty"] else ""))
    print(f"{args.bank} export {args.export} -> {args.out}")


if __name__ == "__main__":
    main()
