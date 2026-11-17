"""Posts the matcher's `post` decisions to Fineract and writes the split ledger and the audit log.

Run:  python -m posting.run [--limit N]
Reads data/decisions.csv, bank_statement.csv and schedules.json; appends to data/ledger.csv and audit.csv.

Safe to re-run: a txn_id in the ledger, or already queued for a person, is skipped, and Fineract refuses a
second use of an externalId.
"""
import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from locale_settings import from_env, with_fineract_digits
from posting.post import handled, post_all

DATA = Path(__file__).resolve().parents[1] / "data"
LEDGER = ["txn_id", "date", "loan_ref", "fineract_txn_id", "amount", "partner_share", "lender_share"]
AUDIT = ["at", "txn_id", "action", "loan_ref", "reason", "approver"]


def read(name: str) -> list[dict]:
    path = DATA / name
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def appender(name: str, fields: list[str]):
    path = DATA / name
    new = not path.exists()
    # backslashreplace: a note typed in the review page may hold a character this machine's encoding
    # lacks (a rupee sign, Hindi). It is written as \uXXXX; failing here would lose the row after the post.
    f = path.open("a", newline="", errors="backslashreplace")
    writer = csv.DictWriter(f, fieldnames=fields)
    if new:
        writer.writeheader()

    def write(row: dict):
        writer.writerow(row)
        f.flush()  # each row reaches the disk before the next post, so a crash loses at most one

    write.close = f.close  # for the review page, which keeps running; a command just ends
    return write


def main():
    from seed.connector import fineract_client as fc, ok  # here: the review page reads the files without it

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="only the first N `post` decisions: a quick trial")
    parser.add_argument("--env", type=Path, default=DATA.parent / ".env", help="the .env holding the locale settings")
    args = parser.parse_args()
    settings = with_fineract_digits(from_env(args.env), fc)

    decisions = [d for d in read("decisions.csv") if d["outcome"] == "post"][: args.limit]
    narrations = {line["txn_id"]: line["narration"] for line in read("bank_statement.csv")}
    loan_ids = {loan["loan_ref"]: loan["loan_id"] for loan in json.loads((DATA / "schedules.json").read_text())}
    payment_types = {p["name"]: p["id"] for p in ok(fc.execute_get("paymenttypes"), "Read payment types")}
    skip = handled(read("ledger.csv"), read("audit.csv"))

    to_ledger, to_audit = appender("ledger.csv", LEDGER), appender("audit.csv", AUDIT)
    counts = dict.fromkeys(("posted", "recovered", "queued", "failed"), 0)
    for audit, ledger in post_all(decisions, narrations, loan_ids, fc, payment_types, skip, settings=settings):
        if ledger:
            to_ledger(ledger)
        to_audit({"at": datetime.now().isoformat(timespec="seconds"), **audit})
        counts[audit["action"]] += 1
        if audit["action"] == "failed":
            print(f"FAILED {audit['txn_id']} {audit['loan_ref']}: {audit['reason']}")
        if sum(counts.values()) % 100 == 0:
            print(f"attempted: {sum(counts.values())}")
    skipped = len(decisions) - sum(counts.values())
    print(f"post decisions: {len(decisions)} posted={counts['posted']} skipped (in ledger or queued earlier)={skipped} "
          f"already paid (queued)={counts['queued']} recovered={counts['recovered']} failed={counts['failed']}")


if __name__ == "__main__":
    main()
