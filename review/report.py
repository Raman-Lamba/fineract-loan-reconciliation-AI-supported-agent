"""The partner bank's report: what each loan repaid each month and how it was split.

Run:  python -m review.report     (reads data/ledger.csv, writes data/partner_report.csv)
"""
import csv
import sys
from decimal import Decimal

FIELDS = ["month", "loan_ref", "payments", "amount", "partner_share", "lender_share"]
MONEY = FIELDS[3:]


def by_loan_month(ledger: list[dict]) -> list[dict]:
    groups: dict = {}
    for row in ledger:
        group = groups.setdefault((row["date"][:7], row["loan_ref"]),
                                  {"payments": 0, **dict.fromkeys(MONEY, Decimal(0))})
        group["payments"] += 1
        for column in MONEY:
            group[column] += Decimal(row[column])
    return [{"month": month, "loan_ref": ref, "payments": g["payments"], **{c: str(g[c]) for c in MONEY}}
            for (month, ref), g in sorted(groups.items())]


def _sums(rows: list[dict], digits: int = 2) -> list:
    """[payments, amount, partner share, lender share]. A ledger row is one payment."""
    zero = Decimal(0).quantize(Decimal(1).scaleb(-digits))
    return [sum(int(row.get("payments", 1)) for row in rows)] + [
        sum((Decimal(row[column]) for row in rows), zero) for column in MONEY]


def totals(rows: list[dict], ledger: list[dict], digits: int = 2) -> dict:
    """The report's totals row. Stops if it differs from the ledger: a report that disagrees must not go out."""
    mine, theirs = _sums(rows, digits), _sums(ledger, digits)
    if mine != theirs:
        sys.exit("STOP: the report's totals do not equal the ledger's (payments, amount, partner, lender): "
                 f"{', '.join(map(str, mine))} against {', '.join(map(str, theirs))}")
    return dict(zip(FIELDS, ["TOTAL", "", mine[0]] + [str(value) for value in mine[1:]]))


def main():
    from locale_settings import from_env
    from posting.run import DATA, read

    settings = from_env(DATA.parent / ".env")
    ledger = read("ledger.csv")
    rows = by_loan_month(ledger)
    total = totals(rows, ledger, settings.minor)
    out = DATA / "partner_report.csv"
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows + [total])
    print(f"partner report: {len(rows)} rows (one per loan per month), {len({r['month'] for r in rows})} months, "
          f"{len({r['loan_ref'] for r in rows})} loans -> {out}")
    print(f"amounts in {settings.currency}, one currency per run")
    print(f"payments:      {total['payments']}")
    print(f"amount:        {Decimal(total['amount']):,}")
    print(f"partner share: {Decimal(total['partner_share']):,}")
    print(f"lender share:  {Decimal(total['lender_share']):,}")
    print("totals equal the ledger's: yes")


if __name__ == "__main__":
    main()
