# Reconciliation agent for a co-lending loan book

An AI agent that matches incoming bank payments to loans in Apache Fineract, posts them as repayments,
and splits each one between the lender and its partner bank. See [spec.md](spec.md) for the problem.
All data is synthetic. This is a portfolio project, not a production or regulator-approved system.

**Status: milestone 2 (synthetic bank statement with an answer key).** Milestone 1 ran against a live
Fineract on 16 Nov 2026: 200 clients and 249 of 250 loans loaded. Loan L0202 is missing because an early
trial run activated its borrower too late; a wipe and reseed (below) would load all 250.

| Milestone | What | State |
|---|---|---|
| 1 | Sandbox, synthetic borrowers and loans | done |
| 2 | Bank statement + answer key from Fineract's schedules | built |
| 3 | Matcher: post, queue or ignore each line, with a reason | next |
| 4 | Post repayments (UTR as `externalId`), 80:20 split ledger | |
| 5 | Review queue, audit log, accuracy report against the targets | |

## Terms

- **Fineract**: Apache's open-source core banking system; the system of record for clients and loans.
- **MCP**: Model Context Protocol, the standard way to give an AI model tools. The
  [openMF connector](https://github.com/openMF/mcp-mifosx) exposes Fineract as MCP tools.
- **UTR**: the bank's unique reference for a payment (our `txn_id`).
- **Co-lending**: two lenders fund one loan in a fixed ratio and share its repayments.

## Start (or restart) everything

Needs: Docker Desktop (WSL 2 backend) running, Git for Windows, Python 3.11+.

```powershell
.\start.ps1          # start Fineract, confirm the login, list one client and one loan
.\start.ps1 -Seed    # the same, then load the synthetic borrowers and loans
```

The script is safe to re-run after a reboot. On first run it clones the connector (pinned commit) into
`vendor/`, generates the sandbox secrets, starts the sandbox, writes `.env`, and creates `.venv`.
The first start takes about 3 minutes (Fineract's database migrations).

| What | Where |
|---|---|
| Mifos web app | http://localhost:4200 (user `mifos`, password `MIFOSX_PASSWORD` in `.env`) |
| Fineract API | http://localhost:4200/fineract-provider/api/v1 (proxied by the web app) |
| Stop | `docker compose stop` in `vendor\mcp-mifosx\agentic-mifos-sandbox` |
| Wipe all data | `docker compose down -v` in the same folder, then delete `.env` |

Sandbox needs (from its README, measured by its authors): about 2.6 GB of memory and about 5.6 GB of
images without local models; ports 4200, 4000, 8080 and 8090 on 127.0.0.1. We do not use its `ollama`
profile (a further 9.4 GB image, 5.2 GB of models, up to 6 GB of memory).

## Seed data

`seed/generate.py` builds 200 borrowers and 250 loans from a fixed random seed, so every run gives the
same data. Names come from a small pool, so several borrowers share a full name on purpose.
`seed/load_fineract.py` loads them: INR currency, five payment types (UPI, NEFT, IMPS, NACH, CASH), one
loan product, then clients and back-dated loans. Each record carries our reference (`B0001`, `L0001`) as
Fineract's `externalId` and is looked up before it is created, so a re-run adds nothing twice.

```powershell
.venv\Scripts\python -m seed.load_fineract --limit 5   # quick trial
python -m unittest discover -s tests                   # generator tests, no Fineract needed
```

## Bank statement

```powershell
.venv\Scripts\python -m statement.fetch_schedules   # every loan's instalments -> data\schedules.json
.venv\Scripts\python -m statement.generate          # -> data\bank_statement.csv, data\answer_key.csv
```

The statement (`txn_id, date, amount, narration`) is the only thing the agent sees. The answer key says
what a careful person would do with each line: post it to a named loan, queue it for a person (partial
or overpaid amounts, a payer who is not the borrower, two possible borrowers, unknown credits), or
ignore it (a repeated txn_id). Cases and outcomes:
[docs/superpowers/specs/2026-11-16-bank-statement-design.md](docs/superpowers/specs/2026-11-16-bank-statement-design.md).

## Findings that shape the design

Read from the Fineract source at the commit the sandbox pins (`apache/fineract@7ac780192`), not yet
exercised against a running server.

**Where the bank txn_id goes: `externalId` on the repayment.**
`POST /loans/{id}/transactions?command=repayment` accepts `transactionDate`, `transactionAmount`,
`externalId`, `note`, `paymentTypeId`, `accountNumber`, `checkNumber`, `routingCode`, `receiptNumber`,
`bankNumber` (`LoanTransactionValidatorImpl.getRepaymentParameters`).

| Field | Limit | Unique in the database? |
|---|---|---|
| `externalId` | 100 chars | Yes: constraint `external_id_UNIQUE` on `m_loan_transaction` |
| `receiptNumber`, `bankNumber`, `checkNumber`, `routingCode`, `accountNumber` | 50 chars each | No |
| `note` | free text | No |

Only `externalId` makes Fineract itself refuse a second posting of the same payment. Plan: `externalId`
= UTR (the guard), `receiptNumber` = UTR again (it is what the web app shows), `paymentTypeId` = channel.

**The connector's repayment tool cannot carry it.** `make_loan_repayment(loan_id, amount,
payment_type_id)` always posts with today's date and sends no `externalId` or `receiptNumber`. We call
the connector's own HTTP adapter (`tools.mcp_adapter.fineract_client`) for repayments and seeding. The
connector is used as it is, not modified.

**Co-lending: no built-in support for a proportional split.** Fineract has an optional investor module
(`fineract-investor`, "external asset owner") that records the sale of a *whole* loan to another owner
and its buy-back. It has no per-loan participation ratio and does not divide repayments. The 80:20
split ledger is ours to build, outside Fineract.

**RBI.** The 80:20 ratio is an illustration, not a rule. RBI's Co-Lending Arrangements Directions, 2025
(in force 1 January 2026) require each lender to keep at least 10% of every loan and the borrower to be
charged a blended interest rate; the 2020 model they replace required the NBFC to keep at least 20%.
These points come from law-firm summaries, not the RBI text. This project does not claim RBI compliance.

**Targets.** The accuracy targets in the plan (under 0.5% wrong auto-postings, at least 80% auto-posted,
no duplicates, and so on) are goals to measure against, not results.
