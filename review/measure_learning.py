"""How much learning from approvals (matcher.payer, review.learn) would shrink the review queue. Synthetic data only.

Run:  python -m review.measure_learning [--data DIR]

Replays the statement in date order, in memory (nothing is written, nothing posted): each day is matched with the
links learned so far; every queued line the answer key says belongs to a loan is then approved for that loan by a
simulated person, which teaches its payer account exactly as review.apply would (matcher.payer.learn). It reports
how many later lines were posted by a learned account, how many of those went to the wrong loan, and the queue
before and after. Done twice: on data/bank_statement.csv, and on the repeat-payer statement generated in memory
(statement.generate repeat_payers). Like review.measure_suggestions, this command reads the answer key; the
matcher and review.learn never do.
"""
import argparse
import csv
import json
from itertools import groupby
from pathlib import Path

from matcher.match import NO_REFERENCE, decide
from matcher.payer import current, key, learn
from statement.generate import generate

SIMULATED = "replay (simulated person)"


def replay(lines: list[dict], answer_key: list[dict], schedules: list[dict], bank: str = "synthetic") -> dict:
    truth = {}
    for row in answer_key:
        if row["case"] != "duplicate":
            truth.setdefault(row["txn_id"], row["true_loan_ref"])
    lines = sorted(lines, key=lambda line: line["date"])
    baseline = decide(lines, schedules, bank)  # the same lines with nothing learned
    rows, after, done = [], 0, 0
    stats = dict.fromkeys(("auto", "wrong", "rescued", "queued_by_link", "was_posted_before", "conflicts"), 0)
    for _, day in groupby(lines, key=lambda line: line["date"]):
        day = list(day)
        for n, (line, d) in enumerate(zip(day, decide(day, schedules, bank, links=current(rows))), done):
            linked = d["reason"].startswith("payer account ")
            if d["outcome"] == "post" and linked:
                stats["auto"] += 1
                stats["wrong"] += d["loan_ref"] != truth.get(d["txn_id"])
                stats["rescued"] += baseline[n]["outcome"] == "queue"
            if d["outcome"] != "queue":
                continue
            after += 1
            if linked:
                stats["queued_by_link"] += 1
                stats["was_posted_before"] += baseline[n]["outcome"] == "post"
            true = truth.get(d["txn_id"])
            if true and not d["txn_id"].startswith(NO_REFERENCE):
                row, _ = learn(current(rows), key(line["narration"], bank), true, d["txn_id"], SIMULATED, d["date"])
                if row:
                    rows.append(row)
                    stats["conflicts"] += row["status"] == "disabled"
        done += len(day)
    keyed = [line for line in lines if key(line["narration"], bank)]
    return {"lines": len(lines), "with_key": len(keyed), "distinct_keys": len({key(x["narration"], bank) for x in keyed}),
            "queue_before": sum(d["outcome"] == "queue" for d in baseline), "queue_after": after,
            "learned": sum(r["status"] == "active" for r in rows), **stats}


def _read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    data = parser.parse_args().data
    schedules = json.loads((data / "schedules.json").read_text())
    for name, (lines, answer) in (("statement in data/", (_read(data / "bank_statement.csv"), _read(data / "answer_key.csv"))),
                                  ("repeat-payer statement", generate(schedules, repeat_payers=True))):
        r = replay(lines, answer, schedules)
        print(f"{name}: {r['lines']} lines, {r['with_key']} with a payer account (UPI ID) "
              f"({100 * r['with_key'] / r['lines']:.1f}%), {r['distinct_keys']} distinct accounts")
        change = r["queue_after"] - r["queue_before"]
        print(f"  queue before learning: {r['queue_before']}  after: {r['queue_after']}  "
              + (f"({-change} fewer)" if change <= 0 else f"({change} more)"))
        print(f"  links learned: {r['learned']}, turned off as shared: {r['conflicts']}")
        print(f"  posted by a learned account: {r['auto']}, to the wrong loan: {r['wrong']}; "
              f"of them, lines the name rule alone would have queued: {r['rescued']}")
        print(f"  queued because of a learned account: {r['queued_by_link']} "
              f"(of which the name rule alone would have posted: {r['was_posted_before']})")
    print("Synthetic data: the default statement's UPI IDs are a first name + a bank, shared by namesakes.")


if __name__ == "__main__":
    main()
