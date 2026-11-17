"""Reads the fields the matcher needs from narrations its rules cannot read (a bank with no FORMATS entry).

Fields per narration: payer_name, channel, reference, loan_account. Each text field must appear word for
word in that narration, or it is dropped; channel must be UPI, NEFT, IMPS or NACH. An answer that is not
JSON, or misses a narration, leaves that narration unread: the matcher then queues it for a person.
The fields only fill blanks in matcher.match.decide; amount, date and txn_id never come from here.

Only the narration text is sent, about BATCH unique ones per request. Answers are cached by a hash of the
narration, the provider and the model in data/ai_cache.json, so a re-run costs nothing and another model
never reuses an answer.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ai.client import AIError, request

BATCH = 40
# shortcut: the India channels only, and nothing uses the answer yet; make it follow CHANNELS when something does.
CHANNELS = ("UPI", "NEFT", "IMPS", "NACH")
TEXT_FIELDS = ("payer_name", "reference", "loan_account")
# shortcut: reference and channel are checked and cached but nothing uses them yet (txn_id stays the file's
# reference column, the payment type stays posting.post.channel); use them when a bank turns up that needs it.
SYSTEM = ("You read Indian bank statement narrations. For each one, copy fields exactly as they are written in it, "
          "character for character. If a field is not in the narration, give an empty string. Never invent, "
          "complete or correct a name or a number.")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
          "properties": {"items": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "required": ["i", "payer_name", "channel", "reference", "loan_account"],
              "properties": {"i": {"type": "integer"}, "payer_name": {"type": "string"},
                             "channel": {"type": "string", "enum": [*CHANNELS, ""]},
                             "reference": {"type": "string"}, "loan_account": {"type": "string"}}}}}}


def key(narration: str, scope: str = "") -> str:
    """Cache key. `scope` is send.scope (provider:model); entries from before it existed are plain hashes, so misses."""
    return hashlib.sha256((f"{scope}{narration}" if scope else narration).encode("utf-8")).hexdigest()


def valid(narration: str, item) -> dict | None:
    """The fields of one answer that survive the checks, or None if the answer is not a set of fields."""
    if not isinstance(item, dict) or not all(isinstance(item.get(f, ""), str) for f in (*TEXT_FIELDS, "channel")):
        return None
    flat = " ".join(narration.split())
    name = " ".join(item.get("payer_name", "").split())
    # A name is kept only as written in the narration (case aside), and only if it has letters.
    name = name if name and len(name) <= 60 and re.search(r"[A-Za-z]", name) and name.upper() in flat.upper() else ""
    # Numbers must be there exactly, case included: a reference that is not in the text is discarded.
    exact = {f: item.get(f, "").strip() for f in ("reference", "loan_account")}
    exact = {f: v if v and re.search(r"\d", v) and v in narration else "" for f, v in exact.items()}
    channel = item.get("channel", "")
    return {"payer_name": name, "channel": channel if channel in CHANNELS else "", **exact}


def _load(cache_file: Path) -> dict:
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
        return cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        return {}


def prompt(batch: list[str]) -> str:
    listing = "\n".join(f"{i}: {json.dumps(text, ensure_ascii=False)}" for i, text in enumerate(batch))
    return ("For each numbered bank narration below, give i (its number) and: payer_name, the name of the person "
            "or business who sent the money, as written; channel, UPI, NEFT, IMPS or NACH if the narration shows "
            "it, else empty; reference, the bank's transaction reference (UTR, RRN or IMPS reference) as written; "
            "loan_account, a loan account number if the narration names one, else empty.\n\n" + listing)


def extract(narrations, send, cache_file: Path) -> tuple[dict, dict]:
    """(narration -> checked fields, what happened). `send` is ai.client.sender(...) or a fake.

    A failed request is reported in what happened and its narrations stay unread; it never raises.
    """
    unique = list(dict.fromkeys(narrations))
    cache, scope = _load(cache_file), getattr(send, "scope", "")
    todo = [text for text in unique if key(text, scope) not in cache]
    stats = {"narrations": len(unique), "from_cache": len(unique) - len(todo), "sent": 0, "errors": []}
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        stats["sent"] += len(batch)
        try:
            items = send(request(SYSTEM, prompt(batch), SCHEMA)).get("items")
        except AIError as problem:
            stats["errors"].append(str(problem))
            continue
        if not isinstance(items, list):
            stats["errors"].append("the model's answer had no list of items")
            continue
        for item in items:
            i = item.get("i") if isinstance(item, dict) else None
            if type(i) is int and 0 <= i < len(batch) and key(batch[i], scope) not in cache:
                fields = valid(batch[i], item)
                if fields is not None:
                    cache[key(batch[i], scope)] = fields
        cache_file.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    # Checked again on the way out: the cache is a file anyone could edit.
    out = {text: valid(text, cache[key(text, scope)]) for text in unique if key(text, scope) in cache}
    out = {text: fields for text, fields in out.items() if fields}
    stats["read"] = sum(bool(f["payer_name"] or f["loan_account"]) for f in out.values())
    stats["unread"] = len(unique) - len(out)
    return out, stats
