"""Demo reviewer: fills in the review queue from the answer key, standing in for a person.

Run:  python -m review.simulate     (synthetic data only; then python -m review.apply)

Only this and matcher.evaluate read the answer key. The matcher never does.
"""
APPROVER = "demo reviewer (simulated)"
POST = ("ambiguous", "partial", "overpaid", "paid_by_other")


def simulate(queue: list[dict], key: list[dict]) -> list[dict]:
    """The queue with a decision on every row the answer key covers. A decision already there is kept."""
    real = {k["txn_id"]: k for k in key if k["case"] != "duplicate"}  # the first copy of a repeated line
    out = []
    for row in queue:
        case = real.get(row["txn_id"], {}).get("case")
        if row["decision"].strip() or case not in POST + ("unrelated",):
            out.append(row)
            continue
        post = case in POST
        out.append({**row, "decision": "post" if post else "reject",
                    "loan_ref": real[row["txn_id"]]["true_loan_ref"] if post else "",
                    "approver": APPROVER, "note": f"answer key: {case}"})
    return out


def main():
    from posting.run import read
    from review.build import QUEUE, read_queue, write_queue

    before = read_queue()
    rows = simulate(before, read("answer_key.csv"))
    write_queue(rows)
    filled = [row for row, old in zip(rows, before) if row is not old]
    print(f"queue rows: {len(rows)} filled in: post={sum(r['decision'] == 'post' for r in filled)} "
          f"reject={sum(r['decision'] == 'reject' for r in filled)} left alone={len(rows) - len(filled)} -> {QUEUE}")


if __name__ == "__main__":
    main()
