"""Applies the decisions a person wrote in the review queue: post to the loan they chose, or reject.

Run:  python -m review.apply     (reads data/queue.csv; posts to Fineract; appends to ledger.csv and audit.csv)

Safe to re-run: an applied line leaves the queue, and a txn_id in the ledger is never posted again.
"""
from posting.post import post_all

PROBLEM = "PROBLEM: "


def apply(queue: list[dict], lines: dict, loan_ids: dict, fc, payment_types: dict, ledger_ids: set,
          rejected_ids: set):
    """Yields (the row if it stays in the queue, audit row or None, ledger row or None) per queue row.

    `lines` maps txn_id to its statement line: the date and amount posted come from the bank, never from
    the edited file, where Excel reformats them.
    """
    for row in queue:
        decision = row["decision"].strip().lower()
        if not decision:
            yield row, None, None
            continue
        txn, ref, approver = row["txn_id"], row["loan_ref"].strip().upper(), row["approver"].strip()
        if txn in ledger_ids or txn in rejected_ids:  # applied earlier, e.g. from an older copy of the file
            yield None, None, None
            continue
        # A problem written by an earlier run sits in front of the person's own note.
        note = row["note"].partition(" | ")[2] if row["note"].startswith(PROBLEM) else row["note"]
        problem = audit = ledger = None
        if txn not in lines:
            problem = "this txn_id is not in the bank statement (was the cell changed?)"
        elif decision not in ("post", "reject"):
            problem = f"unknown decision '{row['decision']}': write post or reject"
        elif not approver:
            problem = "approver is required"
        elif decision == "reject":
            audit = {"txn_id": txn, "action": "rejected", "loan_ref": "", "reason": f"manual: {note}",
                     "approver": approver}
        elif ref not in loan_ids:
            problem = f"loan_ref '{ref}' is not a known loan"
        else:
            d = {"txn_id": txn, "date": lines[txn]["date"], "amount": lines[txn]["amount"], "outcome": "post",
                 "loan_ref": ref, "reason": f"manual: {note}"}
            (audit, ledger), = post_all([d], {txn: lines[txn]["narration"]}, loan_ids, fc, payment_types, set(),
                                        approver)
            if not ledger:
                problem = audit["reason"]
        yield ({**row, "note": f"{PROBLEM}{problem} | {note}"} if problem else None), audit, ledger


def main():
    import json
    from datetime import datetime

    from posting.run import AUDIT, DATA, LEDGER, appender, read
    from review.build import QUEUE, read_queue, write_queue
    from seed.connector import fineract_client as fc, ok

    queue = read_queue()
    QUEUE.open("a").close()  # stops here, before any posting, if Excel still has the file open
    lines = {}
    for line in read("bank_statement.csv"):
        lines.setdefault(line["txn_id"], line)
    loan_ids = {loan["loan_ref"]: loan["loan_id"] for loan in json.loads((DATA / "schedules.json").read_text())}
    payment_types = {p["name"]: p["id"] for p in ok(fc.execute_get("paymenttypes"), "Read payment types")}
    ledger_ids = {row["txn_id"] for row in read("ledger.csv")}
    rejected_ids = {row["txn_id"] for row in read("audit.csv") if row["action"] == "rejected"}

    to_ledger, to_audit = appender("ledger.csv", LEDGER), appender("audit.csv", AUDIT)
    kept, counts = [], {}
    for keep, audit, ledger in apply(queue, lines, loan_ids, fc, payment_types, ledger_ids, rejected_ids):
        if ledger:
            to_ledger(ledger)
        if audit:
            to_audit({"at": datetime.now().isoformat(timespec="seconds"), **audit})
        if keep:
            kept.append(keep)
            what = "left in the queue with a problem" if keep["decision"].strip() else "no decision yet"
            if keep["decision"].strip():
                print(f"{keep['txn_id']}: {keep['note']}")
        else:
            what = audit["action"] if audit else "applied earlier"
        counts[what] = counts.get(what, 0) + 1
    write_queue(kept)
    print(f"queue rows: {len(queue)} " + " ".join(f"{what}={n}" for what, n in counts.items())
          + f" -> {len(kept)} left in {QUEUE}")


if __name__ == "__main__":
    main()
