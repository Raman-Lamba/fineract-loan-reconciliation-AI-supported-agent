"""Asks the AI to put a line's local shortlist (review.suggest) in order, with one short reason per loan.

Only for a line a person clicked "Ask AI" on, and only with both consents (ai.client.sender(kind="suggest")).
Sent: the narration as it stands (as ai.narration sends it) and, for at most 5 shortlisted loans, the loan_ref
and the borrower's name. Never an amount, a due date, an account number, a phone, an address or a balance.

The answer only re-orders the shortlist and adds a reason: a loan_ref not on the shortlist is dropped, a reason
is one line of plain text cut to MAX_REASON, and the facts beside each suggestion stay the ones review.suggest
computed. A malformed answer or a failed call leaves the local order. It never posts, approves or decides.

Answers are cached in data/ai_suggest_cache.json by provider + model + narration + shortlist, and checked again
when read back.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ai.client import AIError, request

MAX_SHORTLIST = 5
MAX_REASON = 200
SYSTEM = ("You help a person at a lender decide which borrower's loan a bank payment is for. You get the payment's "
          "bank narration and a short list of possible borrowers, each with a loan reference and a name. Put every "
          "loan of the list in order, most likely first, judging only from the names and the narration (a relative "
          "often pays: the same surname with another first name), and give one short plain sentence for each saying "
          "why. Use only loan references from the list. Do not state amounts or dates. Never add a loan.")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ranked"],
          "properties": {"ranked": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": ["loan_ref", "reason"],
              "properties": {"loan_ref": {"type": "string"}, "reason": {"type": "string"}}}}}}


def listing(shortlist: list[dict]) -> list[dict]:
    """What is sent of the shortlist: the loan_ref and the borrower's name, at most MAX_SHORTLIST."""
    return [{"loan_ref": s["loan_ref"], "borrower": s["borrower"]} for s in shortlist[:MAX_SHORTLIST]]


def payload(narration: str, shortlist: list[dict]) -> dict:
    people = "\n".join(f"- {p['loan_ref']}: {p['borrower']}" for p in listing(shortlist))
    return request(SYSTEM, f"Narration: {json.dumps(narration, ensure_ascii=False)}\n\nPossible borrowers:\n{people}",
                   SCHEMA, 2000)


def _reason(text) -> str:
    text = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", text).split()) if isinstance(text, str) else ""
    return text if len(text) <= MAX_REASON else text[:MAX_REASON - 1] + "…"


def valid(answer, shortlist: list[dict]) -> list[dict] | None:
    """The model's order as [{loan_ref, reason}], only refs from the shortlist, each once; None if nothing usable."""
    items = answer.get("ranked") if isinstance(answer, dict) else None
    if not isinstance(items, list):
        return None
    allowed, out = {p["loan_ref"] for p in listing(shortlist)}, []
    for item in items:
        ref = item.get("loan_ref") if isinstance(item, dict) else None
        if isinstance(ref, str) and ref in allowed and ref not in {o["loan_ref"] for o in out}:
            out.append({"loan_ref": ref, "reason": _reason(item.get("reason"))})
    return out or None


def merge(shortlist: list[dict], ranking: list[dict]) -> list[dict]:
    """The shortlist in the model's order (its reason added), then any it left out, in the local order."""
    reasons = {r["loan_ref"]: r["reason"] for r in ranking}
    first = [{**s, "ai_reason": reasons[s["loan_ref"]]} for r in ranking for s in shortlist if s["loan_ref"] == r["loan_ref"]]
    return first + [{**s, "ai_reason": ""} for s in shortlist if s["loan_ref"] not in reasons]


def key(narration: str, shortlist: list[dict], scope: str) -> str:
    return hashlib.sha256(json.dumps([scope, narration, listing(shortlist)], ensure_ascii=False).encode("utf-8")).hexdigest()


def _load(cache_file: Path) -> dict:
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
        return cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        return {}


def cached(narration: str, shortlist: list[dict], scope: str, cache_file: Path) -> list[dict] | None:
    """The ranking given earlier by this provider + model for this line and shortlist, checked again; or None."""
    return valid({"ranked": _load(cache_file).get(key(narration, shortlist, scope))}, shortlist)


def rank(narration: str, shortlist: list[dict], send, cache_file: Path) -> tuple[list[dict] | None, str]:
    """(the model's checked ranking, '') or (None, why not). Never raises for the model's sake."""
    if not shortlist:
        return None, "there are no possible loans to put in order"
    scope = getattr(send, "scope", "")
    hit = cached(narration, shortlist, scope, cache_file)
    if hit:
        return hit, ""
    try:
        ranking = valid(send(payload(narration, shortlist)), shortlist)
    except AIError as problem:
        return None, str(problem)
    if ranking is None:
        return None, "the AI's answer named no loan from the list"
    cache = _load(cache_file)
    cache[key(narration, shortlist, scope)] = ranking
    cache_file.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    return ranking, ""
