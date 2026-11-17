"""Turns a bank's CSV export into the statement the matcher reads. convert() is pure: no files.

Run:  python -m statement.import_bank <export.csv> [--bank hdfc] [--out data/bank_statement.csv]
      (--bank icici, sbi or other: the column map confirmed for that file on the page's Statement tab)

Writes `txn_id, date, amount, narration`, credits only, in the export's order. It never drops a money
line quietly: a credit whose amount or date it cannot read stops the import with the row number, and
nothing is written. A credit with no bank reference is kept under a NOREF- id and goes to the review queue.
Rules: docs/superpowers/specs/2026-11-16-bank-import-design.md.

The HDFC layout comes from secondary sources and no real export was used: unverified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from matcher.match import FORMATS, NO_REFERENCE

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


def _date(cell: str, formats=DATE_FORMATS) -> str:
    for fmt in formats:
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


# A column map says where a bank's columns are, for a layout not in LAYOUTS (ai.layout proposes one, a
# person confirms it): {"headers": [...the header row...], "date": i, "date_format": "%d-%m-%Y",
# "narration": i, "reference": i or -1, "style": one of STYLES, "credit"/"debit"/"amount"/"type": i or -1}.
STYLES = {"two_columns": "a Debit column and a Credit column",
          "type_column": "one Amount column and a column saying Dr or Cr",
          "suffix": "one Amount column ending in Dr or Cr (8,450.00 Cr)",
          "signed": "one Amount column, debits with a minus sign"}
CREDIT, DEBIT = ("CR", "C", "CREDIT"), ("DR", "D", "DEBIT")


def _split(cells: tuple, cmap: dict, row: int) -> tuple[str, str]:
    """(debit, credit) text from a mapped row's amount cells, as the two-column layout has them."""
    if cmap["style"] == "two_columns":
        return cells
    amount, kind = cells[0], cells[1] if len(cells) > 1 else ""
    if cmap["style"] == "suffix":
        found = re.fullmatch(r"(.*?)\s*\(?(CR|DR)\)?\.?", amount, re.IGNORECASE)
        amount, kind = (found.group(1), found.group(2)) if found else (amount, "")
    elif cmap["style"] == "signed":
        kind = "DR" if amount.startswith("-") else "CR"
        amount = amount[1:].strip() if amount[:1] in "+-" else amount
    kind = kind.strip().rstrip(".").upper()
    if kind in CREDIT:
        return "", amount
    if kind in DEBIT:
        return amount, ""
    raise ValueError(f"row {row}: cannot tell whether {cells[0]!r} is a credit or a debit (found {kind!r}, "
                     f"expected Cr or Dr)")


def convert(export, bank: str = "hdfc", cmap: dict | None = None) -> tuple[list[dict], dict]:
    """export: an open CSV file (newline=""). Returns (statement lines, counts) or raises ValueError.

    With `cmap` (a confirmed column map) the columns come from it instead of LAYOUTS[bank], and every
    transaction row's date must read with its one date format, not only the credits'.
    """
    layout = LAYOUTS[bank] if cmap is None else None
    if cmap is not None:
        name = lambda field: cmap["headers"][cmap[field]] if cmap.get(field, -1) >= 0 else "(none)"  # noqa: E731
        style = cmap["style"]
        money_fields = ("debit", "credit") if style == "two_columns" else ("amount", "type")[:1 + (style == "type_column")]
        debit_name = credit_name = name("amount")
        if style == "two_columns":
            debit_name, credit_name = name("debit"), name("credit")
        formats = (cmap["date_format"],)
        expected = datetime(2026, 9, 5).strftime(cmap["date_format"])
    else:
        debit_name, credit_name, formats, expected = layout["debit"], layout["credit"], DATE_FORMATS, "dd/mm/yy"
    at, ended, lines, raw_refs, seen = None, False, [], {}, {}
    counts = {"rows": 0, "credits": 0, "debits": 0, "empty": 0}
    # Row numbers count every row of the file from 1, as Excel shows them.
    for n, row in enumerate(csv.reader(export), 1):
        cells = [cell.strip() for cell in row]
        if at is None:  # still in the preamble: look for the header row
            names = [_norm(cell) for cell in cells]
            if cmap is not None:
                at = cmap if names == [_norm(h) for h in cmap["headers"]] else None
            elif all(_norm(name) in names for name in layout.values()):
                at = {field: names.index(_norm(name)) for field, name in layout.items()}
            continue
        if cmap is None:
            date, narration, ref, debit, credit = (cells[at[f]] if at[f] < len(cells) else "" for f in layout)
            amounts = (debit, credit)
        else:
            get = lambda field: cells[cmap[field]] if 0 <= cmap.get(field, -1) < len(cells) else ""  # noqa: E731
            date, narration, ref = get("date"), get("narration"), get("reference")
            amounts = tuple(get(field) for field in money_fields)
        day = _date(date, formats)

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
        if not day and not any(amounts[:1 if cmap is not None and style == "type_column" else 2]):
            ended = True
            continue

        counts["rows"] += 1
        if cmap is not None:
            if not day:
                raise ValueError(f"row {n}: unreadable date {date!r} in '{name('date')}' (expected like {expected})")
            debit, credit = _split(amounts, cmap, n)
        out, money = _amount(debit, n, debit_name), _amount(credit, n, credit_name)
        if out and money:
            raise ValueError(f"row {n}: both a withdrawal ({debit}) and a deposit ({credit})")
        if not money:
            counts["debits" if out else "empty"] += 1
            continue
        if not day:
            raise ValueError(f"row {n}: credit of {credit} has an unreadable date {date!r} (expected {expected})")

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
            # Kept, not refused: the matcher queues every NOREF line for a person, who decides it. The id is
            # stable (same row, same id on a re-import) and the position keeps identical-looking rows apart.
            same = (day, f"{money:.2f}", narration)
            seen[same] = seen.get(same, 0) + 1
            digest = hashlib.sha256("|".join((*same, str(seen[same]))).encode()).hexdigest()[:8]
            txn_id = f"{NO_REFERENCE}{day}-{digest}"
            counts.setdefault("no_reference", []).append(n)
        elif raw_refs.setdefault(txn_id, raw) != raw:
            raise ValueError(f"row {n}: references {raw_refs[txn_id]!r} and {raw!r} differ only in leading "
                             f"zeros, so they cannot be told apart once the padding is removed")
        lines.append({"txn_id": txn_id, "date": day, "amount": f"{money:.2f}", "narration": narration})
        counts["credits"] += 1

    if at is None:
        raise ValueError("no header row with the columns " + ", ".join(layout.values() if cmap is None else cmap["headers"]))
    return lines, counts


def record(out: Path, bank: str):
    """Notes whose narrations this statement holds (statement.json, tied to the file by its hash), so the
    page's Run tab reads them with the right FORMATS entry. A statement with no note is the synthetic one."""
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.parent / "statement.json").write_text(json.dumps({"bank": bank, "sha256": digest}))


def recorded_bank(out: Path) -> str:
    try:
        note = json.loads((out.parent / "statement.json").read_text())
        if note["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest() and note["bank"] in FORMATS:
            return note["bank"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return "synthetic"


def confirmed(export_path: Path, layouts_file: Path) -> dict | None:
    """The column map a person confirmed for this file's header row (ai.layout.save), or None."""
    from ai.layout import saved  # here: the HDFC path needs nothing from ai/
    return saved(export_path.read_text(encoding="utf-8-sig"), layouts_file)


def main():
    parser = argparse.ArgumentParser(description="Bank CSV export -> data/bank_statement.csv (credits only).")
    parser.add_argument("export", type=Path)
    parser.add_argument("--bank", default="hdfc", choices=sorted(FORMATS),
                        help="hdfc reads HDFC's columns; any other uses the column map confirmed on the Statement tab")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "bank_statement.csv")
    args = parser.parse_args()
    try:
        cmap = None
        if args.bank not in LAYOUTS:
            cmap = confirmed(args.export, args.out.parent / "layouts.json")
            if cmap is None:
                raise ValueError(f"no column map is confirmed for this file's layout ({args.bank}). Read it once on "
                                 f"the page's Statement tab (python -m ui.serve) and press 'Looks right, import it'")
        # shortcut: UTF-8 only (with or without BOM); add an --encoding option if a bank exports cp1252.
        with args.export.open(newline="", encoding="utf-8-sig") as f:
            lines, counts = convert(f, args.bank if cmap is None else "hdfc", cmap)
        if not lines:
            raise ValueError("no credit rows found")
    except ValueError as problem:  # includes a file that is not UTF-8
        sys.exit(f"REFUSED, nothing written. {args.export}: {problem}")
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(lines[0]))
        writer.writeheader()
        writer.writerows(lines)
    record(args.out, args.bank)
    print(f"rows read: {counts['rows']} credits kept: {counts['credits']} debits skipped: {counts['debits']}"
          + (f" rows with no amount skipped: {counts['empty']}" if counts["empty"] else ""))
    if counts.get("no_reference"):
        print(f"{len(counts['no_reference'])} credits had no bank reference; they will go to the review queue "
              f"(rows {', '.join(map(str, counts['no_reference']))})")
    print(f"{args.bank} export {args.export} -> {args.out}")


if __name__ == "__main__":
    main()
