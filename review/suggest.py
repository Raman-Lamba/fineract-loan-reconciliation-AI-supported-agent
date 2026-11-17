"""Possible loans for a queued line the matcher found no loan for: a shortlist for a person, never a decision.

Local and offline: standard library only, nothing is sent anywhere, no key is needed. For each loan it scores
the payer's name (same surname with another first name counts, because relatives pay; an initial, a name cut
short or run together, and a misspelling count too) and the instalment due nearest the payment. The facts on
each suggestion (the instalment, its due date, whether the amount equals it and by how much it differs) are
computed here, never by a model. A suggestion only fills the loan box on the Review tab: the person still
chooses and approves, and the queue's decision is theirs.

Design: docs/superpowers/specs/2026-11-17-suggestions-design.md
"""
from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache

from matcher.match import FORMATS, WINDOW_DAYS, _paise, payer_name

LIMIT = 5
SEARCH_DAYS = 31  # an instalment this close to the payment is shown as the one it may be for
SIMILAR = 0.75  # difflib ratio from which two words count as the same name misspelt (KUMRA/KUMAR is 0.8)
# Words of a narration that are never a name, for a bank whose layout the rules cannot read.
# shortcut: a short English list; a bank whose narrations carry other boilerplate words adds them here.
NOT_NAMES = {"upi", "neft", "imps", "nach", "rtgs", "ach", "cr", "dr", "by", "to", "from", "transfer", "p2a", "inf",
             "mmt", "emi", "loan", "instalment", "installment", "repayment", "payment", "part", "ref", "the", "for",
             "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}


@lru_cache(maxsize=65536)
def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def name_tokens(narration: str, bank: str = "synthetic") -> list[str]:
    """The payer's name as lower-case words: the bank's name field if the rules read one, else the narration's words."""
    name = payer_name(narration, bank) if bank in FORMATS else ""
    if name:
        return re.findall(r"[a-z]+", name.lower())
    return [word for word in re.findall(r"[a-z]+", narration.lower()) if word not in NOT_NAMES]


def _surname(word: str, last: str) -> float:
    if len(word) >= 2 and last.startswith(word):  # whole, or cut short by the bank's 10-character field
        return 1.0
    return _misspelt(word, last)


def _misspelt(word: str, name: str) -> float:
    # Under 4 letters a ratio means little (ADV is 0.75 like YADAV), so a short word must match exactly or as a prefix.
    ratio = _ratio(word, name) if len(word) >= 4 else 0.0
    return ratio if ratio >= SIMILAR else 0.0


def _first(word: str, first: str) -> float:
    if word == first or (len(word) >= 3 and first.startswith(word)):
        return 1.0
    if len(word) == 1:
        return 0.7 if word == first[:1] else 0.0
    return _misspelt(word, first)


def name_match(words: list[str], first: str, last: str) -> tuple[float, float, str]:
    """(surname score, first-name score, a plain label) for the best reading of `words` as this borrower."""
    first, last = first.lower(), last.lower()
    if len(words) == 1 and len(words[0]) >= 6:  # RAMESHKUMA: the name run together and cut
        whole = _ratio(words[0], (first + last)[:len(words[0])])
        if whole >= 0.85:
            return whole, whole, "same name, written as one word"
    best = (0.0, 0.0)
    for i, word in enumerate(words):
        s = _surname(word, last)
        f = max((_first(other, first) for j, other in enumerate(words) if j != i), default=0.0)
        best = max(best, (s, f), key=lambda sf: 0.6 * sf[0] + 0.4 * sf[1])
    if best[0] == 0:  # no surname: the first name alone, if any word is it
        best = (0.0, max((_first(word, first) for word in words), default=0.0))
    s, f = best
    if s == 1.0 and f == 1.0:
        return s, f, "same name"
    surname = "same surname" if s == 1.0 else "similar surname" if s else "different surname"
    given = ("same first name" if f == 1.0 else "first initial matches" if f == 0.7 else
             "similar first name" if f else "different first name (a relative?)" if s else "")
    return s, f, ", ".join(filter(None, (surname, given)))


def nearest(loan: dict, paise: int, day: date) -> dict | None:
    """The instalment this payment may be for: one of exactly this amount within the window, else the closest in time."""
    near = [(i, (day - date.fromisoformat(i["due"])).days) for i in loan["instalments"]]
    near = [(i, days) for i, days in near if abs(days) <= SEARCH_DAYS]
    if not near:
        return None
    inst, days = min(near, key=lambda pair: (not (_paise(pair[0]["amount"]) == paise and abs(pair[1]) <= WINDOW_DAYS),
                                             abs(pair[1])))
    return {"due": inst["due"], "paise": _paise(inst["amount"]), "days": days}


def suggest(line: dict, loans: list[dict], bank: str = "synthetic", exclude=(), limit: int = LIMIT) -> list[dict]:
    """Up to `limit` loans this line may be for, best first; [] when no loan is plausible. Loans in `exclude`
    (the matcher's own candidates, already on the page) are left out."""
    words = name_tokens(line["narration"], bank)
    paise, day = _paise(line["amount"]), date.fromisoformat(line["date"])
    out = []
    for loan in loans:
        if loan["loan_ref"] in exclude:
            continue
        s, f, label = name_match(words, loan["firstname"], loan["lastname"])
        inst = nearest(loan, paise, day)
        equal = bool(inst) and inst["paise"] == paise and abs(inst["days"]) <= WINDOW_DAYS
        named = 0.6 * s + 0.4 * f
        # A surname that fits (0.6 * SIMILAR = 0.45), or an exact instalment due these days: anything less is noise.
        if named < 0.6 * SIMILAR and not equal:
            continue
        closeness = 1.0 if equal else max(0.0, 1 - abs(paise - inst["paise"]) / inst["paise"]) * 0.6 if inst else 0.0
        timing = 1 - abs(inst["days"]) / SEARCH_DAYS if inst else 0.0
        confidence = ("high" if s >= SIMILAR and equal else
                      "medium" if (s >= SIMILAR and inst) or equal else "low")
        out.append({"loan_ref": loan["loan_ref"], "borrower": f"{loan['firstname']} {loan['lastname']}",
                    "loan_id": loan.get("loan_id"), "client_id": loan.get("client_id"),
                    "similarity": label or "name does not match", "confidence": confidence,
                    "due": inst["due"] if inst else "", "instalment": f"{inst['paise'] / 100:.2f}" if inst else "",
                    "equal": equal, "difference": f"{(paise - inst['paise']) / 100:.2f}" if inst else "",
                    "days": inst["days"] if inst else None,
                    "score": round(0.55 * named + 0.35 * closeness + 0.10 * timing, 4)})
    out.sort(key=lambda s: (-s["score"], s["loan_ref"]))
    return out[:limit]
