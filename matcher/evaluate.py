"""Accuracy report: the matcher's decisions against the answer key.

Run:  python -m matcher.evaluate     (reads data/decisions.csv and data/answer_key.csv)
"""
import csv
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
OUTCOMES = ("post", "queue", "ignore")


def evaluate(decisions: list[dict], key: list[dict]) -> dict:
    # A repeated txn_id has two key rows: the first statement line is the real payment, later ones
    # are the `duplicate` rows. `missed` rows have no statement line.
    real = {k["txn_id"]: k for k in key if k["case"] not in ("duplicate", "missed")}
    repeat = {k["txn_id"]: k for k in key if k["case"] == "duplicate"}
    seen = set()
    r = {"posts": 0, "wrong_posts": 0, "duplicates_posted": 0, "should_post": 0, "posted_right": 0, "table": {}}
    for d in decisions:
        k = repeat[d["txn_id"]] if d["txn_id"] in seen else real[d["txn_id"]]
        seen.add(d["txn_id"])
        r["table"].setdefault(k["case"], dict.fromkeys(OUTCOMES, 0))[d["outcome"]] += 1
        right = d["outcome"] == "post" == k["expected_outcome"] and d["loan_ref"] == k["expected_loan_ref"]
        r["should_post"] += k["expected_outcome"] == "post"
        r["posted_right"] += right
        if d["outcome"] == "post":
            r["posts"] += 1
            r["wrong_posts"] += not right
            r["duplicates_posted"] += k["case"] == "duplicate"
    return r


def _read(name: str) -> list[dict]:
    with (DATA / name).open(newline="") as f:
        return list(csv.DictReader(f))


def main():
    decisions = _read("decisions.csv")
    key = _read("answer_key.csv")
    r = evaluate(decisions, key)
    wrong = 100 * r["wrong_posts"] / max(r["posts"], 1)
    share = 100 * r["posted_right"] / max(r["should_post"], 1)
    met = lambda ok: "met" if ok else "MISSED"  # noqa: E731
    print(f"lines: {len(decisions)}  posts: {r['posts']}")
    print(f"wrong auto-postings: {r['wrong_posts']} of {r['posts']} posts = {wrong:.2f}%  (target < 0.5%: {met(wrong < 0.5)})")
    print(f"auto-posted share:   {r['posted_right']} of {r['should_post']} lines the key says to post = {share:.1f}%"
          f"  (target >= 80%: {met(share >= 80)})")
    print(f"duplicates posted:   {r['duplicates_posted']}  (target 0: {met(r['duplicates_posted'] == 0)})")
    print(f"\n{'case':<14}" + "".join(f"{o:>8}" for o in OUTCOMES) + "   key says")
    says = {k["case"]: k["expected_outcome"] for k in key}
    for case, row in sorted(r["table"].items(), key=lambda item: -sum(item[1].values())):
        print(f"{case:<14}" + "".join(f"{row[o]:>8}" for o in OUTCOMES) + f"   {says[case]}")


if __name__ == "__main__":
    main()
