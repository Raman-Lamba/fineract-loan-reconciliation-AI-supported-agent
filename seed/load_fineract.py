"""Loads the synthetic borrowers and loans into Fineract. Safe to re-run.

Run:  python -m seed.load_fineract [--limit N]

Every record carries our own reference as Fineract's externalId, and is looked
up by it before it is created, so a second run creates nothing twice.

Calls go through the connector's HTTP adapter. Its high-level tools
(create_client, create_loan, approve_and_disburse_loan) always use today's date
and set no externalId, so they cannot make loans that were disbursed months ago.
"""
import argparse
from datetime import date

from seed.connector import fineract_client as fc, ok
from seed.generate import generate

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
FMT = {"locale": "en", "dateFormat": "dd MMMM yyyy"}
PAYMENT_TYPES = ["UPI", "NEFT", "IMPS", "NACH", "CASH"]

# Same shape as the sandbox bootstrap's product (known to be accepted by this
# Fineract build), changed to INR, 1.5% a month on the declining balance.
PRODUCT = {
    "name": "Synthetic Co-Lent Personal Loan", "shortName": "SCPL", "currencyCode": "INR",
    "digitsAfterDecimal": 2, "inMultiplesOf": 0,
    "principal": 100000, "minPrincipal": 10000, "maxPrincipal": 500000,
    "numberOfRepayments": 12, "minNumberOfRepayments": 3, "maxNumberOfRepayments": 60,
    "repaymentEvery": 1, "repaymentFrequencyType": 2,
    "interestRatePerPeriod": 1.5, "interestRateFrequencyType": 2,
    "amortizationType": 1, "interestType": 0, "interestCalculationPeriodType": 1,
    "transactionProcessingStrategyCode": "mifos-standard-strategy", "loanScheduleType": "CUMULATIVE",
    "daysInYearType": 1, "daysInMonthType": 1, "isInterestRecalculationEnabled": False,
    "accountingRule": 1, "locale": "en",
}


def fdate(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day:02d} {MONTHS[d.month - 1]} {d.year}"


def find(resource: str, external_id: str):
    page = ok(fc.execute_get(resource, {"externalId": external_id}), f"Look up {resource} {external_id}")
    items = page.get("pageItems", []) if isinstance(page, dict) else page
    return items[0] if items else None


def ensure_currency():
    selected = [c["code"] for c in ok(fc.execute_get("currencies"), "Read currencies")["selectedCurrencyOptions"]]
    if "INR" not in selected:
        ok(fc.execute_put("currencies", {"currencies": selected + ["INR"]}), "Enable INR")


def ensure_payment_types() -> dict:
    existing = {p["name"]: p["id"] for p in ok(fc.execute_get("paymenttypes"), "Read payment types")}
    for position, name in enumerate(PAYMENT_TYPES, start=1):
        if name not in existing:
            body = {"name": name, "description": f"{name} (synthetic)", "isCashPayment": name == "CASH", "position": position}
            existing[name] = ok(fc.execute_post("paymenttypes", body), f"Create payment type {name}")["resourceId"]
    return existing


def ensure_product() -> int:
    for product in ok(fc.execute_get("loanproducts"), "Read loan products"):
        if product["shortName"] == PRODUCT["shortName"]:
            return product["id"]
    return ok(fc.execute_post("loanproducts", PRODUCT), "Create loan product")["resourceId"]


def ensure_client(borrower: dict, office_id: int, active_on: str) -> int:
    found = find("clients", borrower["ref"])
    if found:
        return found["id"]
    body = {"officeId": office_id, "firstname": borrower["firstname"], "lastname": borrower["lastname"],
            "mobileNo": borrower["mobile"], "externalId": borrower["ref"], "legalFormId": 1,
            "active": True, "activationDate": active_on, **FMT}
    return ok(fc.execute_post("clients", body), f"Create client {borrower['ref']}")["clientId"]


def ensure_loan(loan: dict, client_id: int, product_id: int) -> int:
    """Create, approve and disburse; a re-run picks up wherever the last one stopped."""
    on = fdate(loan["disbursed_on"])
    found = find("loans", loan["ref"])
    if found:
        loan_id, status = found["id"], found["status"]["value"]
    else:
        body = {"clientId": client_id, "productId": product_id, "externalId": loan["ref"], "loanType": "individual",
                "principal": loan["principal"], "loanTermFrequency": loan["months"], "loanTermFrequencyType": 2,
                "numberOfRepayments": loan["months"], "repaymentEvery": 1, "repaymentFrequencyType": 2,
                **{k: PRODUCT[k] for k in ("interestRatePerPeriod", "amortizationType", "interestType",
                                           "interestCalculationPeriodType", "transactionProcessingStrategyCode")},
                "submittedOnDate": on, "expectedDisbursementDate": on, **FMT}
        loan_id = ok(fc.execute_post("loans", body), f"Create loan {loan['ref']}")["loanId"]
        status = "Submitted and pending approval"
    if status == "Submitted and pending approval":
        ok(fc.execute_post(f"loans/{loan_id}?command=approve", {"approvedOnDate": on, **FMT}), f"Approve {loan['ref']}")
        status = "Approved"
    if status == "Approved":
        ok(fc.execute_post(f"loans/{loan_id}?command=disburse", {"actualDisbursementDate": on, **FMT}),
           f"Disburse {loan['ref']}")
    return loan_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="load only the first N loans (and their borrowers): a quick trial")
    args = parser.parse_args()

    data = generate()
    loans = data["loans"][: args.limit] if args.limit else data["loans"]
    wanted = {loan["borrower_ref"] for loan in loans}
    borrowers = [b for b in data["borrowers"] if b["ref"] in wanted]

    ensure_currency()
    payment_types = ensure_payment_types()
    product_id = ensure_product()
    office_id = ok(fc.execute_get("offices"), "Read offices")[0]["id"]
    print(f"product={product_id} office={office_id} payment types={sorted(payment_types)}")

    first_loan = {}
    for loan in loans:
        ref = loan["borrower_ref"]
        first_loan[ref] = min(first_loan.get(ref, loan["disbursed_on"]), loan["disbursed_on"])
    client_ids = {b["ref"]: ensure_client(b, office_id, fdate(first_loan[b["ref"]])) for b in borrowers}
    print(f"clients ready: {len(client_ids)}")
    for n, loan in enumerate(loans, start=1):
        ensure_loan(loan, client_ids[loan["borrower_ref"]], product_id)
        if n % 25 == 0 or n == len(loans):
            print(f"loans ready: {n}/{len(loans)}")


if __name__ == "__main__":
    main()
