# Reconciliation agent for a co-lending loan book

An AI agent that matches incoming bank payments to loans in Apache Fineract, posts them as repayments,
and splits each one between the lender and its partner bank. See [spec.md](spec.md) for the problem.
All data is synthetic. This is a portfolio project, not a production or regulator-approved system.

**Status: milestone 5, all five built (clear payments are posted to Fineract and split 80:20 in a ledger;
unclear ones wait in a queue a person works in Excel; the partner bank gets a report checked against the
ledger).** On 16 Nov 2026 a wipe and one `.\start.ps1 -Seed -Reconcile` rebuilt everything in 15
minutes: 200 clients, all 260 loans (L0251-L0260 are same-name twins), 1,348 lines auto-posted with 0
wrong, 273 queued for a person, ledger and partner report equal to Fineract.

| Milestone | What | State |
|---|---|---|
| 1 | Sandbox, synthetic borrowers and loans | done |
| 2 | Bank statement + answer key from Fineract's schedules | built |
| 3 | Matcher: post, queue or ignore each line, with a reason | built |
| 4 | Post repayments (UTR as `externalId`), 80:20 split ledger | built |
| 5 | Review queue a person edits in Excel, partner report, end-to-end demo | built |
| UI | Local review page for the same queue, and a setup guide with a live connection test ([Review page](#review-page)) | built |

Connecting it to a Fineract and Mifos of your own: [Connect to your own Fineract and Mifos](#connect-to-your-own-fineract-and-mifos).

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
.\start.ps1 -Seed -Reconcile   # everything: statement, match, post the clear payments, verify, review queue, partner report
```

`-Reconcile` runs `statement.fetch_schedules`, `statement.generate`, `matcher.run`, `matcher.evaluate`,
`posting.run`, `posting.verify`, `review.build` and `review.report` in that order (each is described below)
and can be used without `-Seed` once the loans are loaded. If `data\bank_export.csv` exists it imports that
file instead of generating the synthetic statement ([Use your own bank statement](#use-your-own-bank-statement))
and prints which source it used. It never runs `review.apply`: posting what a
person decided is a separate step ([Review queue](#review-queue)).

`.\start.ps1 -Ui` does the same start-up checks and then serves the [review page](#review-page) at
http://127.0.0.1:8000 until you press Ctrl+C. It can be combined with the other switches and runs last.

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
.venv\Scripts\python -m statement.generate --hdfc   # the same, plus data\bank_export_hdfc.csv (HDFC layout, to test the import)
```

The statement (`txn_id, date, amount, narration`) is the only thing the agent sees. The answer key says
what a careful person would do with each line: post it to a named loan, queue it for a person (partial
or overpaid amounts, a payer who is not the borrower, two possible borrowers, unknown credits), or
ignore it (a repeated txn_id). Its last column, `true_loan_ref`, is the loan whose borrower really paid;
only the demo reviewer reads it. Cases and outcomes:
[docs/superpowers/specs/2026-11-16-bank-statement-design.md](docs/superpowers/specs/2026-11-16-bank-statement-design.md).

## Use your own bank statement

1. In your bank's net banking, download the account statement as CSV.
2. Save it as `data\bank_export.csv`. Leave it as the bank made it: the account details above the table and
   the summary below it are skipped.
3. Add a line `BANK=hdfc` to `.env` (the layout of the export; `hdfc` is the default and the only one so far).
4. Run `.\start.ps1 -Reconcile`. It prints `Statement source: data\bank_export.csv`, then imports, matches,
   posts the clear lines, verifies the ledger and writes the review queue and partner report. There is no
   accuracy report: a real statement has no answer key. To go back to the synthetic statement, delete or
   rename `data\bank_export.csv`.

The same by hand:

```powershell
.venv\Scripts\python -m statement.import_bank data\bank_export.csv --bank hdfc   # -> data\bank_statement.csv
.venv\Scripts\python -m matcher.run --bank hdfc                                  # the same bank, or no name is read
```

The import keeps credits only and prints `rows read`, `credits kept` and `debits skipped`. Columns are found
by their header names, in any order. `txn_id` is the `Chq./Ref.No.` cell without its leading zeros; if that
cell is empty or all zeros, it is the one number of 12 or more digits in the narration. Dates (dd/mm/yy)
become `2026-09-05`, amounts (`1,21,940.65`) become `121940.65`.

**It stops instead of guessing.** A credit with no usable reference, or an amount or date it cannot read,
ends the import with `REFUSED, nothing written` and the row number as Excel shows it. Nothing is written,
so no money line is dropped quietly. Fix or remove that row in the export and run it again. A cash deposit
with no reference number is such a row: record it by hand in Fineract.

**What is verified and what is not**

| Part | State |
|---|---|
| Synthetic narrations (`UPI/..`, `NEFT-..`, `IMPS/P2A/..`, `NACH/..`) | measured against the answer key |
| HDFC export columns, dates, amounts; HDFC narrations (`UPI-NAME-..`, `NEFT CR-IFSC-NAME-..`, `IMPS-ref-NAME-..`) | built from secondary sources and tested only on a synthetic HDFC-layout file we generate. **No real HDFC export has been through it.** |
| ICICI narrations (`UPI/ref/../NAME`, `INF/NEFT/UTR/../NAME`, `MMT/IMPS/ref/../NAME`), SBI narrations (`NEFT-CR-..`, `IMPS-..`) | from secondary sources, the name position partly assumed; unit tests on made-up lines only. There is no ICICI or SBI column layout yet, so `import_bank` accepts only `--bank hdfc`. |

Before trusting it with a real book: run a real export through `import_bank`, read the first queue it
produces, and correct the bank's line in `FORMATS` (`matcher/match.py`, narration layouts) or `LAYOUTS`
(`statement/import_bank.py`, column names). Adding a bank is one line in each. A wrong name position cannot
post on its own: a post still needs the exact instalment amount, a date within 5 days and one borrower who
fits. Design: [docs/superpowers/specs/2026-11-16-bank-import-design.md](docs/superpowers/specs/2026-11-16-bank-import-design.md).

**Tested through the import path** (16 Nov 2026). `statement.generate --hdfc` also writes the synthetic
statement as an HDFC-layout export, `data\bank_export_hdfc.csv`: account details on top, 41 bank-charge debits
among the credits, zero-padded references (80 UPI rows have none in the column), 372 narrations with a comma,
a summary at the bottom. `data\bank_statement.csv` and `data\answer_key.csv` come out byte for byte as before
(same sha256 with and without `--hdfc`). Importing the export printed `rows read: 1695 credits kept: 1654
debits skipped: 41` and gave the same txn_id, date and amount on all 1,654 lines. `matcher.run --bank hdfc`
on it made the same decision on every line as on the synthetic statement: 1,348 posts, 0 wrong, 100.0%
auto-posted, 0 duplicates posted. `posting.run` then posted nothing (all 1,348 already in the ledger).

## Matcher

```powershell
.venv\Scripts\python -m matcher.run        # statement + schedules -> data\decisions.csv (never reads the answer key)
.venv\Scripts\python -m matcher.evaluate   # decisions against the answer key, with the targets
```

Fixed rules, no language model: a repeated txn_id is ignored; a loan account in the narration names the
loan; otherwise the payer's name and the exact amount must fit one loan with an instalment due within 5
days. Where the name sits in a narration comes from a small table, one entry per format and one line per
bank (`FORMATS` in `matcher/match.py`; `--bank` picks the line, the synthetic layout by default). Two loans that fit, a look-alike borrower (same surname and first initial) owing the same amount, a
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

For each `post` decision, in order: skip it if its txn_id is already in the ledger or was queued for a
person by an earlier run; read the loan from
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
run posted nothing (all 1,341 skipped as already in the ledger). `posting.verify` then printed:

```
ledger rows checked against Fineract: 1341, problems: 0
payments posted: 1341
rupees posted:   18,129,477.71
partner share:   14,503,581.84
lender share:    3,625,895.87
loans in arrears: 194 of 259
```

Loans are still in arrears where a payment was queued for a person (271 lines), never arrived, or fell
due after the statement ends on 31 October. The review queue below deals with the 271.

## Review queue

```powershell
.venv\Scripts\python -m review.build    # -> data\queue.csv: every line a person has to decide
.venv\Scripts\python -m review.apply    # posts or rejects the rows a person has decided
.venv\Scripts\python -m review.report   # -> data\partner_report.csv, checked against the ledger
```

`review.build` puts two kinds of line in `data\queue.csv`: those the matcher queued, and those posting found
already paid. Each row has the bank line, the reason it is there, and the candidate loans with the borrower's
name and the instalment due nearest the payment (`L0189 Rajesh Kumar: 26314.16 due 2026-02-20`). A line the
matcher found no loan for has no candidates; the person looks the payer up in Fineract. Design:
[docs/superpowers/specs/2026-11-16-review-queue-design.md](docs/superpowers/specs/2026-11-16-review-queue-design.md).

**How a person works the queue**

1. Open `data\queue.csv` in Excel.
2. On each row you can decide, fill in the last four columns and leave the others alone:
   - `decision`: `post` or `reject`
   - `loan_ref`: the loan to post to, such as `L0189` (not needed for `reject`)
   - `approver`: your name
   - `note`: why. It becomes the reason in the audit log, as `manual: <note>`.
3. Leave a row blank to decide it later. Save (keep the CSV format) and close Excel.
4. Run `.venv\Scripts\python -m review.apply`. This posts money to Fineract, so `start.ps1` never runs it.

What `review.apply` does with each decided row:

- **`post`** goes through the same posting code as the automatic postings: the same duplicate guard, 80:20
  split, ledger row and audit row, with your name as the approver. The amount is posted as the bank sent it,
  even if it is less or more than the instalment, because a person approved it.
- **`reject`** writes a `rejected` row to the audit log and the line leaves the queue. Nothing is posted.
- **A row it cannot apply** (no approver, a loan_ref that is not a loan, a decision other than post or reject,
  or Fineract refused the payment) stays in the queue with `PROBLEM: ...` at the start of `note`. Fix the row
  and run it again.

Good to know:

- The date and amount posted come from the bank statement, not from `queue.csv`. Excel rewrites dates
  (`2026-02-06` becomes `06-02-2026`) and that does no harm.
- `txn_id` is written as `UTR507296189749`. Without the letters Excel saves a 12-digit number as `5.07296E+11`
  and the id is lost (both seen with Excel on 16 Nov 2026). Do not edit that column.
- It is safe to run `review.build` and `review.apply` as often as you like. A rebuild keeps what you filled in
  and drops lines that are now in the ledger or rejected. A line in the ledger is never posted again, even if
  an old copy of `queue.csv` is put back.
- `posting.run` now logs an already-paid line once. Earlier it added a `queued` audit row on every run.

**Partner report.** `data\partner_report.csv` has one row per loan per month (`month, loan_ref, payments,
amount, partner_share, lender_share`) and a `TOTAL` row at the bottom. If the totals differ from the ledger's,
`review.report` stops with an error instead of writing the report.

**Demo (synthetic data only).** `review.simulate` stands in for the person. It fills in `queue.csv` from the
answer key's `true_loan_ref` column (the loan whose borrower really paid): `post` to that loan for ambiguous,
partial, overpaid and paid-by-someone-else lines, `reject` for unrelated credits, approver
`demo reviewer (simulated)`. It never overwrites a decision that is already there.

```powershell
.venv\Scripts\python -m review.simulate   # fill in queue.csv from the answer key
.venv\Scripts\python -m review.apply      # post and reject what it decided
.venv\Scripts\python -m posting.verify
.venv\Scripts\python -m review.report
```

Measured on 16 Nov 2026, against the live sandbox:

| Step | Result |
|---|---|
| `review.build` | 271 lines: 121 "amount differs: partial or overpaid", 86 "2 loans fit", 64 "no loan fits" |
| `review.simulate` | 255 `post`, 16 `reject` |
| `review.apply` | 255 posted (3,908,968.81 rupees), 16 rejected, 0 problems, 0 left in the queue |
| `review.apply` again | nothing to do. With the 271 decided rows put back (a copy saved from Excel): 271 "applied earlier", ledger and audit files unchanged |
| `matcher.evaluate` | the same as before: 0 wrong of 1,341 posts, 100.0% auto-posted, 0 duplicates |

`posting.verify`, then `review.report`:

```
ledger rows checked against Fineract: 1596, problems: 0
payments posted: 1596
rupees posted:   22,038,446.52
partner share:   17,630,756.98
lender share:    4,407,689.54
loans in arrears: 144 of 259
```

```
partner report: 1589 rows (one per loan per month), 10 months, 259 loans -> data\partner_report.csv
payments:      1596
amount:        22,038,446.52
partner share: 17,630,756.98
lender share:  4,407,689.54
totals equal the ledger's: yes
```

Loans in arrears fell from 194 to 144. All 144 are explained, read loan by loan from Fineract:

- 76 still owe an instalment due on or before 31 October. Every one has a missed instalment (31 loans), a
  partial payment (56 loans) or both (11 of them). Posting a partial payment as it is leaves the rest of that
  instalment overdue, which is correct.
- 68 owe only an instalment due 1-15 November. The statement ends on 31 October, so no payment for it has
  been seen yet.

Five loans now show as `Overpaid` in Fineract because overpaid lines were posted in full. What to do with
the extra (refund it or hold it) is a decision this project leaves to a person.

## Review page

A local web page for the same queue, for people who would rather click than edit a CSV. Python's standard
library only: nothing to install, no build step.

```powershell
.\start.ps1 -Ui                          # sandbox up, then the page at http://127.0.0.1:8000
.venv\Scripts\python -m ui.serve         # the page alone (Fineract already running); --port 8010 for another port
.venv\Scripts\python -m ui.serve --demo  # a made-up queue and a made-up Fineract: try it without posting anywhere
```

- **Review tab.** Every line in `data\queue.csv` with its date, amount, narration and the reason it is there.
  The candidate loans sit side by side (borrower, loan_ref, the instalment due and its amount), each with a
  link that opens the loan in Mifos. Choose a candidate or type a loan_ref, add a note, then Approve or
  Reject. The Approver box at the top is required and is remembered by the browser.
- **It is the CSV workflow, not a second one.** A click writes `decision`, `loan_ref`, `approver` and `note`
  into `data\queue.csv`, exactly as a person would in Excel, then runs `review.apply` on that one row. So the
  duplicate guard, the 80:20 split, the ledger row and the audit row are the same code. Rows other people
  filled in Excel are left alone until someone runs `review.apply`. If `review.apply` refuses a row (unknown
  loan_ref, no approver, Fineract said no), the problem appears on the row and stays in the file.
- **Summary strip.** Payments posted, rupees, the partner bank's and the lender's share, queue size, and a
  Count button for loans in arrears (it reads every loan from Fineract and checks the ledger against it, as
  `posting.verify` does; about 20 seconds for 260 loans). The partner report link rebuilds
  `data\partner_report.csv` from the ledger and downloads it.
- **Setup tab** (http://127.0.0.1:8000/#setup). A read-only connection test and the guide in the next section.
- **Safety.** It listens on 127.0.0.1 only and refuses a request whose Host header is not this machine.
  Anything that changes data is a POST carrying a token made new each run and written into the page. The
  Fineract password is never sent to the browser or logged.
- **Limit.** One local user, no login: the approver is whatever name is typed. More than one reviewer, or
  any use beyond this machine, needs real accounts first, because that name is the audit trail.

Design: [docs/superpowers/specs/2026-11-16-ui-design.md](docs/superpowers/specs/2026-11-16-ui-design.md).

**What was checked** (16 Nov 2026). Against the live sandbox, over HTTP: the page, the queue (empty: the
sandbox is fully reconciled), the summary (1,605 payments, 22,058,241.68 rupees), all seven connection checks
passing in 1.7 seconds, the arrears count (145 of 260, 0 ledger problems, 22 seconds), the partner report
download, and the refusals (wrong Host, no token, wrong token, a txn_id not in the queue). Nothing was
approved or rejected on the sandbox: `ledger.csv` and `audit.csv` had the same sha256 before and after.
Approve, reject and a refused row were run over HTTP in `--demo` mode and in the unit tests, not against the
sandbox. The page was rendered in headless Chrome (both tabs); nobody has clicked through it in a browser yet.

## Connect to your own Fineract and Mifos

The same guide is on the page's Setup tab, with a "Test connection" button.

```
  YOUR SIDE (already running)                THIS AGENT (this machine)

  Bank -- statement CSV -------------------> data\bank_export.csv
                                                   | import, then match
  Fineract -- loans + schedules (read) ----> matcher: post / queue / ignore
     ^                                             |
     |---- repayment, externalId = UTR <---- clear lines (automatic)
     |---- repayment, externalId = UTR <---- unclear lines, after a person approves
     |                                             |
  Mifos web app (shows the same loans)       data\ledger.csv (80:20 split), data\audit.csv,
  staff see each repayment on the loan       data\queue.csv, data\partner_report.csv
```

Fineract is the loan system. Mifos is its web app: the agent never talks to Mifos, it only links to it.
The agent reads loans and schedules from Fineract's API and writes one thing back: repayments.

### Steps

1. **Make a Fineract user for the agent. Do not use the admin account.** In Mifos: Admin > System > Manage
   Roles and Permissions, create a role with only these permissions; then Admin > Users, create a user with
   that role in the office that holds the loans.

   | Permission | Why the agent needs it |
   |---|---|
   | `READ_LOAN` | List loans, read schedules and transactions, find a repayment by its external id |
   | `REPAYMENT_LOAN` | Post a repayment. This is the only thing it writes. |
   | `READ_PAYMENTTYPE` | Look up the UPI / NEFT / IMPS / NACH payment types |
   | `READ_CURRENCY` | Only for the INR check on the Setup tab |
   | `READ_CLIENT` | Only for `start.ps1`'s start-up check, which lists one client |

   These five codes are in Fineract's own permission list (`GET /permissions` on the sandbox,
   `apache/fineract@7ac780192`, 16 Nov 2026). **Not tested: a full run as a user holding only these.** The
   sandbox was only ever used as its admin. Run "Test connection" as the new user first; a missing
   permission shows there as error 403.
2. **Get the code ready.** `.\start.ps1` does it, but it also needs Docker and starts the sandbox. Without
   Docker, these are the same steps by hand (not run separately from `start.ps1` so far):

   ```powershell
   git -c core.autocrlf=false clone https://github.com/openMF/mcp-mifosx.git vendor\mcp-mifosx
   git -C vendor\mcp-mifosx checkout be806dbd36daf510dcd787d911dfeab4471a1a8c
   python -m venv .venv
   .venv\Scripts\python -m pip install -r vendor\mcp-mifosx\python\requirements.txt
   ```
3. **Put the connection in `.env`** in the project folder (git-ignored; copy `.env.example`). The sandbox
   values are the example:

   | Setting | Meaning | Sandbox value |
   |---|---|---|
   | `MIFOSX_BASE_URL` | Fineract's API address, ending in `/fineract-provider/api/v1` | `http://127.0.0.1:4200/fineract-provider/api/v1` |
   | `MIFOSX_TENANT_ID` | The Fineract tenant | `default` |
   | `MIFOSX_USERNAME` | The agent's Fineract user | `mifos` (the sandbox admin) |
   | `MIFOSX_PASSWORD` | Its password | written by `start.ps1` |
   | `BANK` | Layout of your bank export, `data\bank_export.csv` | `hdfc` (the only one so far) |
   | `MIFOS_URL` | Optional. The Mifos web app's address, for the "Open in Mifos" links, if it is not the host in `MIFOSX_BASE_URL` | not set |
   | `MIFOSX_SKIP_TLS_VERIFY` | Optional. `true` only for an https test server with a self-signed certificate | not set |

   `start.ps1` only writes `.env` when there is none, so your values are kept.
4. **Start the page and test:** `.venv\Scripts\python -m ui.serve`, open http://127.0.0.1:8000/#setup, press
   "Test connection". It only reads (one login call, then GETs) and reports seven checks as pass or fail,
   each with its fix: Fineract answers at the URL, the tenant exists, the login works, INR is enabled, the
   four payment types exist, loans exist (and how many have an externalId), a loan's schedule can be read.
5. **Read the loans:** `.venv\Scripts\python -m statement.fetch_schedules` writes `data\schedules.json`.
6. **Bring a bank statement** ([Use your own bank statement](#use-your-own-bank-statement)), import and match
   it. Nothing is posted yet. Read `data\decisions.csv` before going on.

   ```powershell
   .venv\Scripts\python -m statement.import_bank data\bank_export.csv --bank hdfc
   .venv\Scripts\python -m matcher.run --bank hdfc
   ```
7. **Post five, look at them in Mifos, then the rest:**

   ```powershell
   .venv\Scripts\python -m posting.run --limit 5
   .venv\Scripts\python -m posting.run
   .venv\Scripts\python -m posting.verify
   .venv\Scripts\python -m review.build     # the unclear lines, for the Review tab or Excel
   ```

**Do not run `.\start.ps1 -Seed`, `-Seed -Reconcile` or `seed.load_fineract` against a real Fineract.** The
seed is for the demo sandbox only: it enables currencies and creates payment types, a loan product, 200
clients and 260 back-dated loans. Against your own Fineract use the commands above, or `.\start.ps1
-Reconcile` without `-Seed` once `data\bank_export.csv` is in place and you have done a trial with `--limit`.

### What the agent expects from your Fineract

| In Fineract | How the agent uses it |
|---|---|
| Each loan's **External Id** | Your own loan reference. It becomes `loan_ref` everywhere: the matcher, the queue, the ledger and the partner report. A loan with no External Id is known by its Fineract account number instead (such as `000000042`). Excel drops the leading zeros of such a number, so decide those lines on the review page, not in Excel. |
| The **client's display name** on the loan | Matched against the payer's name in the bank narration. The first word is taken as the first name and the rest as the surname. |
| The loan's **repayment schedule** | Each instalment's due date and total due. A line is posted automatically only if its amount equals an instalment exactly and its date is within 5 days of the due date. |
| The loan's **account number** | A narration containing `SCPL` + the 9-digit account number names the loan directly. The prefix is the synthetic lender's; another one is a one-line change in `matcher/match.py`. |
| **Payment types** named UPI, NEFT, IMPS, NACH | The channel found in the narration is posted as the payment type. A missing type does not stop the posting; the repayment just has none. |
| **INR** as an enabled currency | Amounts are rupees with two decimals. Loans in another currency are not supported. |
| Repayment **External Id** | Each repayment is posted with the bank's UTR as the transaction's External Id (and as its receipt number). Fineract keeps that unique, so one bank line can never be posted twice, even from a second machine. |

**What the agent never does.** It never creates or changes clients, loans, products, currencies or payment
types (only the demo seed does). It never reverses or edits a repayment. It never posts a line that fits two
loans, an amount that is not an instalment, or anything a person rejected. Its only writing call is
`POST /loans/{id}/transactions?command=repayment`.

Until this change `statement.fetch_schedules` skipped loans with no External Id. It now takes them with their
account number as `loan_ref` (covered by a unit test; the sandbox has no such loan, all 260 have an External
Id, so it has not run against a live one).

### Where to see the results in Mifos

- Open a loan: `http://localhost:4200/#/clients/{clientId}/loans-accounts/{loanId}/transactions` (use your
  Mifos address). The two numbers are Fineract's client id and loan id; both are in `data\schedules.json`.
  The review page's "Open in Mifos" links use the same pattern ending in `/general`.
- On the loan's **Transactions** tab each repayment the agent posted has the bank's UTR in the **External
  Id** column. Open the transaction for its payment details: payment type and receipt number (the UTR again).
- The loan's **Repayment Schedule** tab shows the instalment as paid.
- From the API: `GET /loans/{loanId}/transactions/external-id/{UTR}`.

How this was verified: the URL pattern, the Transactions columns and the payment details panel were read
from the code of the Mifos web app the sandbox runs (`openmf/web-app:1.14`: hash routing, routes
`clients/:clientId` > `loans-accounts` > `:loanId` > `general` / `transactions` / `transactions/:id`). They were
not clicked through in a logged-in browser. The menu names in step 1 come from the same code.

### Troubleshooting

| What you see | What to do |
|---|---|
| `Connection failed`, or the first check fails | Fineract is not running or `MIFOSX_BASE_URL` is wrong. It must end in `/fineract-provider/api/v1`. Restart the page after changing `.env`. |
| `Error 400` on every call | Unknown tenant: fix `MIFOSX_TENANT_ID`. |
| `Error 401` | Wrong user or password in `.env`. |
| `Error 403` | The user lacks a permission (step 1) or cannot see the loans' office. |
| Everything is slow (2 seconds a call) | Use `127.0.0.1` instead of `localhost` in `MIFOSX_BASE_URL`. |
| `Connector not found in vendor/mcp-mifosx` | Do step 2. |
| `loan_ref '...' is not a known loan` | The loan is newer than `data\schedules.json`, or the reference is mistyped. Run `statement.fetch_schedules` again. |
| `queue.csv is open in another program` | Close it in Excel, then press the button again. |
| `this txn_id is already in Fineract, but not on this loan` | That UTR was posted to another loan earlier. Find it in Mifos before deciding; the agent will not post it a second time. |
| The queue is empty | Nothing is waiting. New unclear lines arrive with the next `.\start.ps1 -Reconcile` or `review.build`. |
| "this page is out of date: reload it" | The page's server was restarted. Reload. |
| The Mifos link asks you to log in | Log in to Mifos once in that browser at the address the link uses (`127.0.0.1` and `localhost` count as different sites), then click again. |
| `cannot listen on 127.0.0.1:8000` | Another program has the port: `python -m ui.serve --port 8010`. |

## Run the whole thing

From an empty machine to a reconciled book, in order. The first command does steps 1 to 9 of the list below (about 15 minutes from a wiped sandbox).

```powershell
.\start.ps1 -Seed -Reconcile
.venv\Scripts\python -m review.simulate    # demo only; a real reviewer edits data\queue.csv in Excel instead
.venv\Scripts\python -m review.apply
.venv\Scripts\python -m posting.verify
.venv\Scripts\python -m review.report
.venv\Scripts\python -m ui.serve          # optional: the review page, http://127.0.0.1:8000
```

The same thing one module at a time, with the sandbox already running:

```powershell
.venv\Scripts\python -m seed.load_fineract          # 1. borrowers and loans
.venv\Scripts\python -m statement.fetch_schedules   # 2. instalments from Fineract
.venv\Scripts\python -m statement.generate          # 3. bank statement and answer key
.venv\Scripts\python -m matcher.run                 # 4. post, queue or ignore each line
.venv\Scripts\python -m matcher.evaluate            # 5. accuracy against the answer key
.venv\Scripts\python -m posting.run                 # 6. post the clear lines, split 80:20
.venv\Scripts\python -m posting.verify              # 7. ledger against Fineract
.venv\Scripts\python -m review.build                # 8. queue.csv for a person
.venv\Scripts\python -m review.report               # 9. partner report
.venv\Scripts\python -m review.simulate             # 10. demo only: fill in the queue
.venv\Scripts\python -m review.apply                # 11. post or reject what was decided
.venv\Scripts\python -m posting.verify              # 12. ledger against Fineract again
.venv\Scripts\python -m review.report               # 13. partner report with the manual postings
.venv\Scripts\python -m unittest discover -s tests  # the tests; no Fineract needed
```

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
