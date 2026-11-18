"""Turns a bank's CSV export into the statement the matcher reads. convert() is pure: no files.

Run:  python -m statement.import_bank <export.csv> [--bank hdfc] [--out data/bank_statement.csv]
      (--bank icici, sbi or other: the column map confirmed for that file on the page's Statement tab)
      A camt.053 (XML) or MT940 file is told by its content and read by statement.import_structured instead.

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
import io
import json
import re
import sys
from datetime import datetime
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from locale_settings import DEFAULT, Settings, from_env
from matcher.match import FORMATS, NO_REFERENCE
from statement.parse import NUMERIC_DATE, infer_date_order, infer_number_format, parse_amount, sign, with_order

# The export's column headers per bank, found by name (case and spaces ignored) wherever they are.
# One line adds a bank.
LAYOUTS = {
    "hdfc": {"date": "Date", "narration": "Narration", "ref": "Chq./Ref.No.",
             "debit": "Withdrawal Amt.", "credit": "Deposit Amt."},
}
DATE_FORMATS = ("%d/%m/%y", "%d/%m/%Y")  # the HDFC layout's; DATE_ORDER may put the month first
CURRENCY_HEADERS = ("currency", "ccy", "curr", "currencycode")  # a column with one of these names is checked
# shortcut: the fallback only takes an all-digit reference (UPI, IMPS: 12 digits). A NEFT UTR has letters;
# read it from the narration per format if a bank is found to leave the reference column empty for NEFT.
REFERENCE = re.compile(r"(?<![A-Za-z0-9])\d{12,}(?![A-Za-z0-9])")


def _norm(header: str) -> str:
    return "".join(header.lower().split())


def _date(cell: str, formats=DATE_FORMATS, probe: dict | None = None, row: int = 0) -> str:
    """The ISO date, or ''. With `probe` (the pass that works out the date order) a numeric day/month date is
    recorded and stands in as '-': its order is not known yet."""
    if probe is not None and (found := NUMERIC_DATE.fullmatch(cell)):
        probe["dates"].append((int(found[1]), int(found[3]), row, cell))
        return "-"
    for fmt in formats:
        try:
            return datetime.strptime(cell, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def noref_id(day: str, amount: str, narration: str, seen: dict) -> str:
    """The id of a credit with no bank reference. Kept, not refused: the matcher queues every NOREF line for a
    person, who decides it. The id is stable (same line, same id on a re-import) and `seen` (one dict per
    statement) counts identical-looking lines so they stay apart."""
    same = (day, amount, narration)
    seen[same] = seen.get(same, 0) + 1
    digest = hashlib.sha256("|".join((*same, str(seen[same]))).encode()).hexdigest()[:8]
    return f"{NO_REFERENCE}{day}-{digest}"


def _amount(cell: str, row: int, column: str, settings: Settings = DEFAULT, probe: dict | None = None) -> Decimal:
    """An empty cell is 0; a negative amount comes back negative. With `probe` the cell is only recorded (the
    pass that works out the number format) and 0 is returned."""
    if probe is not None:
        probe["amounts"].append(sign(cell)[1])
        return Decimal(0)
    return parse_amount(cell, settings.number_format, settings.minor, settings.country == "India", row, column)


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
        negative, amount = sign(amount)
        kind = "DR" if negative else "CR"
    kind = kind.strip().rstrip(".").upper()
    if kind in CREDIT:
        return "", amount
    if kind in DEBIT:
        return amount, ""
    raise ValueError(f"row {row}: cannot tell whether {cells[0]!r} is a credit or a debit (found {kind!r}, "
                     f"expected Cr or Dr)")


def convert(export, bank: str = "hdfc", cmap: dict | None = None, settings: Settings = DEFAULT) -> tuple[list[dict], dict]:
    """export: an open CSV file (newline=""). Returns (statement lines, counts) or raises ValueError.

    With `cmap` (a confirmed column map) the columns come from it instead of LAYOUTS[bank], and every
    transaction row's date must read with its one date format, not only the credits'.
    `settings` say the date order, number format and currency; where they say "auto" the whole file is read
    once first to work them out, and a file that does not say is refused.
    """
    if "auto" not in (settings.date_order, settings.number_format):
        return _convert(export, bank, cmap, settings)
    text = export.read()
    probe = {"dates": [], "amounts": [], "column": ""}
    _convert(io.StringIO(text, newline=""), bank, cmap, settings, probe)
    order = infer_date_order(probe["dates"], probe["column"]) if settings.date_order == "auto" else settings.date_order
    number = (infer_number_format(probe["amounts"], settings.minor) if settings.number_format == "auto"
              else settings.number_format)
    return _convert(io.StringIO(text, newline=""), bank, cmap, replace(settings, date_order=order, number_format=number))


def _convert(export, bank, cmap, settings, probe=None) -> tuple[list[dict], dict]:
    layout = LAYOUTS[bank] if cmap is None else None
    if cmap is not None:
        name = lambda field: cmap["headers"][cmap[field]] if cmap.get(field, -1) >= 0 else "(none)"  # noqa: E731
        style = cmap["style"]
        money_fields = ("debit", "credit") if style == "two_columns" else ("amount", "type")[:1 + (style == "type_column")]
        debit_name = credit_name = name("amount")
        if style == "two_columns":
            debit_name, credit_name = name("debit"), name("credit")
        formats = (with_order(cmap["date_format"], settings.date_order),)
        expected = datetime(2026, 9, 5).strftime(formats[0])
    else:
        debit_name, credit_name = layout["debit"], layout["credit"]
        formats = tuple(with_order(fmt, settings.date_order) for fmt in DATE_FORMATS)
        expected = "dd/mm/yy" if settings.date_order != "month-first" else "mm/dd/yy"
    currency_at = -1
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
            if at is not None:
                currency_at = next((i for i, name in enumerate(names) if name in CURRENCY_HEADERS), -1)
                if probe is not None:
                    probe["column"] = cmap["headers"][cmap["date"]] if cmap else layout["date"]
            continue
        if cmap is None:
            date, narration, ref, debit, credit = (cells[at[f]] if at[f] < len(cells) else "" for f in layout)
            amounts = (debit, credit)
        else:
            get = lambda field: cells[cmap[field]] if 0 <= cmap.get(field, -1) < len(cells) else ""  # noqa: E731
            date, narration, ref = get("date"), get("narration"), get("reference")
            amounts = tuple(get(field) for field in money_fields)
        day = _date(date, formats, probe, n)

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
        out, money = abs(_amount(debit, n, debit_name, settings, probe)), _amount(credit, n, credit_name, settings, probe)
        if money < 0:
            raise ValueError(f"row {n}: a negative amount ({credit}) in '{credit_name}', which holds credits")
        if out and money:
            raise ValueError(f"row {n}: both a withdrawal ({debit}) and a deposit ({credit})")
        if not money:
            counts["debits" if out else "empty"] += 1
            continue
        if not day:
            raise ValueError(f"row {n}: credit of {credit} has an unreadable date {date!r} (expected {expected})")
        if 0 <= currency_at < len(cells) and cells[currency_at] and cells[currency_at].upper() != settings.currency:
            raise ValueError(f"row {n}: the currency is {cells[currency_at]!r} but CURRENCY is {settings.currency}. The "
                             f"agent handles one currency per run and does not convert: import a statement in "
                             f"{settings.currency} only, or change CURRENCY on the Connect tab")

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
            txn_id = noref_id(day, f"{money:.{settings.minor}f}", narration, seen)
            counts.setdefault("no_reference", []).append(n)
        elif raw_refs.setdefault(txn_id, raw) != raw:
            raise ValueError(f"row {n}: references {raw_refs[txn_id]!r} and {raw!r} differ only in leading "
                             f"zeros, so they cannot be told apart once the padding is removed")
        lines.append({"txn_id": txn_id, "date": day, "amount": f"{money:.{settings.minor}f}", "narration": narration})
        counts["credits"] += 1

    if at is None:
        raise ValueError("no header row with the columns " + ", ".join(layout.values() if cmap is None else cmap["headers"]))
    return lines, counts


def record(out: Path, bank: str):
    """Notes whose narrations this statement holds (statement.json, tied to the file by its hash), so the
    page's Run tab reads them with the right FORMATS entry. A statement with no note is the synthetic one."""
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.parent / "statement.json").write_text(json.dumps({"bank": bank, "sha256": digest}))


def write_statement(out: Path, lines: list[dict], bank: str):
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(lines[0]))
        writer.writeheader()
        writer.writerows(lines)
    record(out, bank)


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
    parser.add_argument("--env", type=Path, default=Path(__file__).resolve().parents[1] / ".env",
                        help="the .env holding the locale settings (currency, date order, number format)")
    args = parser.parse_args()
    from statement import import_structured  # here: it imports this module
    try:
        settings = from_env(args.env)
        # camt.053 and MT940 are told by their content, whatever --bank says; their narrations are read as that format.
        found = import_structured.read_bytes(args.export.read_bytes(), settings)
        if found:
            args.bank, lines, counts = found
            if not lines:
                raise ValueError("no credit entries found")
    except (ValueError, OSError) as problem:
        sys.exit(f"REFUSED, nothing written. {args.export}: {problem}")
    if found:
        write_statement(args.out, lines, args.bank)
        print(f"entries read: {counts['rows']} credits kept: {counts['credits']} debits skipped: {counts['debits']} "
              f"reversals skipped: {counts['reversals']} entries without a reference: {len(counts.get('no_reference', []))}"
              + (f" not yet booked, skipped: {counts['pending']}" if counts["pending"] else "")
              + (f" zero amounts skipped: {counts['empty']}" if counts["empty"] else ""))
        if counts.get("no_reference"):
            print(f"{len(counts['no_reference'])} credits had no bank reference; they will go to the review queue "
                  f"(entries {', '.join(map(str, counts['no_reference']))})")
        print(f"{import_structured.NAMES[args.bank]} {args.export} -> {args.out}")
        return
    try:
        cmap = None
        if args.bank not in LAYOUTS:
            cmap = confirmed(args.export, args.out.parent / "layouts.json")
            if cmap is None:
                raise ValueError(f"no column map is confirmed for this file's layout ({args.bank}). Read it once on "
                                 f"the page's Statement tab (python -m ui.serve) and press 'Looks right, import it'")
        # shortcut: UTF-8 only (with or without BOM); add an --encoding option if a bank exports cp1252.
        with args.export.open(newline="", encoding="utf-8-sig") as f:
            lines, counts = convert(f, args.bank if cmap is None else "hdfc", cmap, settings)
        if not lines:
            raise ValueError("no credit rows found")
    except ValueError as problem:  # includes a file that is not UTF-8
        sys.exit(f"REFUSED, nothing written. {args.export}: {problem}")
    write_statement(args.out, lines, args.bank)
    print(f"rows read: {counts['rows']} credits kept: {counts['credits']} debits skipped: {counts['debits']}"
          + (f" rows with no amount skipped: {counts['empty']}" if counts["empty"] else ""))
    if counts.get("no_reference"):
        print(f"{len(counts['no_reference'])} credits had no bank reference; they will go to the review queue "
              f"(rows {', '.join(map(str, counts['no_reference']))})")
    print(f"{args.bank} export {args.export} -> {args.out}")


if __name__ == "__main__":
    main()
