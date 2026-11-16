"""Reads every loan and its instalment schedule from Fineract into data/schedules.json.

Run:  python -m statement.fetch_schedules

Instalment amounts are Fineract's own, so the statement generator never redoes EMI maths.
"""
import json
from datetime import date

from seed.connector import ROOT, fineract_client as fc, ok

OUT = ROOT / "data" / "schedules.json"


def fetch() -> list[dict]:
    loans, offset = [], 0
    while True:
        page = ok(fc.execute_get("loans", {"offset": offset, "limit": 200}), "List loans")
        items = page.get("pageItems", [])
        loans += items
        offset += len(items)
        if not items or offset >= page.get("totalFilteredRecords", 0):
            break
    out = []
    for item in loans:
        if not item.get("externalId"):
            continue  # not one of ours (the sandbox bootstrap's own demo loans)
        loan = ok(fc.execute_get(f"loans/{item['id']}", {"associations": "repaymentSchedule"}), f"Read loan {item['id']}")
        instalments = [
            {"due": date(*p["dueDate"]).isoformat(), "amount": round(p["totalDueForPeriod"], 2)}
            for p in loan["repaymentSchedule"]["periods"] if p.get("period")
        ]
        first, _, last = loan["clientName"].partition(" ")
        out.append({"loan_ref": loan["externalId"], "loan_id": loan["id"], "account_no": loan["accountNo"],
                    "client_id": loan["clientId"], "firstname": first, "lastname": last,
                    "instalments": instalments})
    return sorted(out, key=lambda loan: loan["loan_ref"])


def main():
    schedules = fetch()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(schedules, indent=1))
    print(f"loans: {len(schedules)} instalments: {sum(len(s['instalments']) for s in schedules)} -> {OUT}")


if __name__ == "__main__":
    main()
