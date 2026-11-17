"""Runs the matcher over the bank statement.

Run:  python -m matcher.run [--bank hdfc]
      (reads data/bank_statement.csv and data/schedules.json, writes data/decisions.csv)

--bank says whose narration layout the statement uses (matcher.match.FORMATS). The default is the
synthetic statement's; after statement.import_bank, pass the same bank.

It never reads answer_key.csv: only matcher.evaluate does. It never calls the AI reader: the review page's
Run tab does that and passes the fields in (run(bank, extracted)).
"""
import argparse
import csv
import json
from pathlib import Path

from matcher.match import FORMATS, decide

DATA = Path(__file__).resolve().parents[1] / "data"


def run(bank: str = "synthetic", extracted: dict | None = None, data: Path | None = None) -> list[dict]:
    """Decides every statement line and writes data/decisions.csv. Returns the decisions."""
    data = data or DATA
    with (data / "bank_statement.csv").open(newline="") as f:
        lines = list(csv.DictReader(f))
    decisions = decide(lines, json.loads((data / "schedules.json").read_text()), bank, extracted)
    with (data / "decisions.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(decisions[0]))
        writer.writeheader()
        writer.writerows({**d, "candidates": ";".join(d["candidates"])} for d in decisions)
    return decisions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", default="synthetic", choices=sorted(FORMATS))
    decisions = run(parser.parse_args().bank)
    counts = {o: sum(d["outcome"] == o for d in decisions) for o in ("post", "queue", "ignore")}
    print(f"lines: {len(decisions)} " + " ".join(f"{o}={n}" for o, n in counts.items()))


if __name__ == "__main__":
    main()
