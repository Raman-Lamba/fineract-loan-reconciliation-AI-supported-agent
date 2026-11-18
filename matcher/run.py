"""Runs the matcher over the bank statement.

Run:  python -m matcher.run [--bank hdfc] [--env .env]
      (reads data/bank_statement.csv and data/schedules.json, writes data/decisions.csv)

--bank says whose narration layout the statement uses (matcher.match.FORMATS). The default is the
synthetic statement's; after statement.import_bank, pass the same bank. --env is the .env whose NAME_ORDER
(matcher.names) is used; without one, or with no NAME_ORDER line, names are read western (given name first).

Payer accounts people approved before (data/payer_links.csv, written by review.apply) are used when the file
exists; without it the decisions are exactly those of the rules alone.

It never reads answer_key.csv: only matcher.evaluate does. It never calls the AI reader: the review page's
Run tab does that and passes the fields in (run(bank, extracted, name_order)).
"""
import argparse
import csv
import json
from pathlib import Path

from locale_settings import from_env
from matcher.match import FORMATS, decide
from matcher.payer import current

DATA = Path(__file__).resolve().parents[1] / "data"
LINKS = "payer_links.csv"  # personal data: account identifiers (README, Learning from approvals)


def read_links(data: Path) -> dict:
    """matcher.payer.current() of data/payer_links.csv; {} when there is none."""
    path = data / LINKS
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return current(list(csv.DictReader(f)))


def run(bank: str = "synthetic", extracted: dict | None = None, data: Path | None = None,
        name_order: str = "western") -> list[dict]:
    """Decides every statement line and writes data/decisions.csv. Returns the decisions."""
    data = data or DATA
    with (data / "bank_statement.csv").open(newline="") as f:
        lines = list(csv.DictReader(f))
    decisions = decide(lines, json.loads((data / "schedules.json").read_text()), bank, extracted, name_order,
                       read_links(data))
    with (data / "decisions.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(decisions[0]))
        writer.writeheader()
        writer.writerows({**d, "candidates": ";".join(d["candidates"])} for d in decisions)
    return decisions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", default="synthetic", choices=sorted(FORMATS))
    parser.add_argument("--env", type=Path, default=DATA.parent / ".env", help="the .env holding NAME_ORDER")
    args = parser.parse_args()
    decisions = run(args.bank, name_order=from_env(args.env).name_order)
    counts = {o: sum(d["outcome"] == o for d in decisions) for o in ("post", "queue", "ignore")}
    print(f"lines: {len(decisions)} " + " ".join(f"{o}={n}" for o, n in counts.items()))


if __name__ == "__main__":
    main()
