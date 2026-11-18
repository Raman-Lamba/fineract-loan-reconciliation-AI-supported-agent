"""Builds the review queue: every bank line a person has to decide, as a CSV to edit in Excel.

Run:  python -m review.build     (writes data/queue.csv; steps: docs/superpowers/specs/2026-11-16-review-queue-design.md)

Safe to re-run: what a person already filled in is kept, and lines since posted or rejected are left out.
"""
import csv
import json
from datetime import date
from pathlib import Path

QUEUE = Path(__file__).resolve().parents[1] / "data" / "queue.csv"
# The columns a person fills in. remember: "no" stops an approval teaching the payer's account (review.learn);
# empty or anything else lets it.
EDITS = ["decision", "loan_ref", "approver", "note", "remember"]
FIELDS = ["txn_id", "date", "amount", "narration", "reason", "candidates"] + EDITS
# Excel shows a bare 12-digit number as 5.07296E+11 and saves it that way (seen on 16 Nov 2026), which
# would lose the txn_id. With this in front, Excel keeps it as text. In memory it is always the bare id.
AS_TEXT = "UTR"


def read_queue() -> list[dict]:
    if not QUEUE.exists():
        return []
    with QUEUE.open(newline="", encoding="utf-8-sig") as f:  # -sig: Excel's "CSV UTF-8" starts with a BOM
        return [{**{field: row.get(field) or "" for field in FIELDS}, "txn_id": row["txn_id"].removeprefix(AS_TEXT)}
                for row in csv.DictReader(f)]


def write_queue(rows: list[dict]):
    with QUEUE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({**row, "txn_id": AS_TEXT + row["txn_id"]} for row in rows)


def _candidate(loan: dict, day: date) -> str:
    """The loan, its borrower and the instalment due nearest the payment: enough to choose without Fineract."""
    inst = min(loan["instalments"], key=lambda i: abs((date.fromisoformat(i["due"]) - day).days))
    return f"{loan['loan_ref']} {loan['firstname']} {loan['lastname']}: {inst['amount']:.2f} due {inst['due']}"


def build(decisions: list[dict], audit: list[dict], ledger_ids: set, lines: list[dict], schedules: list[dict],
          old: list[dict]) -> list[dict]:
    """Queue rows in statement order. `old` is the queue as a person left it."""
    done = set(ledger_ids) | {a["txn_id"] for a in audit if a["action"] == "rejected"}
    # Why each line is here and which loans it could be for: the matcher's queue decisions, then the
    # lines the matcher would have posted but posting found already paid.
    why = {d["txn_id"]: (d["reason"], d["candidates"]) for d in decisions if d["outcome"] == "queue"}
    why.update((a["txn_id"], (a["reason"], a["loan_ref"])) for a in audit if a["action"] == "queued")
    loans = {loan["loan_ref"]: loan for loan in schedules}
    edits = {row["txn_id"]: row for row in old}
    rows = []
    for line in lines:
        txn = line["txn_id"]
        if txn not in why or txn in done:
            continue
        done.add(txn)  # a txn_id the bank feed repeats is queued once
        reason, refs = why[txn]
        day = date.fromisoformat(line["date"])
        rows.append({**line, "reason": reason,
                     "candidates": "; ".join(_candidate(loans[ref], day) for ref in refs.split(";") if ref),
                     **{field: edits.get(txn, {}).get(field, "") for field in EDITS}})
    return rows


def main():
    from posting.run import DATA, read

    rows = build(read("decisions.csv"), read("audit.csv"), {row["txn_id"] for row in read("ledger.csv")},
                 read("bank_statement.csv"), json.loads((DATA / "schedules.json").read_text()), read_queue())
    write_queue(rows)
    print(f"queue: {len(rows)} lines, {sum(bool(row['decision']) for row in rows)} with a decision -> {QUEUE}")


if __name__ == "__main__":
    main()
