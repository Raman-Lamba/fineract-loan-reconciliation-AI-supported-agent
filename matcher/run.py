"""Runs the matcher over the bank statement.

Run:  python -m matcher.run [--bank hdfc]
      (reads data/bank_statement.csv and data/schedules.json, writes data/decisions.csv)

--bank says whose narration layout the statement uses (matcher.match.FORMATS). The default is the
synthetic statement's; after statement.import_bank, pass the same bank.

It never reads answer_key.csv: only matcher.evaluate does.
"""
import argparse
import csv
import json
from pathlib import Path

from matcher.match import FORMATS, decide

DATA = Path(__file__).resolve().parents[1] / "data"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", default="synthetic", choices=sorted(FORMATS))
    bank = parser.parse_args().bank
    with (DATA / "bank_statement.csv").open(newline="") as f:
        lines = list(csv.DictReader(f))
    decisions = decide(lines, json.loads((DATA / "schedules.json").read_text()), bank)
    with (DATA / "decisions.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(decisions[0]))
        writer.writeheader()
        writer.writerows({**d, "candidates": ";".join(d["candidates"])} for d in decisions)
    counts = {o: sum(d["outcome"] == o for d in decisions) for o in ("post", "queue", "ignore")}
    print(f"lines: {len(decisions)} " + " ".join(f"{o}={n}" for o, n in counts.items()))


if __name__ == "__main__":
    main()
