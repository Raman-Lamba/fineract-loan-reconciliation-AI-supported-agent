"""How a statement writes dates and amounts, per the locale settings. Pure: no files.

Dates: a numeric day/month date is read in the order the settings say; with "auto" the order is worked out
from every date in the file, and a file that does not say is refused (never guessed).
Amounts: thousands and decimal marks per NUMBER_FORMAT, with "auto" worked out from the whole amount column.
"""
from __future__ import annotations

import re
from decimal import Decimal

# Every date format a column map may name. The settings' DATE_ORDER decides which of day and month comes first
# in the numeric ones (with_order), so the month-first spellings below are the same dates under another order.
DATE_FORMATS = ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d-%b-%Y", "%d %b %y",
                "%d-%b-%y", "%d/%b/%Y", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%Y/%m/%d", "%d.%m.%y",
                "%d %B %Y", "%d-%B-%Y", "%b %d, %Y", "%B %d, %Y")
NUMERIC_DATE = re.compile(r"(\d{1,2})([/.-])(\d{1,2})\2(\d{2}|\d{4})")


def with_order(fmt: str, order: str) -> str:
    """`fmt` with day and month in the given order ('day-first' or 'month-first'); other formats unchanged."""
    if order not in ("day-first", "month-first") or "%d" not in fmt or "%m" not in fmt or fmt.startswith("%Y"):
        return fmt
    if (fmt.index("%d") < fmt.index("%m")) == (order == "day-first"):
        return fmt
    return fmt.replace("%d", "\0").replace("%m", "%d").replace("\0", "%m")


def infer_date_order(dates: list[tuple], column: str) -> str:
    """'day-first' or 'month-first' from the numeric dates seen: (first part, second part, row, text).

    A first part above 12 can only be a day, a second part above 12 only a day too. All 12 or below: no answer.
    """
    day = next((d for d in dates if d[0] > 12), None)
    month = next((d for d in dates if d[1] > 12), None)
    if day and month:
        raise ValueError(f"the dates in '{column}' are written both ways: row {day[2]} ({day[3]}) can only be day-first "
                         f"and row {month[2]} ({month[3]}) only month-first. Fix the file, or choose the date order "
                         f"on the Statement tab to see which rows it refuses")
    if day:
        return "day-first"
    if month:
        return "month-first"
    if dates:
        raise ValueError(f"cannot tell whether the dates in '{column}' are day-first or month-first: all {len(dates)} "
                         f"have both numbers 12 or below (row {dates[0][2]}: {dates[0][3]}). Choose the date order "
                         f"on the Statement tab; the agent does not guess")
    return "day-first"  # no numeric day/month dates in the file: the order is not used


# --- amounts ---------------------------------------------------------------------------------------------

DECIMAL_MARK = {"1,234.56": ".", "1.234,56": ",", "1 234,56": ",", "1'234.56": "."}
THOUSANDS = {"1,234.56": ",", "1.234,56": r"\.", "1 234,56": "[ \u00a0\u202f]", "1'234.56": "['\u2019]"}
MINUS = "-\u2212"


def sign(text: str, suffix: bool = False) -> tuple[bool, str]:
    """(is it negative, the digits and marks): (1,234.56), -1.234,56 and 1,234.56- are negative.

    With `suffix`, a trailing Cr or Dr is read too (Dr is negative); otherwise it is left in the text, where
    it makes the amount unreadable.
    """
    text, negative = text.strip(), False
    found = suffix and re.fullmatch(r"(.*?)\s*\(?(CR|DR)\)?\.?", text, re.IGNORECASE)
    if found:
        text, negative = found.group(1).strip(), found.group(2).upper() == "DR"
    if text.startswith("(") and text.endswith(")"):
        text, negative = text[1:-1].strip(), True
    if text and text[0] in MINUS:
        text, negative = text[1:].strip(), True
    elif text and text[-1] in MINUS:
        text, negative = text[:-1].strip(), True
    elif text[:1] == "+":
        text = text[1:].strip()
    return negative, text


_patterns: dict = {}


def _pattern(fmt: str, digits: int, indian: bool):
    key = (fmt, digits, indian)
    if key not in _patterns:
        groups = rf"\d+|\d{{1,3}}(?:{THOUSANDS[fmt]}\d{{3}})+"
        if indian and fmt == "1,234.56":
            groups += r"|\d{1,2}(?:,\d{2})*,\d{3}"  # 1,21,940
        fraction = rf"(?:{re.escape(DECIMAL_MARK[fmt])}\d{{1,{digits}}})?" if digits else ""
        _patterns[key] = re.compile(rf"(?:{groups}){fraction}")
    return _patterns[key]


def parse_amount(cell: str, fmt: str, digits: int, indian: bool, row: int, column: str, suffix: bool = False) -> Decimal:
    """The amount in a cell (negative if marked so); an empty cell is 0. ValueError names the row and column.

    A Cr or Dr suffix is read only where the layout says amounts carry one (`suffix`): elsewhere it is not an amount.
    """
    cell = cell.strip()
    if not cell:
        return Decimal(0)
    negative, body = sign(cell, suffix)
    if not _pattern(fmt, digits, indian).fullmatch(body):
        raise ValueError(f"row {row}: cannot read the amount {cell!r} in '{column}' (number format {fmt}, "
                         f"{digits} decimals)")
    value = Decimal(re.sub(THOUSANDS[fmt], "", body).replace(DECIMAL_MARK[fmt], "."))
    return -value if negative else value


def infer_number_format(bodies: list[str], digits: int) -> str:
    """The number format the whole amount column is written in, or ValueError if it does not say.

    A value with both a comma and a dot shows which is the decimal mark; a space or an apostrophe between
    digits is a thousands mark; a mark that repeats is a thousands mark; one mark followed by 1 or 2 digits is
    a decimal mark. One mark followed by exactly 3 digits (1.234) could be either: if nothing else in the
    column decides, it is refused.
    """
    strong, marks, unclear = set(), set(), []  # whole formats seen; decimal marks seen alone; the 3-digit cases
    for body in bodies:
        if re.search(r"['\u2019]", body):
            strong.add("1'234.56")
        elif re.search(r"[ \u00a0\u202f]", body):
            strong.add("1 234,56")
        elif "," in body and "." in body:
            strong.add("1,234.56" if body.rindex(".") > body.rindex(",") else "1.234,56")
        elif "," in body or "." in body:
            mark = "," if "," in body else "."
            after = len(body) - body.index(mark) - 1
            if body.count(mark) > 1:
                strong.add("1,234.56" if mark == "," else "1.234,56")
            elif after == 3:
                unclear.append(body)
            else:
                marks.add(mark)  # a decimal mark; the thousands mark is not shown
    if len(strong) > 1 or len(marks) > 1 or (strong and marks and {DECIMAL_MARK[f] for f in strong} != marks):
        raise ValueError("the amounts are written in more than one style (" + " and ".join(sorted(strong) + sorted(
            f"a {mark!r} decimal mark" for mark in marks)) + "). Choose the number format on the Statement tab to see "
                         "which rows it refuses")
    if strong:
        return next(iter(strong))
    if marks:
        return "1.234,56" if "," in marks else "1,234.56"
    if unclear:
        raise ValueError(f"cannot tell the number format: every amount with a mark in it looks like {unclear[0]!r}, which "
                         f"is either thousands (1234) or a fraction (1.234). Choose the number format on the "
                         f"Statement tab; the agent does not guess")
    return "1,234.56"  # no separators anywhere: every format reads the same
