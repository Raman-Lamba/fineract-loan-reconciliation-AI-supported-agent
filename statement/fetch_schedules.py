"""Reads every loan and its instalment schedule from Fineract into data/schedules.json.

Run:  python -m statement.fetch_schedules

Instalment amounts are Fineract's own, so the statement generator never redoes EMI maths.
"""
import json
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "schedules.json"


def list_loans(fc, ok) -> list[dict]:
    """Every loan in Fineract, as the list call gives it (no schedule)."""
    loans, offset = [], 0
    while True:
        page = ok(fc.execute_get("loans", {"offset": offset, "limit": 200}), "List loans")
        items = page.get("pageItems", [])
        loans += items
        offset += len(items)
        if not items or offset >= page.get("totalFilteredRecords", 0):
            return loans


def fetch(fc, ok) -> list[dict]:
    out = []
    for item in list_loans(fc, ok):
        loan = ok(fc.execute_get(f"loans/{item['id']}", {"associations": "repaymentSchedule"}), f"Read loan {item['id']}")
        instalments = [
            {"due": date(*p["dueDate"]).isoformat(), "amount": round(p["totalDueForPeriod"], 2)}
            for p in loan["repaymentSchedule"]["periods"] if p.get("period")
        ]
        first, _, last = loan["clientName"].partition(" ")
        # loan_ref is the lender's own reference, Fineract's externalId. A loan without one is known by its
        # Fineract account number, so a book that never filled externalId in can still be reconciled.
        out.append({"loan_ref": loan.get("externalId") or loan["accountNo"], "loan_id": loan["id"],
                    "account_no": loan["accountNo"], "client_id": loan["clientId"], "firstname": first,
                    "lastname": last, "instalments": instalments})
    return sorted(out, key=lambda loan: loan["loan_ref"])


def main():
    from seed.connector import fineract_client as fc, ok

    schedules = fetch(fc, ok)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(schedules, indent=1))
    print(f"loans: {len(schedules)} instalments: {sum(len(s['instalments']) for s in schedules)} -> {OUT}")


if __name__ == "__main__":
    main()
