"""How often the local shortlist (review.suggest) holds the loan that really paid. Synthetic data only.

Run:  python -m review.measure_suggestions [--data DIR]

Over every line review.build would queue (the matcher's queue decisions and the lines posting found already
paid), it compares the shortlist with data/answer_key.csv's true_loan_ref. This command is the only reader
of the answer key here; review.suggest never sees it. The shortlist is measured whole (the matcher's own
candidates not left out), so lines that already have candidates show how good the list is too.
"""
import argparse
import csv
import json
from pathlib import Path

from review.suggest import LIMIT, suggest
from statement.import_bank import recorded_bank

CASES = ["paid_by_other", "ambiguous", "partial", "overpaid", "unrelated"]


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def measure(data: Path) -> dict:
    lines = {line["txn_id"]: line for line in _read(data / "bank_statement.csv")}
    loans = json.loads((data / "schedules.json").read_text())
    truth = {}
    for row in _read(data / "answer_key.csv"):
        if row["case"] != "duplicate":  # the repeat of a line has the first copy's txn_id
            truth.setdefault(row["txn_id"], row)
    queued = [d["txn_id"] for d in _read(data / "decisions.csv") if d["outcome"] == "queue"]
    queued += [a["txn_id"] for a in _read(data / "audit.csv") if a["action"] == "queued"]
    bank = recorded_bank(data / "bank_statement.csv")
    stats = {}
    for txn in dict.fromkeys(queued):
        key = truth.get(txn, {"case": "(not in the answer key)", "true_loan_ref": ""})
        shortlist = [s for s in suggest(lines[txn], loans, bank)]
        refs, true = [s["loan_ref"] for s in shortlist], key["true_loan_ref"]
        case = stats.setdefault(key["case"], dict.fromkeys(("lines", "with_true_loan", "hit_at_1", "hit_at_5",
                                                            "any_suggestion", "high_confidence"), 0))
        case["lines"] += 1
        case["with_true_loan"] += bool(true)
        case["hit_at_1"] += bool(true) and refs[:1] == [true]
        case["hit_at_5"] += bool(true) and true in refs[:LIMIT]
        case["any_suggestion"] += bool(refs)
        case["high_confidence"] += any(s["confidence"] == "high" for s in shortlist)
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    stats = measure(parser.parse_args().data)
    order = [c for c in CASES if c in stats] + sorted(c for c in stats if c not in CASES)
    pct = lambda n, d: f"{n:>4} ({100 * n / d:5.1f}%)" if d else f"{n:>4}      -"  # noqa: E731
    print(f"{'case':<26}{'lines':>6}{'true loan':>10}   {'hit@1':<14}{'hit@5':<14}{'any suggestion':<16}high confidence")
    for name in order + ["all"]:
        c = stats[name] if name != "all" else {k: sum(s[k] for s in stats.values()) for k in next(iter(stats.values()))}
        print(f"{name:<26}{c['lines']:>6}{c['with_true_loan']:>10}   {pct(c['hit_at_1'], c['with_true_loan']):<14}"
              f"{pct(c['hit_at_5'], c['with_true_loan']):<14}{pct(c['any_suggestion'], c['lines']):<16}"
              f"{pct(c['high_confidence'], c['lines'])}")
    print("hit@k: the true loan is among the first k suggestions (share of lines with a true loan). "
          "Synthetic data; the shortlist never reads the answer key.")


if __name__ == "__main__":
    main()
