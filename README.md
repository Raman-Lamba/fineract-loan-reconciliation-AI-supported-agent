# Reconciliation agent for a co-lending loan book

An AI agent that matches incoming bank payments to loans in Apache Fineract, posts them as repayments,
and splits each one between the lender and its partner bank. See [spec.md](spec.md) for the problem.
All data is synthetic. This is a portfolio project, not a production or regulator-approved system.

**Status: milestone 4 (clear payments are posted to Fineract and split 80:20 in a ledger; the review queue is
next).** Milestone 1 ran against a live
Fineract on 16 Nov 2026: 200 clients and 259 of 260 loans loaded (L0251-L0260 are same-name twins). Loan
L0202 is missing because an early trial run activated its borrower too late; a wipe and reseed (below)
would load all 260.

| Milestone | What | State |
|---|---|---|
| 1 | Sandbox, synthetic borrowers and loans | done |
| 2 | Bank statement + answer key from Fineract's schedules | built |
| 3 | Matcher: post, queue or ignore each line, with a reason | built |
| 4 | Post repayments (UTR as `externalId`), 80:20 split ledger | built |
| 5 | Review queue, audit log, accuracy report against the targets | next |

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
.\start.ps1 -Seed -Reconcile   # everything: also build the statement, match, post the clear payments, verify
```

`-Reconcile` runs `statement.fetch_schedules`, `statement.generate`, `matcher.run`, `matcher.evaluate`,
`posting.run` and `posting.verify` in that order (each is described below) and can be used without `-Seed` once the loans are loaded.

The script is safe to re-run after a reboot. On first run it clones the connector (pinned commit) into
`vendor/`, generates the sandbox secrets, starts the sandbox, writes `.env`, and creates `.venv`.
The first start takes about 3 minutes (Fineract's database migrations).

| What | Where |
|---|---|
| Mifos web app | http://localhost:4200 (user `mifos`, password `MIFOSX_PASSWORD` in `.env`) |
| Fineract API | http://127.0.0.1:4200/fineract-provider/api/v1 (proxied by the web app) |
| Stop | `docker compose stop` in `vendor\mcp-mifosx\agentic-mifos-sandbox` |
| Wipe all data | `docker compose down -v` in the same folder, then delete `.env` |

Sandbox needs (from its README, measured by its authors): about 2.6 GB of memory and about 5.6 GB of
images without local models; ports 4200, 4000, 8080 and 8090 on 127.0.0.1. We do not use its `ollama`
profile (a further 9.4 GB image, 5.2 GB of models, up to 6 GB of memory).

## Seed data

`seed/generate.py` builds 200 borrowers and 250 loans from a fixed random seed, so every run gives the
same data. Names come from a small pool, so several borrowers share a full name on purpose. Ten more
"twin" loans (L0251-L0260) copy the principal, term and disbursal date of a loan held by another borrower
with the same surname and first initial, so two look-alike borrowers owe the same amount on the same day.
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

## Matcher

```powershell
.venv\Scripts\python -m matcher.run        # statement + schedules -> data\decisions.csv (never reads the answer key)
.venv\Scripts\python -m matcher.evaluate   # decisions against the answer key, with the targets
```

Fixed rules, no language model: a repeated txn_id is ignored; a loan account in the narration names the
loan; otherwise the payer's name and the exact amount must fit one loan with an instalment due within 5
days. Two loans that fit, a look-alike borrower (same surname and first initial) owing the same amount, a
different amount, or no fit all go to the queue with the candidates and a reason. Rules:
[docs/superpowers/specs/2026-11-16-matcher-design.md](docs/superpowers/specs/2026-11-16-matcher-design.md).

Measured on 16 Nov 2026 (259 loans, 1,644 statement lines, 1,341 posts):

| Measure | Result | Target |
|---|---|---|
| Wrong auto-postings | 0 of 1,341 posts (0.00%) | under 0.5% |
| Auto-posted share | 1,341 of 1,341 lines the key says to post (100.0%) | at least 80% |
| Duplicates posted | 0 | 0 |

All 271 queued lines are ones the key also queues (86 ambiguous, 68 partial, 53 overpaid, 48 paid by
another person, 16 unrelated). The statement is synthetic and its name mangling follows a few fixed
patterns that the rules were written for, so these numbers say the rules are sound on this data, not how
they would do on a real bank feed.

## Posting

```powershell
.venv\Scripts\python -m posting.run --limit 5   # quick trial: the first 5 `post` decisions
.venv\Scripts\python -m posting.run             # all of them -> Fineract, data\ledger.csv, data\audit.csv
.venv\Scripts\python -m posting.verify          # the ledger against Fineract, and the totals
```

For each `post` decision, in order: skip it if its txn_id is already in the ledger; read the loan from
Fineract and, if the matched instalment is already paid or the loan is closed, queue it instead; post the
repayment with the line's date, `externalId` and `receiptNumber` = txn_id and the channel as payment type;
split it 80:20 (partner share rounded half-up to the paisa, the lender gets the rest, so the two always add
up); append one row to `data\ledger.csv` and one to `data\audit.csv` (posted, recovered, queued or
failed, with the reason and the approver). A failed post is logged and the run carries on. Steps:
[docs/superpowers/specs/2026-11-16-posting-design.md](docs/superpowers/specs/2026-11-16-posting-design.md).

It is safe to re-run. If a run stops after Fineract took a payment but before the ledger row was written,
the next run finds that payment by its `externalId` and records it without posting again (`recovered`).

Measured on 16 Nov 2026. The first full run posted all 1,341 `post` decisions (1 of them recovered, from
the trial that checked Fineract's duplicate guard); none was already paid and none failed. A second full
run posted nothing (`skipped (in ledger)=1341`). `posting.verify` then printed:

```
ledger rows checked against Fineract: 1341, problems: 0
payments posted: 1341
rupees posted:   18,129,477.71
partner share:   14,503,581.84
lender share:    3,625,895.87
loans in arrears: 194 of 259
```

Loans are still in arrears where a payment was queued for a person (271 lines), never arrived, or fell
due after the statement ends on 31 October.

## Findings that shape the design

Read from the Fineract source at the commit the sandbox pins (`apache/fineract@7ac780192`). The repayment
fields and the duplicate guard were then exercised against the running sandbox on 16 Nov 2026:

- A second repayment with the same `externalId` is refused with HTTP 400, `Failed data validation due to:
  value.must.be.unique.`
- `GET /loans/{loanId}/transactions/external-id/{externalId}` returns the transaction, or 404 if that loan
  has none with that id.
- Call the API at `127.0.0.1`, not `localhost`: on Windows each call to `localhost` waited about 2 seconds
  (IPv6 is tried first), which made a full posting run 20 times slower. `start.ps1` writes `127.0.0.1` into
  a new `.env`; in an older `.env`, change the host in `MIFOSX_BASE_URL` by hand.

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
