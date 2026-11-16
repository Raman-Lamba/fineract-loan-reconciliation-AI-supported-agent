"""Deterministic synthetic borrowers and loans. Pure: no I/O, no network.

All data is invented. Names come from a small pool on purpose, so several
borrowers share a full name: the matcher must later refuse to guess between them.
Amounts are whole rupees (int); EMI maths is left to Fineract.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

FIRST = ["Ramesh", "Suresh", "Anita", "Priya", "Vijay", "Sunita", "Amit", "Pooja", "Rajesh", "Kavita",
         "Manoj", "Deepa", "Sanjay", "Neha", "Arun", "Meena", "Imran", "Fatima", "Joseph", "Lakshmi"]
LAST = ["Kumar", "Sharma", "Patel", "Singh", "Reddy", "Nair", "Das", "Khan", "Verma", "Iyer", "Yadav", "Joshi"]

DISBURSE_FROM = date(2026, 1, 5)   # every loan has instalments due in Aug-Oct 2026
DISBURSE_DAYS = 175                # -> latest disbursal 29 Jun 2026


def generate(n_borrowers: int = 200, n_loans: int = 250, seed: int = 20260816) -> dict:
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
    return {"seed": seed, "borrowers": borrowers, "loans": loans}
