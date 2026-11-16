"""Deterministic synthetic borrowers and loans. Pure: no I/O, no network.

All data is invented. Names come from a small pool on purpose, so several
borrowers share a full name: the matcher must later refuse to guess between them.
Amounts are whole rupees (int); EMI maths is left to Fineract.

Twin loans (L0251 on) are added after the drawn ones, without touching the random stream: a twin gives
one borrower a copy (principal, months, disbursal date) of a loan held by another borrower with the same
surname and first initial. Fineract then computes equal instalments on equal dates for both, which is
the case the matcher must queue rather than guess.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

FIRST = ["Ramesh", "Suresh", "Anita", "Priya", "Vijay", "Sunita", "Amit", "Pooja", "Rajesh", "Kavita",
         "Manoj", "Deepa", "Sanjay", "Neha", "Arun", "Meena", "Imran", "Fatima", "Joseph", "Lakshmi"]
LAST = ["Kumar", "Sharma", "Patel", "Singh", "Reddy", "Nair", "Das", "Khan", "Verma", "Iyer", "Yadav", "Joshi"]

DISBURSE_FROM = date(2026, 1, 5)   # every loan has instalments due in Aug-Oct 2026
DISBURSE_DAYS = 175                # -> latest disbursal 29 Jun 2026


def _twins(borrowers: list[dict], loans: list[dict], n_twins: int) -> list[dict]:
    """Up to n_twins extra loans, half for same-full-name pairs and half for same-initial pairs."""
    groups: dict = {}
    for b in borrowers:
        groups.setdefault((b["lastname"], b["firstname"][0]), []).append(b)
    same, alike = [], []  # pairs of borrowers; nobody is in more than one pair
    for group in groups.values():
        for a, b in zip(group[::2], group[1::2]):
            (same if a["firstname"] == b["firstname"] else alike).append((a, b))
    pairs = same[: n_twins // 2] + alike[: n_twins - n_twins // 2]
    pairs.sort(key=lambda pair: pair[0]["ref"])

    held: dict = {}
    for loan in loans:
        held.setdefault(loan["borrower_ref"], []).append(loan)
    twins = []
    for a, b in pairs[:n_twins]:
        # The twin must not be disbursed before its borrower's first loan: that is the day Fineract
        # activated the client. Of the loans that allow it (either direction), copy the latest, which
        # leaves the widest margin if a client was activated late.
        options = [(source, x) for x, y in ((a, b), (b, a)) for source in held[y["ref"]]
                   if source["disbursed_on"] >= min(loan["disbursed_on"] for loan in held[x["ref"]])]
        source, x = max(options, key=lambda option: option[0]["disbursed_on"])
        twins.append({"ref": f"L{len(loans) + len(twins) + 1:04d}", "borrower_ref": x["ref"],
                      "principal": source["principal"], "months": source["months"],
                      "disbursed_on": source["disbursed_on"], "twin_of": source["ref"]})
    return twins


def generate(n_borrowers: int = 200, n_loans: int = 250, seed: int = 20260816, n_twins: int = 10) -> dict:
    if n_loans < n_borrowers:
        raise ValueError("every borrower needs at least one loan")
    rng = random.Random(seed)
    borrowers = [
        {
            "ref": f"B{i:04d}",
            "firstname": rng.choice(FIRST),
            "lastname": rng.choice(LAST),
            "mobile": f"90000{i:05d}",  # unique, obviously fake
        }
        for i in range(1, n_borrowers + 1)
    ]
    # One loan each, then the extra loans go to random borrowers (second/third loans).
    owners = [b["ref"] for b in borrowers] + [rng.choice(borrowers)["ref"] for _ in range(n_loans - n_borrowers)]
    loans = [
        {
            "ref": f"L{i:04d}",
            "borrower_ref": owner,
            "principal": rng.randrange(20_000, 300_001, 5_000),
            "months": rng.choice([6, 9, 12, 18, 24, 36]),
            "disbursed_on": (DISBURSE_FROM + timedelta(days=rng.randrange(DISBURSE_DAYS))).isoformat(),
        }
        for i, owner in enumerate(owners, start=1)
    ]
    return {"seed": seed, "borrowers": borrowers, "loans": loans + _twins(borrowers, loans, n_twins)}
