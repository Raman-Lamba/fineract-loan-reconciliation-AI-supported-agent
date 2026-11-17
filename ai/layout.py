"""Finds a bank export's columns: where the header row is, which column is the date, the narration, the
reference and the money, and how amounts are written. Proposes a column map; a person confirms it.

In order: the HDFC layout by its header names; a map confirmed earlier for the same header row
(data/layouts.json, keyed by a hash of that row); then, only if the AI reader is on, the model.

What the model sees: the first SAMPLE rows with every digit turned into 9, so no amount, balance, date
value or account number leaves the machine; the column names and the narration words do.
Whatever it answers is checked by check() against the whole file before anyone is asked to confirm it.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path

from ai.client import request
from locale_settings import DEFAULT, Settings
from statement.import_bank import LAYOUTS, STYLES, _norm, convert
from statement.parse import DATE_FORMATS  # whether a numeric one is day-first is the DATE_ORDER setting's call, not the model's

SAMPLE = 20  # rows sent to the model
FIELDS = {"date": "date", "narration": "narration (description)", "reference": "reference",
          "credit": "credit (deposit)", "debit": "debit (withdrawal)", "amount": "amount", "type": "Dr/Cr"}
USES = {"two_columns": ("credit", "debit"), "type_column": ("amount", "type"), "suffix": ("amount",),
        "signed": ("amount",)}
NEEDS = {"two_columns": ("credit",), "type_column": ("amount", "type"), "suffix": ("amount",), "signed": ("amount",)}

SYSTEM = ("You read the layout of a bank statement exported as CSV. Every digit in it has been replaced by 9 "
          "before you see it. Answer only with the column map asked for; never guess a column that is not there: "
          "use -1.")
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["header_row", "date", "date_format", "narration", "reference", "style", "credit", "debit",
                       "amount", "type"],
          "properties": {"header_row": {"type": "integer"}, "date": {"type": "integer"},
                         "date_format": {"type": "string", "enum": list(DATE_FORMATS)},
                         "narration": {"type": "integer"}, "reference": {"type": "integer"},
                         "style": {"type": "string", "enum": list(STYLES)}, "credit": {"type": "integer"},
                         "debit": {"type": "integer"}, "amount": {"type": "integer"}, "type": {"type": "integer"}}}


class LayoutError(Exception):
    """The map does not fit the file. The message says why in plain words."""


def rows_of(text: str) -> list[list[str]]:
    return [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text, newline=""))]


def header_key(cells: list[str]) -> str:
    return hashlib.sha256("\x1f".join(_norm(cell) for cell in cells).encode("utf-8")).hexdigest()


def shape(rows: list[list[str]]) -> list[list[str]]:
    """The first SAMPLE rows as the model may see them: digits masked, long cells cut."""
    return [[re.sub(r"\d", "9", cell)[:80] for cell in row[:20]] for row in rows[:SAMPLE]]


def ask(rows: list[list[str]], send) -> dict:
    """The model's proposed map (unchecked). `send` is ai.client.sender(...) or a fake."""
    sample = "\n".join(f"row {i}: {json.dumps(row, ensure_ascii=False)}" for i, row in enumerate(shape(rows)))
    prompt = (
        "These are the first rows of a bank account statement CSV (row numbers from 0; column numbers from 0 "
        "within the header row). There may be account details above the table.\n\n" + sample + "\n\n"
        "Give: header_row (the row holding the column names); the column numbers of date (the transaction "
        "date, not the value date), narration (the description or remarks text), reference (cheque or "
        "reference number, -1 if none); date_format as a strftime pattern from the list; and how money is "
        "written, as style: two_columns (separate debit/withdrawal and credit/deposit columns: give debit and "
        "credit), type_column (one amount column and a column saying Dr or Cr: give amount and type), suffix "
        "(one amount column whose values end in Dr or Cr: give amount), or signed (one amount column, debits "
        "negative: give amount). Use -1 for every column that does not apply. Never pick a balance column.")
    return send(request(SYSTEM, prompt, SCHEMA, max_tokens=4000))


def check(raw, text: str, settings: Settings = DEFAULT) -> tuple[dict, list[dict], dict]:
    """(column map, statement lines, counts) if `raw` fits the whole file, else LayoutError.

    Every index must be a column of the header row, the date format one we know, and every date and amount
    in the file must read with the map: the import itself is run.
    """
    if not isinstance(raw, dict):
        raise LayoutError("the answer was not a column map")
    rows = rows_of(text)
    at = raw.get("header_row")
    if type(at) is not int or not 0 <= at < len(rows) or not any(rows[at]):
        raise LayoutError(f"row {at!r} is not a row of column names in this file")
    headers, style = rows[at], raw.get("style")
    if style not in STYLES:
        raise LayoutError(f"'{style}' is not a way of writing amounts this importer knows")
    if raw.get("date_format") not in DATE_FORMATS:
        raise LayoutError(f"the date format {raw.get('date_format')!r} is not one this importer reads")
    cmap = {"headers": headers, "date_format": raw["date_format"], "style": style}
    for field, label in FIELDS.items():
        index = raw.get(field, -1)
        if type(index) is not int or not -1 <= index < len(headers):
            raise LayoutError(f"the {label} column ({index!r}) is not one of the file's {len(headers)} columns")
        if index == -1 and field in ("date", "narration") + NEEDS[style]:
            raise LayoutError(f"no {label} column was given")
        cmap[field] = index if field in ("date", "narration", "reference") + USES[style] else -1
    used = [cmap[field] for field in FIELDS if cmap[field] >= 0]
    if len(used) != len(set(used)):
        raise LayoutError("one column is given for two different things")
    try:
        lines, counts = convert(io.StringIO(text, newline=""), cmap=cmap, settings=settings)
    except ValueError as problem:
        raise LayoutError(str(problem)) from None
    if not lines:
        raise LayoutError("with these columns the file has no credit rows")
    return {**cmap, "header_row": at}, lines, counts


def hdfc_map(rows: list[list[str]]) -> dict | None:
    """The HDFC layout as a column map (for the page's dropdowns), or None if the header is not there."""
    layout = LAYOUTS["hdfc"]
    for at, row in enumerate(rows):
        names = [_norm(cell) for cell in row]
        if all(_norm(name) in names for name in layout.values()):
            index = {field: names.index(_norm(name)) for field, name in layout.items()}
            return {"header_row": at, "date": index["date"], "date_format": "%d/%m/%y", "narration": index["narration"],
                    "reference": index["ref"], "style": "two_columns", "credit": index["credit"],
                    "debit": index["debit"], "amount": -1, "type": -1}
    return None


def _load(layouts_file: Path) -> dict:
    try:
        return json.loads(layouts_file.read_text())
    except (OSError, ValueError):
        return {}


def saved(text: str, layouts_file: Path) -> dict | None:
    """The map confirmed earlier for a header row in this file, or None. Costs nothing: no model call."""
    layouts = _load(layouts_file)
    for at, row in enumerate(rows_of(text)[:60]):
        if any(row) and header_key(row) in layouts:
            return {**layouts[header_key(row)], "header_row": at}
    return None


def save(cmap: dict, layouts_file: Path):
    """Remembers a confirmed map, so the same bank format is never asked about again."""
    layouts = _load(layouts_file)
    layouts[header_key(cmap["headers"])] = {key: value for key, value in cmap.items() if key != "header_row"}
    layouts_file.write_text(json.dumps(layouts, indent=1))
