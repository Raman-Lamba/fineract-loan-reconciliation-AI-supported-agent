"""Reads the two standard international statement formats into the statement the matcher reads. Pure: no files.

ISO 20022 camt.053 (XML) and SWIFT MT940 (text) give the same `txn_id, date, amount, narration` lines as
statement.import_bank's CSV path: credits only, in the file's order. The narration is written as
`payer name | remittance | account | channel` from the file's own fields, so matcher.match.FORMATS "camt053" and
"mt940" read the payer's name from position 0 (empty when the file names no payer). The format is told by the
content, never by the file name; statement.import_bank and the page's Statement tab call read().

It never drops money quietly: debits, reversals and entries not yet booked are skipped and counted, and an entry
it cannot read stops the import with its number. A credit with no reference gets a NOREF- id (the CSV importer's
own) and goes to the review queue. Rules: docs/superpowers/specs/2026-11-18-camt-mt940-design.md.

Every format detail comes from the public specifications as remembered, with no real bank file: UNVERIFIED.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

from locale_settings import DEFAULT, Settings
from posting.post import channel
from statement.import_bank import noref_id

MAX_BYTES = 20_000_000  # a year of a busy account in camt.053 is a few MB
MAX_ENTRIES = 100_000
SUPPORTED = "CSV (any layout), ISO 20022 camt.053 (XML) and SWIFT MT940"
NAMES = {"camt053": "camt.053 (ISO 20022 XML)", "mt940": "MT940 (SWIFT)"}
NO_REF = ("NOTPROVIDED", "NONREF")  # what the two formats write in a reference field that has no reference
# ISO bank transaction sub-family codes that name a channel. UNVERIFIED beyond ESCT (SEPA credit transfer).
ISO_CHANNEL = {"ESCT": "SEPA", "ESDD": "SEPA"}


def detect(text: str) -> str:
    """'camt053', 'mt940' or 'csv'. ValueError for XML that may not be read or is not a camt.053."""
    if text.lstrip("\ufeff \t\r\n").startswith("<"):
        # Refused before any parser sees it: a statement never needs one, and they carry the entity-expansion
        # (billion laughs) and external-entity attacks.
        if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.IGNORECASE):
            raise ValueError("the XML declares a DOCTYPE or ENTITY, which a bank statement never has (it can be used "
                             "to attack the program reading it). Nothing was read")
        kind = re.search(r"<(?:[\w.-]+:)?(BkToCstmr\w+)", text)
        kind = kind and kind.group(1)
        if kind == "BkToCstmrStmt":
            return "camt053"
        what = {"BkToCstmrAcctRpt": "a camt.052 account report (intraday)",
                "BkToCstmrDbtCdtNtfctn": "a camt.054 debit/credit notification"}.get(kind, "an XML file but not a camt.053 statement")
        raise ValueError(f"this is {what}. Supported: {SUPPORTED}. Ask your bank for the camt.053 end-of-day statement")
    if re.search(r"(^|\{4:):20:", text, re.MULTILINE) and re.search(r"^:61:", text, re.MULTILINE):
        return "mt940"
    return "csv"


def read(text: str, settings: Settings = DEFAULT) -> tuple[str, list[dict], dict] | None:
    """(format, statement lines, counts) for camt.053 or MT940 text; None for anything else (a CSV).
    Raises ValueError, in plain words naming the entry, for a file it refuses."""
    fmt = detect(text)
    if fmt == "csv":
        return None
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError(f"the file is over {MAX_BYTES // 1_000_000} MB: export a shorter period")
    entries = _camt(text) if fmt == "camt053" else _mt940(text)
    return (fmt, *_lines(entries, settings))


def read_bytes(data: bytes, settings: Settings = DEFAULT):
    """read() for a file's bytes (the command line). None for a CSV, which the caller reads its own way."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # shortcut: German banks write MT940 in Latin-1; read it so if the file is MT940, else leave it to the
        # CSV path (which refuses non-UTF-8). Add an --encoding option if a bank uses another code page.
        text = data.decode("latin-1")
        if detect(text) != "mt940":
            return None
    return read(text, settings)


# ---- shared: entries -> statement lines ---------------------------------------------------------------------

def _narration(name: str, remittance: str, account: str, code: str) -> str:
    """`name | remittance | account | channel`; a '|' inside a field becomes '/', so the positions hold."""
    fields = [" ".join(str(f or "").replace("|", "/").split()) for f in (name, remittance, account, code)]
    while len(fields) > 1 and not fields[-1]:
        fields.pop()
    return " | ".join(fields).strip()


def _lines(entries, settings: Settings) -> tuple[list[dict], dict]:
    """entries: dicts with n, where, kind (credit, debits, reversals, pending), date, amount ('1234.56'),
    currency, refs (best first), name, remittance, account, codes (text that may name a channel)."""
    lines, seen = [], {}
    counts = {"rows": 0, "credits": 0, "debits": 0, "empty": 0, "reversals": 0, "pending": 0}
    for e in entries:
        counts["rows"] += 1
        if counts["rows"] > MAX_ENTRIES:
            raise ValueError(f"the file has more than {MAX_ENTRIES} entries: export a shorter period")
        if e["kind"] != "credit":
            counts[e["kind"]] += 1
            continue
        where = e["where"]
        if not re.fullmatch(r"\d+(\.\d+)?", e["amount"]):
            raise ValueError(f"{where}: cannot read the amount {e['amount']!r}")
        money = Decimal(e["amount"])
        if not money:
            counts["empty"] += 1
            continue
        if money != money.quantize(Decimal(1).scaleb(-settings.minor)):
            raise ValueError(f"{where}: the amount {e['amount']} has more decimals than {settings.currency} has "
                             f"({settings.minor})")
        if (e["currency"] or "").upper() != settings.currency:
            raise ValueError(f"{where}: the currency is {e['currency'] or '(not given)'!r} but CURRENCY is "
                             f"{settings.currency}. The agent handles one currency per run and does not convert: import a "
                             f"statement in {settings.currency} only, or change CURRENCY on the Connect tab")
        try:
            day = date.fromisoformat(e["date"]).isoformat()
        except (TypeError, ValueError):
            raise ValueError(f"{where}: credit of {e['amount']} has no readable booking or value date") from None
        found = channel(" ".join(e["codes"]), settings.channels)
        narration = _narration(e["name"], e["remittance"], e["account"], found)
        amount = f"{money:.{settings.minor}f}"
        ref = next((r.strip() for r in e["refs"] if r and r.strip() and r.strip().upper() not in NO_REF), "")
        # shortcut: a reference becomes Fineract's external id and part of a URL path, so anything but letters,
        # digits and . _ - turns into '-'. Two references differing only there would then count as one (the second
        # is ignored as a repeat, never posted twice); keep the raw one in another column if that is ever seen.
        txn_id = re.sub(r"[^A-Za-z0-9._-]", "-", ref)
        if not txn_id:
            txn_id = noref_id(day, amount, narration, seen)
            counts.setdefault("no_reference", []).append(e["n"])
        lines.append({"txn_id": txn_id, "date": day, "amount": amount, "narration": narration})
        counts["credits"] += 1
    return lines, counts


# ---- camt.053 -----------------------------------------------------------------------------------------------

def _text(element, *paths) -> str:
    """The first of these paths with text, stripped, or ''."""
    for path in paths:
        found = element.findtext(path) if element is not None else None
        if found and found.strip():
            return found.strip()
    return ""


def _camt(text: str):
    # detect() refused any DOCTYPE or ENTITY before this; ElementTree never fetches anything from the network.
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text.lstrip("\ufeff"))  # the text is decoded already
    try:
        root = ET.fromstring(text)
    except ET.ParseError as problem:
        raise ValueError(f"the XML cannot be read ({problem})") from None
    for element in root.iter():  # namespaces differ by version (camt.053.001.02 to .12): match local names only
        if isinstance(element.tag, str):
            element.tag = element.tag.rpartition("}")[2]
    n = 0
    for statement in root.iter("Stmt"):
        sid = _text(statement, "Id") or "?"
        account_ccy = _text(statement, "Acct/Ccy")
        for entry in statement.findall("Ntry"):
            n += 1
            where = f"entry {n} (statement {sid}, reference {_text(entry, 'AcctSvcrRef') or 'none'})"
            mark, status = _text(entry, "CdtDbtInd"), _text(entry, "Sts/Cd", "Sts")
            if _text(entry, "RvslInd").lower() in ("true", "1"):
                kind = "reversals"
            elif status and status != "BOOK":
                kind = "pending"  # PDNG or INFO: not booked yet, so not money on the account yet
            elif mark in ("CRDT", "DBIT"):
                kind = "credit" if mark == "CRDT" else "debits"
            else:
                raise ValueError(f"{where}: cannot tell whether it is a credit or a debit (CdtDbtInd {mark!r})")
            amount = entry.find("Amt")
            txs = entry.findall("NtryDtls/TxDtls")
            tx = txs[0] if len(txs) == 1 else None
            codes = [_text(entry, "BkTxCd/Prtry/Cd"), _text(tx, "BkTxCd/Prtry/Cd"), _text(entry, "AddtlNtryInf"),
                     ISO_CHANNEL.get(_text(entry, "BkTxCd/Domn/Fmly/SubFmlyCd"), "")]
            if len(txs) > 1:
                # shortcut: a batch booking (several payments in one entry) is kept as one line with no payer, so it
                # goes to a person; split it per TxDtls if a lender's bank books its incoming payments that way.
                name, account = "", ""
                remittance = f"BATCH OF {len(txs)} PAYMENTS {_text(entry, 'AddtlNtryInf')}"
                refs = [_text(entry, "AcctSvcrRef")]
            else:
                name = _text(tx, "RltdPties/Dbtr/Nm", "RltdPties/Dbtr/Pty/Nm")
                account = _text(tx, "RltdPties/DbtrAcct/Id/IBAN", "RltdPties/DbtrAcct/Id/Othr/Id")
                parts = [] if tx is None else [p.text.strip() for p in (*tx.findall("RmtInf/Ustrd"),
                                                                         *tx.findall("RmtInf/Strd/CdtrRefInf/Ref")) if p.text]
                remittance = " ".join(parts) or _text(tx, "AddtlTxInf") or _text(entry, "AddtlNtryInf")
                refs = [_text(entry, "AcctSvcrRef"), _text(tx, "Refs/AcctSvcrRef"), _text(tx, "Refs/TxId"),
                        _text(tx, "Refs/EndToEndId")]
            yield {"n": n, "where": where, "kind": kind,
                   "date": (_text(entry, "BookgDt/Dt", "BookgDt/DtTm", "ValDt/Dt", "ValDt/DtTm"))[:10],
                   "amount": (amount.text or "").strip() if amount is not None else "",
                   "currency": (amount.get("Ccy") if amount is not None else "") or account_ccy,
                   "refs": refs, "name": name, "remittance": remittance, "account": account, "codes": codes}


# ---- MT940 --------------------------------------------------------------------------------------------------

TAG = re.compile(r":(\d{2}[A-Z]?):(.*)")
# :61: value date YYMMDD, booking date MMDD (optional), mark (RC/RD: reversals), funds code letter (optional),
# amount with a comma, type (N + 3 characters, or S/F), customer reference, //bank reference.
LINE_61 = re.compile(r"(\d{6})(\d{4})?(RC|RD|C|D)([A-Z])?(\d+,\d*)([NSF][A-Z0-9]{3})([^/]*(?:/(?!/)[^/]*)*)(?://(.*))?")
SEPA_KEYS = re.compile(r"(?<=\S)(?=(?:EREF|KREF|MREF|CRED|DEBT|SVWZ|ABWA|ABWE|IBAN|BIC)\+)")


def _fields(text: str):
    """(tag, [lines]) in file order; '-' marks the end of a message. SWIFT {1:..}{2:..}{4: blocks are skipped."""
    fields = []
    for raw in text.replace("{4:", "{4:\n").splitlines():
        line = raw.rstrip()
        found = TAG.fullmatch(line)
        if found:
            fields.append((found.group(1), [found.group(2)]))
        elif line.startswith(("-", "{")) or line.endswith("{4:"):
            fields.append(("-", []))
        elif fields and line:
            fields[-1][1].append(line)
    return fields


def _details(lines: list[str]) -> tuple[str, str, str, str]:
    """(name, remittance, account, booking text) from an :86: field.

    The German `?nn` subfield style (a 3-digit business code, then ?00 booking text, ?20-?29 and ?60-?63
    remittance, ?31 payer account, ?32-?33 payer name) is read field by field. UNVERIFIED subfield numbers,
    from the DFÜ/DK format description as remembered. Anything else (plain text, the `/NAME/..` style of some
    Dutch and Belgian banks) is kept as free text with no payer name: the AI reader or a person reads it.
    """
    joined = "".join(lines)
    if not re.match(r"\d{3}\?\d{2}", joined):
        return "", " ".join(lines), "", ""
    sub: dict = {}
    parts = re.split(r"\?(\d{2})", joined[3:])
    for code, value in zip(parts[1::2], parts[2::2]):
        sub[code] = sub.get(code, "") + value  # lines break anywhere, so subfields join with nothing between
    remittance = "".join(sub.get(f"{k:02d}", "") for k in (*range(20, 30), *range(60, 64)))
    return (sub.get("32", "") + sub.get("33", ""), SEPA_KEYS.sub(" ", remittance), sub.get("31", ""), sub.get("00", ""))


def _mt940(text: str):
    n, statement, entry = 0, None, None
    for tag, lines in _fields(text):
        if tag == "20":
            statement, entry = {"ref": lines[0].strip(), "currency": ""}, None
        elif statement is None or tag == "-":
            statement = None if tag == "-" else statement
            entry = None
        elif tag in ("60F", "60M"):
            statement["currency"] = lines[0].strip()[7:10]  # D/C mark, YYMMDD, then the currency
        elif tag == "61":
            if entry:
                yield entry
            n += 1
            entry = _entry_61(n, statement, lines)
            continue
        elif tag == "86" and entry:
            entry["name"], remittance, entry["account"], code = _details(lines)
            entry["remittance"] = " ".join(filter(None, (remittance, entry["remittance"])))
            entry["codes"].append(code)
        if tag != "86" and entry:  # an :86: belongs to the :61: just before it; after anything else it is the statement's
            yield entry
            entry = None
    if entry:
        yield entry


def _entry_61(n: int, statement: dict, lines: list[str]) -> dict:
    where = f"entry {n} (statement {statement['ref'] or '?'})"
    found = LINE_61.fullmatch(lines[0].strip())
    if not found:
        raise ValueError(f"{where}: cannot read the :61: line {lines[0].strip()!r}")
    value, booked, mark, _, amount, kind, customer, bank = found.groups()
    day = f"20{value[:2]}-{value[2:4]}-{value[4:6]}"  # shortcut: two-digit years are read as 20YY
    if booked:  # MMDD in the value date's year, or the next/previous one across a new year
        year = int(day[:4]) + (int(booked[:2]) - int(value[2:4]) < -6) - (int(booked[:2]) - int(value[2:4]) > 6)
        day = f"{year}-{booked[:2]}-{booked[2:]}"
    supplementary = " ".join(line.strip() for line in lines[1:])
    return {"n": n, "where": where, "date": day, "amount": amount.replace(",", ".").rstrip("."),
            "kind": {"C": "credit", "D": "debits"}.get(mark, "reversals"), "currency": statement["currency"],
            "refs": [bank or "", customer], "name": "", "remittance": supplementary, "account": "",
            "codes": [kind, supplementary]}
