"""Checks the split ledger against Fineract and prints the totals.

Run:  python -m posting.verify     (reads data/ledger.csv and schedules.json, and every loan from Fineract)
"""
import json
import sys
from decimal import Decimal


def check(ledger: list[dict], in_fineract: dict) -> list[str]:
    """Problems found. `in_fineract` maps each live repayment's externalId to its amount."""
    problems, seen = [], set()
    for row in ledger:
        txn, amount = row["txn_id"], Decimal(row["amount"])
        if txn in seen:
            problems.append(f"{txn}: in the ledger twice")
        seen.add(txn)
        if Decimal(row["partner_share"]) + Decimal(row["lender_share"]) != amount:
            problems.append(f"{txn}: shares do not add up to {amount}")
        if txn not in in_fineract:
            problems.append(f"{txn}: not in Fineract")
        elif Decimal(str(in_fineract[txn])) != amount:
            problems.append(f"{txn}: amount {amount} in the ledger, {in_fineract[txn]} in Fineract")
    return problems


def read_fineract(loans: list[dict], fc, ok) -> tuple[dict, int]:
    """(each live repayment's externalId -> its amount, how many of the loans are in arrears)."""
    in_fineract, arrears = {}, 0
    for loan in loans:
        live = ok(fc.execute_get(f"loans/{loan['loan_id']}", {"associations": "transactions"}),
                  f"Read loan {loan['loan_ref']}")
        arrears += bool(live.get("inArrears"))
        in_fineract.update({t["externalId"]: t["amount"] for t in live["transactions"]
                            if t.get("externalId") and not t.get("manuallyReversed")})
    return in_fineract, arrears


def totals(ledger: list[dict], digits: int = 2) -> dict:
    """Payments posted and the money: in all, the partner's share and the lender's."""
    zero = Decimal(0).quantize(Decimal(1).scaleb(-digits))
    return {"payments": len(ledger), **{column: sum((Decimal(row[column]) for row in ledger), zero)
                                        for column in ("amount", "partner_share", "lender_share")}}


def main():
    from posting.run import DATA, read  # here, not at the top: check() must import without the connector
    from locale_settings import from_env
    from seed.connector import fineract_client as fc, ok

    settings = from_env(DATA.parent / ".env")
    ledger = read("ledger.csv")
    loans = json.loads((DATA / "schedules.json").read_text())
    in_fineract, arrears = read_fineract(loans, fc, ok)

    problems = check(ledger, in_fineract)
    for problem in problems:
        print(f"PROBLEM {problem}")
    total = totals(ledger, settings.minor)
    print(f"ledger rows checked against Fineract: {len(ledger)}, problems: {len(problems)}")
    print(f"payments posted: {total['payments']}")
    print(f"amount posted ({settings.currency}): {total['amount']:,}")
    if settings.co_lending:
        print(f"partner share:   {total['partner_share']:,}")
        print(f"lender share:    {total['lender_share']:,}")
    print(f"loans in arrears: {arrears} of {len(loans)}")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
