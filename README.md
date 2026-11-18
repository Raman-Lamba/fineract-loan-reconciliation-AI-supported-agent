# Reconciliation agent for a co-lending loan book

A program that matches the payments arriving in a lender's bank account to the right borrower's loan, records each one as
a repayment in the loan system, and splits it between the lender and its partner bank. Fixed rules decide every payment.
A person decides the unclear ones. An optional AI reader can help read messy bank statements, but it never decides.

All data in this project is synthetic. It is a portfolio project, not a production system, and no regulator has approved it.

## Contents

1. [What it is and the problem it solves](#what-it-is-and-the-problem-it-solves)
2. [What it does, and what it never does](#what-it-does-and-what-it-never-does)
3. [Where to start](#where-to-start)
4. [Quick start](#quick-start)
5. [Daily use: the page, tab by tab](#daily-use-the-page-tab-by-tab)
6. [Connect to your own Fineract and Mifos](#connect-to-your-own-fineract-and-mifos)
7. [Use your own bank statement](#use-your-own-bank-statement)
8. [The optional AI reader](#the-optional-ai-reader)
9. [Going global](#going-global)
10. [The review queue, suggestions and learning](#the-review-queue-suggestions-and-learning)
11. [How it works inside](#how-it-works-inside)
12. [Measured results and honest limits](#measured-results-and-honest-limits)
13. [Troubleshooting](#troubleshooting)
14. [Project structure, tests and contributing](#project-structure-tests-and-contributing)
15. [Reference](#reference)
16. [Author](#author) and [License](#license)

## What it is and the problem it solves

A lender gets hundreds of payments a day into its bank account. Each one has to be matched to the right borrower's loan and
recorded in the loan system. The bank's descriptions of the payments are messy, so staff do this by hand in Excel. Until a
payment is matched, the borrower looks overdue even though they paid.

In a **co-lending** arrangement two lenders fund one loan in a fixed ratio and share its repayments. Every repayment then has
to be split between them too, and the partner bank needs a report it can check.

**One worked example** (from [spec.md](spec.md)):

- **Input.** A bank line reads `UPI/432198/RAMESH KUM/okaxis/emi sept`, Rs 8,450, 5 September.
- **What the agent does.** It finds Ramesh Kumar's loan with an Rs 8,450 instalment due on 5 September and records the
  repayment.
- **Output.** The loan is no longer overdue. With an 80:20 split, Rs 6,760 is marked as owed to the partner bank and Rs
  1,690 to the lender. The log shows why it matched.
- **When it is not sure.** If two borrowers named Ramesh both owed Rs 8,450, it does not guess. It puts the line in a queue
  for a person, with both candidates and its reasoning.

What this gives a lender:

- Staff handle only the unclear cases, not every line.
- Overdue status is right the same day, so nobody gets a wrong collections call.
- Less money sits unexplained in the holding account.
- The partner bank gets its correct share, with a report an auditor can check against the ledger.
- Every action has a recorded reason and approver.

**Terms used in this guide**

- **Fineract**: Apache's open-source core banking system. It holds the clients and loans. **Mifos** is its web app, the
  screen your staff use.
- **UTR**: the bank's unique reference for a payment. Here it is called `txn_id`.
- **Narration**: the free-text description the bank puts on each statement line.
- **UPI, NEFT, IMPS, NACH**: Indian payment channels (Unified Payments Interface, National Electronic Funds Transfer,
  Immediate Payment Service, National Automated Clearing House).
- **camt.053** and **MT940**: two standard bank statement file formats (ISO 20022 XML, and SWIFT text).
- **IBAN**: the international bank account number. **VPA**: a UPI address such as `name@bank`.
- **MCP**: Model Context Protocol, a standard way to give an AI model tools. The
  [openMF connector](https://github.com/openMF/mcp-mifosx) exposes Fineract that way. This project uses its HTTP adapter.

## What it does, and what it never does

**What it does**

- Reads a bank statement (CSV in any layout, camt.053 or MT940) and the loans and instalments in Fineract.
- For each credit, decides one of three things, with a reason: **post** it, **queue** it for a person, or **ignore** it (a
  repeat of a line it already has).
- Posts the clear payments to Fineract as repayments and splits each between lender and partner.
- Gives a person a queue of the unclear lines, with candidate loans and a reason, to approve or reject.
- Writes a partner report and checks it against the ledger and Fineract.

**What it never does**

- **It never guesses.** A line posts only if its amount equals an instalment exactly, the date is within 5 days of the due
  date, exactly one borrower's name fits, and no look-alike borrower owes the same amount. Anything else goes to a person.
- **It never posts the same payment twice.** The bank's UTR is sent to Fineract as the repayment's external id, and Fineract
  refuses a second one. The ledger is checked first too.
- **The AI model only reads.** It can fill in a payer's name or a statement layout. The fixed rules then decide as if they
  had read it themselves. It never chooses a loan, posts or approves.
- **Nothing is posted without the rules or a person.** The only thing it writes to Fineract is a repayment. It never creates
  or changes clients, loans or products, and never reverses a repayment.
- It stops instead of guessing on a statement it cannot read: a credit with an unreadable amount or date refuses the whole
  import and writes nothing.
- It is not legal or compliance advice. The 80:20 split is an example; check your own rules.

**What is built**

- Synthetic borrowers and loans, loaded into a local Fineract sandbox, and a synthetic bank statement with an answer key.
- Statement import: CSV (HDFC by its column names, any other layout by a column map), camt.053 and MT940.
- The matcher, posting with an 80:20 (or your own) split ledger, the review queue, the partner report and a ledger check.
- A local web page with five tabs (Connect, Statement, Run, Review, Guide) and a one-click `Start.bat` for Windows.
- An optional AI reader (Claude, ChatGPT, Gemini or a model on your own computer) for layouts and narrations.
- Settings for other countries (currency, date and number formats, channels, name order), a no-partner mode, names from
  other countries, suggestions for unclear lines, and learning from approvals.

## Where to start

| You want to | Do this | Needs |
|---|---|---|
| **See the page in one minute** | `git clone` this repo, then `python -m ui.serve --demo`, then open http://127.0.0.1:8000 | Python 3.11+ only. Made-up data in a temporary folder; nothing is posted anywhere. |
| **Use it without typing commands** | Double-click `Start.bat`, then follow the tabs in the browser ([Windows one-click](#windows-one-click-startbat)) | Windows 10/11, Docker Desktop (WSL 2), Git for Windows, Python 3.11+ |
| **Run the whole thing on a sandbox** | `.\start.ps1 -Seed -Reconcile` ([Full command-line run](#full-command-line-run)) | The same as above, with PowerShell |
| **Connect it to your own Fineract** | The page's Connect tab, or [Connect to your own Fineract and Mifos](#connect-to-your-own-fineract-and-mifos) | A Fineract you can log in to |
| **Feed it your own bank statement** | The Statement tab, or [Use your own bank statement](#use-your-own-bank-statement) | A CSV, camt.053 or MT940 export |
| **Use it outside India** | The Connect tab's "Country and money" box ([Going global](#going-global)) | A statement in one currency |
| **Check the code works** | `python -m unittest discover -s tests` (438 tests, about 70 seconds, no Fineract needed) | Python 3.11+ only |
| **Understand how it works** | [How it works inside](#how-it-works-inside) and [docs/reference.md](docs/reference.md) | Nothing |

The scripts `start.ps1` and `Start.bat` are Windows-only. The Python modules are plain standard-library code, so on macOS or
Linux you can run them one at a time against any Fineract ([Full command-line run](#full-command-line-run)).

## Quick start

### Try the demo (no Docker, no Fineract)

```powershell
git clone <this repository>
cd Leverage
python -m ui.serve --demo
```

Open http://127.0.0.1:8000. The page shows a made-up review queue backed by a made-up Fineract. It uses a temporary folder
and posts nowhere. Press Ctrl+C in the terminal to stop it. `--port 8010` uses another port if 8000 is taken.

### Windows one-click (Start.bat)

Needs Docker Desktop (WSL 2 backend), Git for Windows and Python 3.11+, installed once. Make sure Docker Desktop says
"Engine running". Then double-click **`Start.bat`** in the project folder.

It starts a local Fineract sandbox (the first time takes about 3 minutes, because Fineract sets up its database), makes
the settings file `.env` and a Python environment `.venv` if they are missing, and opens http://127.0.0.1:8000 in your
browser. Keep its window open: closing it stops the page. If something is missing, the window stays open with a red `STOP`
line saying what to do. Fix that and double-click again. The page opens even when the connection check fails, so the Connect
tab can fix it.

`Start.bat` loads no borrowers or loans. To fill the sandbox with the synthetic ones, run the full command-line run once.

### Full command-line run

Needs the same programs, with PowerShell.

```powershell
.\start.ps1                    # start Fineract, confirm the login, list one client and one loan
.\start.ps1 -Seed              # the same, then load the synthetic borrowers and loans
.\start.ps1 -Seed -Reconcile   # everything: statement, match, post the clear payments, verify, queue, report
.\start.ps1 -Ui                # start the sandbox, then serve the page at http://127.0.0.1:8000 (Ctrl+C to stop)
```

About 15 minutes from a wiped sandbox. The script is safe to re-run. On first run it clones the connector (at a fixed
commit) into `vendor/`, generates the sandbox secrets, starts the sandbox, writes `.env` and creates `.venv`. `-Reconcile`
runs `statement.fetch_schedules`, `statement.generate`, `matcher.run`, `matcher.evaluate`, `posting.run`, `posting.verify`,
`review.build` and `review.report` in that order. It never runs `review.apply`, because that posts what a person decided. If
`data\bank_export.csv` exists, `-Reconcile` imports that instead of generating the synthetic statement.

The same thing one module at a time, with the sandbox already running (`.venv\Scripts\python` on Windows):

```powershell
.venv\Scripts\python -m seed.load_fineract          # 1. borrowers and loans (sandbox only)
.venv\Scripts\python -m statement.fetch_schedules   # 2. instalments from Fineract -> data\schedules.json
.venv\Scripts\python -m statement.generate          # 3. synthetic bank statement and answer key
.venv\Scripts\python -m matcher.run                 # 4. post, queue or ignore each line -> data\decisions.csv
.venv\Scripts\python -m matcher.evaluate            # 5. accuracy against the answer key (synthetic data only)
.venv\Scripts\python -m posting.run                 # 6. post the clear lines, split by PARTNER_SHARE_PERCENT
.venv\Scripts\python -m posting.verify              # 7. ledger against Fineract
.venv\Scripts\python -m review.build                # 8. data\queue.csv for a person
.venv\Scripts\python -m review.report               # 9. partner report
.venv\Scripts\python -m review.simulate             # 10. demo only: a stand-in for the person fills in the queue
.venv\Scripts\python -m review.apply                # 11. post or reject what was decided
.venv\Scripts\python -m ui.serve                    # the page
```

When the sandbox is up:

| What | Where |
|---|---|
| Mifos web app | http://localhost:4200 (user `mifos`, password `MIFOSX_PASSWORD` in `.env`) |
| Fineract API | http://127.0.0.1:4200/fineract-provider/api/v1 (proxied by the web app) |
| Stop | `docker compose stop` in `vendor\mcp-mifosx\agentic-mifos-sandbox` |
| Wipe all data | `docker compose down -v` in the same folder, then delete `.env` |

The sandbox needs about 2.6 GB of memory and about 5.6 GB of images (its authors' figures), and ports 4200, 4000, 8080 and
8090 on 127.0.0.1.

**The seed is for the sandbox only.** `seed/generate.py` builds 200 borrowers and 250 loans from a fixed random seed, so
every run gives the same data. Several borrowers share a full name on purpose, and ten "twin" loans (L0251 to L0260) copy
the amount and dates of a loan held by a look-alike borrower. `seed.load_fineract` creates INR, five payment types, a loan
product, clients and back-dated loans. Never run it against a real Fineract.

## Daily use: the page, tab by tab

This is for a person who does not use a terminal. Start the page with `Start.bat` (or `python -m ui.serve`). The tabs run in
order, left to right: **Connect, Statement, Run, Review, Guide**. When the page opens it shows the Review tab if everything is
connected and the Connect tab if not.

### 1. Connect

Tells the agent where your Fineract is and (optionally) which AI service and which country.

- **What to do.** Fill in Fineract's address (ending in `/fineract-provider/api/v1`), the tenant, user and password. On the
  sandbox they are already filled in. Optionally add the Mifos address (for the "Open in Mifos" links) and your bank. Press
  **Save**. It writes these into `.env`. Lines it does not know are kept. A password or key is never shown again, only "Saved"
  or "Not set". Then press **Test connection**.
- **A good result.** Seven read-only checks all pass: Fineract answers, the tenant exists, the login works, your currency is
  enabled, the payment channels are listed, loans exist, and a loan's schedule can be read. A failure shows its fix.
- The AI reader and the "Country and money" box are also here; see [The optional AI reader](#the-optional-ai-reader) and
  [Going global](#going-global).

### 2. Statement

Brings in your bank statement.

- **What to do.** Choose your bank's export file (CSV, camt.053 XML or MT940, up to 5 MB). Press **Read my statement**. The
  page finds the columns, shows the first rows with the chosen columns marked, and says how many credits and debits it reads.
  Any column can be corrected from a dropdown. Press **Looks right, import it**.
- **A good result.** It says how many credits it kept, and writes `data\bank_statement.csv`. A row it cannot read is shown with
  its row number and nothing is imported.

### 3. Run

Reads the loans, decides each line, and posts the clear ones.

- **What to do.** Press **Read the loans** (read-only). Press **Match**: a table shows how many lines would post, go to a person
  or be ignored, and why, with the amounts. Nothing is posted yet. Read it. Then press **Post the clear ones**. The page asks
  you to confirm the number of payments and the total. Then it posts, checks the ledger against Fineract and rebuilds the
  queue.
- **A good result.** The ledger check reports no problems. Pressing either button twice is safe: what is in the ledger is never
  posted again. If the numbers changed between your look and your confirmation, the post is refused.

### 4. Review

The unclear lines, for a person to decide.

- **What to do.** Type your name in the **Approver** box at the top (required; the browser remembers it). For each line, read
  the reason it is here. Choose one of the candidate loans (or type a loan_ref), add a note, and press **Approve and post** or
  **Reject**. A line with no candidate shows likely loans. Each candidate has an "Open in Mifos" link.
- **A good result.** An approved line leaves the queue and appears in the ledger. A rejected line leaves with nothing posted.
  The summary strip shows payments posted, the amount, the partner's and the lender's share and the queue size. **Count**
  reads every loan from Fineract and shows how many are in arrears (about 20 seconds for 260 loans). The partner report link
  rebuilds `data\partner_report.csv` from the ledger and downloads it.
- More in [The review queue, suggestions and learning](#the-review-queue-suggestions-and-learning).

### 5. Guide

The same help as this README in short: the Fineract user to create, what the agent expects from your Fineract, and
troubleshooting. It is also reachable at http://127.0.0.1:8000/#setup.

**About safety.** The page listens on 127.0.0.1 only and refuses a request whose Host header is not this machine. Anything
that changes data carries a token made new each run. The Fineract password and API keys are never sent to the browser or
logged. It has no login: the approver is whatever name is typed, so it suits one local user. More than one reviewer, or any use
beyond this machine, needs real accounts first, because that name is the audit trail. Other options: `--env FILE` and `--data
DIR` point the page at another `.env` and data folder (a copy, for a trial), and `--open` opens the browser.

## Connect to your own Fineract and Mifos

```
  YOUR SIDE (already running)                THIS AGENT (this machine)

  Bank -- statement file ------------------> Statement tab (data\bank_statement.csv)
                                                   | import, then match
  Fineract -- loans + schedules (read) ----> matcher: post / queue / ignore
     ^                                             |
     |---- repayment, externalId = UTR <---- clear lines (automatic)
     |---- repayment, externalId = UTR <---- unclear lines, after a person approves
     |                                             |
  Mifos web app (shows the same loans)       data\ledger.csv (the split), data\audit.csv,
  staff see each repayment on the loan       data\queue.csv, data\partner_report.csv
```

Fineract is the loan system. Mifos is its web app: the agent never talks to Mifos, it only links to it. The agent reads loans
and schedules from Fineract's API and writes one thing back: repayments.

### Steps

1. **Make a Fineract user for the agent. Do not use the admin account.** In Mifos: Admin > System > Manage Roles and
   Permissions, create a role with only these permissions; then Admin > Users, create a user with that role in the office
   that holds the loans.

   | Permission | Why the agent needs it |
   |---|---|
   | `READ_LOAN` | List loans, read schedules and transactions, find a repayment by its external id |
   | `REPAYMENT_LOAN` | Post a repayment. This is the only thing it writes. |
   | `READ_PAYMENTTYPE` | Look up the payment types named in `CHANNELS` (UPI / NEFT / IMPS / NACH for India) |
   | `READ_CURRENCY` | Only for the currency check on the Connect tab |
   | `READ_CLIENT` | Only for `start.ps1`'s start-up check, which lists one client |

   These five codes are in Fineract's own permission list (read from the sandbox on 16 Nov 2026). **Not tested: a full run as
   a user holding only these.** The sandbox was only ever used as its admin. Run "Test connection" as the new user first; a
   missing permission shows there as error 403.
2. **Get the code ready.** `.\start.ps1` does it, but it also needs Docker and starts the sandbox. Without Docker, these are
   the same steps by hand (not run separately from `start.ps1` so far):

   ```powershell
   git -c core.autocrlf=false clone https://github.com/openMF/mcp-mifosx.git vendor\mcp-mifosx
   git -C vendor\mcp-mifosx checkout be806dbd36daf510dcd787d911dfeab4471a1a8c
   python -m venv .venv
   .venv\Scripts\python -m pip install -r vendor\mcp-mifosx\python\requirements.txt
   ```
3. **Put the connection in `.env`**: fill in the Connect tab and press Save, or copy `.env.example` to `.env` and edit it
   (the file is git-ignored). The sandbox values are the example:

   | Setting | Meaning | Sandbox value |
   |---|---|---|
   | `MIFOSX_BASE_URL` | Fineract's API address, ending in `/fineract-provider/api/v1` | `http://127.0.0.1:4200/fineract-provider/api/v1` |
   | `MIFOSX_TENANT_ID` | The Fineract tenant | `default` |
   | `MIFOSX_USERNAME` | The agent's Fineract user | `mifos` (the sandbox admin) |
   | `MIFOSX_PASSWORD` | Its password | written by `start.ps1` |
   | `BANK` | Whose narrations your bank export has: `hdfc`, `icici`, `sbi` or `other` | `hdfc` |
   | `MIFOS_URL` | Optional. The Mifos web app's address, for the "Open in Mifos" links, if it is not the host in `MIFOSX_BASE_URL` | not set |
   | `MIFOSX_SKIP_TLS_VERIFY` | Optional. `true` only for an https test server with a self-signed certificate | not set |

   The AI and country settings are in [The optional AI reader](#the-optional-ai-reader) and [Going global](#going-global).
   `start.ps1` only writes `.env` when there is none, so your values are kept.
4. **Start the page and test:** `.venv\Scripts\python -m ui.serve`, open http://127.0.0.1:8000/#connect, press "Test
   connection". It only reads (one login call, then GETs).
5. **Read the loans:** the Run tab's "Read the loans", or `.venv\Scripts\python -m statement.fetch_schedules`. Either writes
   `data\schedules.json`.
6. **Bring a bank statement** ([Use your own bank statement](#use-your-own-bank-statement)), import and match it. Nothing is
   posted yet. Read `data\decisions.csv` before going on.

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

**Do not run `.\start.ps1 -Seed`, `-Seed -Reconcile` or `seed.load_fineract` against a real Fineract.** The seed is for the
demo sandbox only: it enables currencies and creates payment types, a loan product, 200 clients and 260 back-dated loans.
Against your own Fineract use the commands above, or `.\start.ps1 -Reconcile` without `-Seed` once `data\bank_export.csv` is
in place and you have done a trial with `--limit`.

### What the agent expects from your Fineract

| In Fineract | How the agent uses it |
|---|---|
| Each loan's **External Id** | Your own loan reference. It becomes `loan_ref` everywhere: the matcher, the queue, the ledger and the partner report. A loan with no External Id is known by its Fineract account number instead (such as `000000042`); this is covered by a unit test but has not run against a live loan. Excel drops the leading zeros of such a number, so decide those lines on the Review tab, not in Excel. |
| The **client's display name** on the loan | Matched against the payer's name in the bank narration. By default the first word is the first name and the rest the surname (`NAME_ORDER` changes this). |
| The loan's **repayment schedule** | Each instalment's due date and total due. A line is posted automatically only if its amount equals an instalment exactly and its date is within 5 days of the due date. |
| The loan's **account number** | A narration containing `SCPL` + the 9-digit account number names the loan directly. The prefix is the synthetic lender's; another one is a one-line change in `matcher/match.py`. |
| **Payment types** named like your `CHANNELS` | The channel found in the narration is posted as the payment type. A missing type does not stop the posting; the repayment just has none. |
| Your **currency** (`CURRENCY`) as an enabled currency | One currency per run, with that currency's own decimals. Loans or statement lines in another currency are refused, never converted. |
| Repayment **External Id** | Each repayment is posted with the bank's UTR as the transaction's External Id (and as its receipt number). Fineract keeps that unique, so one bank line can never be posted twice, even from a second machine. |

The agent never creates or changes clients, loans, products, currencies or payment types (only the demo seed does). It never
reverses or edits a repayment. Its only writing call is `POST /loans/{id}/transactions?command=repayment`.

### Where to see the results in Mifos

- Open a loan: `http://localhost:4200/#/clients/{clientId}/loans-accounts/{loanId}/transactions` (use your Mifos address).
  The two numbers are Fineract's client id and loan id; both are in `data\schedules.json`. The Review tab's "Open in Mifos"
  links use the same pattern ending in `/general`.
- On the loan's **Transactions** tab each repayment the agent posted has the bank's UTR in the **External Id** column. Open
  the transaction for its payment type and receipt number (the UTR again).
- The loan's **Repayment Schedule** tab shows the instalment as paid.
- From the API: `GET /loans/{loanId}/transactions/external-id/{UTR}`.

These paths and menu names were read from the code of the Mifos web app the sandbox runs, not clicked through in a logged-in
browser.

## Use your own bank statement

The no-typing way is the Statement tab ([Daily use](#2-statement)). Three kinds of file are accepted. Which one a file is gets
read from its content, not its name (`.csv`, `.xml`, `.sta`, `.mt940` and `.txt` can all be chosen). All three become the
same `data\bank_statement.csv` (`txn_id, date, amount, narration`, credits only), so matching, posting and review work the
same way afterwards.

| Format | What it carries | How it is read |
|---|---|---|
| **CSV, any layout** | Whatever columns the bank chose; the payer's name is somewhere inside a free-text narration | HDFC's columns by their names. Any other layout by a column map you choose once on the Statement tab (or that the optional AI reader proposes). Names by the bank's line in `FORMATS` in `matcher/match.py`. |
| **camt.053** (ISO 20022 XML, versions 02 to 12) | Per entry: credit or debit, reversal flag, booked or pending, dates, amount and currency, the bank's reference, the payer's name and IBAN (or UK sort code and account), remittance text | Field by field. Debits, reversals and entries not yet booked are skipped and counted. |
| **MT940** (SWIFT text) | Per `:61:` line: dates, credit/debit or reversal mark, amount, references; the `:86:` line holds the payer and remittance in a bank's own dialect | The German `?nn` subfields give name, account and remittance. Any other `:86:` is kept as free text with no name. The currency comes from the opening balance (`:60F:`). |

### A CSV by hand

1. In your bank's net banking, download the account statement as CSV.
2. Save it as `data\bank_export.csv`. Leave it as the bank made it: the account details above the table and the summary below
   it are skipped.
3. Add a line `BANK=hdfc` to `.env` (whose narrations the export has: `hdfc`, `icici`, `sbi` or `other`). For any bank but
   `hdfc` the columns come from a map confirmed once on the Statement tab; without one the import stops and says so.
4. Run `.\start.ps1 -Reconcile`. It prints `Statement source: data\bank_export.csv`, then imports, matches, posts the clear
   lines, verifies the ledger and writes the review queue and partner report. There is no accuracy report, because a real
   statement has no answer key. To go back to the synthetic statement, delete or rename `data\bank_export.csv`.

Or one step at a time:

```powershell
.venv\Scripts\python -m statement.import_bank data\bank_export.csv --bank hdfc   # -> data\bank_statement.csv
.venv\Scripts\python -m matcher.run --bank hdfc                                  # the same bank, or no name is read
```

The import keeps credits only and prints `rows read`, `credits kept` and `debits skipped`. Columns are found by their header
names, in any order. `txn_id` is the `Chq./Ref.No.` cell without its leading zeros; if that cell is empty or all zeros, it is
the one number of 12 or more digits in the narration. Dates (dd/mm/yy for India) become `2026-09-05`, amounts (`1,21,940.65`)
become `121940.65`.

**It stops instead of guessing.** A credit whose amount or date it cannot read ends the import with `REFUSED, nothing
written` and the row number as Excel shows it. Fix or remove that row and run it again.

**A credit with no reference** (a cash deposit, a cheque with no number) does not stop the import. It is kept under a made-up
id such as `NOREF-2026-09-06-1a2b3c4d` (the date and a hash of the row, so a re-import gives the same id). The matcher never
posts such a line, whatever else fits: it goes to the review queue. If a person approves it, it is posted with that id as
Fineract's external id, so the duplicate guard still holds, and the audit log says no bank reference was used.

### camt.053 and MT940

For these two the narration is written as `payer name | remittance | account | channel`, and the match reads the name from the
first field (`BANK=camt053` or `BANK=mt940`, set by the import). The reference is the bank's own. A credit with none gets a
`NOREF-` id as in a CSV. The import prints `entries read`, `credits kept`, `debits skipped`, `reversals skipped` and `entries
without a reference`, and the page shows the same counts. A statement in a currency other than `CURRENCY` is refused with the
entry named; a camt.052 or camt.054 file is refused with the list of supported formats. A batch booking (several payments in
one entry) is kept as one line for a person. No AI service is used for these formats.

XML is read with Python's standard library only, after refusing any file with a `DOCTYPE` or `ENTITY` declaration (this blocks
the "billion laughs" and external-entity attacks). Files are capped at 20 MB (5 MB through the page) and 100,000 entries.
Nothing is fetched from the network.

### What is verified and what is not

| Part | State |
|---|---|
| Synthetic narrations (`UPI/..`, `NEFT-..`, `IMPS/P2A/..`, `NACH/..`) | Measured against the answer key. |
| HDFC export columns, dates, amounts; HDFC narrations | Built from secondary sources and tested only on a synthetic HDFC-layout file we generate. **No real HDFC export has been through it.** |
| ICICI and SBI narrations | From secondary sources, the name position partly assumed; unit tests on made-up lines only. |
| Any other column layout (the ICICI-, SBI- and Axis-style test files in `tests/fixtures/`) | A column map chosen on the Statement tab and checked against every row. The three test files are hand-made from secondary research notes, **unverified**. |
| camt.053 and MT940 | Written from the public format descriptions as remembered. The test files are made up with made-up people, **format details unverified against real bank files**. Banks differ in which optional fields they fill and in their `:86:` dialect. |

**Before trusting it with a real book:** import a real export from your own bank, look at the preview (is the payer column
filled?) and read the first review queue. If the bank's narration layout is wrong, correct its line in `FORMATS`
(`matcher/match.py`) or `LAYOUTS` (`statement/import_bank.py`). Adding a bank's narrations is one line in `FORMATS`; its
columns need no code, a confirmed column map is enough. A wrong name position cannot post on its own, because a post still
needs the exact instalment amount, a date within 5 days and one borrower who fits.

Designs: [bank import](docs/superpowers/specs/2026-11-16-bank-import-design.md),
[camt.053 and MT940](docs/superpowers/specs/2026-11-18-camt-mt940-design.md).

## The optional AI reader

Optional. Without it everything works: HDFC exports are found by themselves, and the columns of any other layout are chosen
by hand from dropdowns.

**What it does.** It reads two things the fixed rules cannot: **a statement layout it has not seen** (which column is the date,
the narration, the amount), and **narrations the rules cannot read** (it returns the payer's name, the channel and the
reference). Every answer is checked against the file, and a person confirms a layout. **It never chooses a loan, posts or
approves.** The rules then decide as if they had read the name themselves. If the model's answer is wrong or missing, the line
goes to a person; it costs time, never a wrong posting.

**How to turn it on.** On the Connect tab choose a service, paste its key (write-only: it is never shown again), choose a
model or a Quality, and tick the consent box. Press **Test AI**: it sends one fixed question with no statement data and says
whether it worked.

| Service (`AI_PROVIDER`) | Key line in `.env` |
|---|---|
| Claude (`claude`, the default) | `ANTHROPIC_API_KEY` |
| ChatGPT / OpenAI (`openai`) | `OPENAI_API_KEY` |
| Gemini (`gemini`) | `GEMINI_API_KEY` |
| Other (`other`): any OpenAI-compatible service, with `AI_BASE_URL` and `AI_MODEL` | `AI_API_KEY` (not needed for a model on this computer) |

**Quality (Claude only).** `AI_QUALITY` is `balanced` (the default), `best` or `cheapest`. It chooses the model for each task
so you do not need to know model names. `AI_MODEL` names one model for every task and overrides Quality.

**A model on your own computer.** Choose Other, address `http://localhost:11434/v1` (Ollama's OpenAI-compatible address), a
model you have pulled, no key. Then no narration leaves the computer. Any other address must be `https://`.

**Privacy and consent.** Before the first use the page shows what is sent, to whom and with which model, and asks for a tick.
Without the tick no request is made. Changing the service asks again. For a layout, the first 20 rows are sent with every
digit replaced by 9 (no amount, balance, date value or account number). For narrations, only the narration text of lines the
rules could not read, which can include people's names. Never sent: amounts, balances, account numbers, anything from
`.env`. A key goes only to its own service.

**Cost.** A statement is roughly 1,600 short narrations, read about 40 to a request. The price is per token, so check your
provider's price list. That figure is an estimate, not a measurement, and nothing here is a measured cost. Answers are cached
in `data\ai_cache.json`, so a re-run costs nothing, and a confirmed layout is stored in `data\layouts.json` so the same bank
format is never sent twice.

**Not verified.** No live call to any AI provider has been made. Everything was tested with fake responses. Model names and
request details were read from the providers' documentation on 2026-11-17, and some request fields are unverified.

The full details (model tables, what exactly is sent, how requests are made, what was checked) are in
[docs/reference.md](docs/reference.md#ai-reader-in-detail). Design:
[AI reader](docs/superpowers/specs/2026-11-17-ai-reader-design.md).

## Going global

Everything country-specific is a setting, not a code change. Type it on the Connect tab's **Country and money** box (it is
saved into `.env`). The defaults are India's, so a project with none of these lines behaves as an Indian one.

| Setting | Meaning | Default |
|---|---|---|
| `COUNTRY` | A preset that fills the boxes below as a starting point: India, United Kingdom, Eurozone (SEPA), United States, Brazil, Kenya, or Other (nothing filled in). Change any of them afterwards. | India |
| `CURRENCY` | ISO 4217 code. The connection test checks it is enabled in your Fineract and that your loans are in it. | INR |
| `CO_LENDING` | `yes` or `no`: do you share your loans with a partner bank? | yes |
| `PARTNER_SHARE_PERCENT` | Co-lending only. The partner bank's share, 0 to 100 (decimals allowed). | 80 |
| `DATE_ORDER` | `day-first`, `month-first` or `auto` (the whole file decides; if it cannot, the import is refused and you choose). | day-first (India); `auto` for other presets |
| `NUMBER_FORMAT` | `1,234.56`, `1.234,56`, `1 234,56`, `1'234.56` or `auto` (same rule: refused, not guessed). | 1,234.56 (India); `auto` for other presets |
| `CHANNELS` | Payment-type names your Fineract uses, comma-separated. | UPI,NEFT,IMPS,NACH |
| `NAME_ORDER` | `western`, `family-first` or `auto`: which word of a borrower's name in Fineract is the given name. | western |

**Presets are a starting point, not facts.** None except India's was checked against a real bank export or a real Fineract.
Check what a preset fills in and change what is wrong. One currency per run: loans, statement, ledger and reports are all in
`CURRENCY`, and nothing is converted. A loan or statement line in another currency is refused. Not yet global: the `SCPL`
loan-account prefix is the synthetic lender's, and the demo seed is India-only.

### No partner bank (single lender)

If you do not co-lend, set **Do you share your loans with a partner bank?** to **No** on the Connect tab (or `CO_LENDING=no`).
Matching, posting, the queue, the duplicate guard and the audit log work as before. Every repayment is recorded as 100% the
lender's: `data\ledger.csv` keeps its columns, `partner_share` is zero and `lender_share` equals `amount`. `review.report`
writes `data\repayments_report.csv` instead of the partner report. Changing the mode later applies to new payments only; the
Connect tab asks you to tick a confirmation if the ledger already has rows, and the change is written to the audit log.

### Names from other countries

Whether a payer's name fits a borrower is decided by `matcher/names.py`, with no network and no AI. It forgives what is the
same person written differently: case and accents, word order, titles (Mr, Dr, Shri...), punctuation, name particles (van
der, bin, al...), double surnames, initials, a middle name in the payer, family-name-first order, joint payers ("A AND B"),
and whole names in non-Latin scripts. It deliberately does **not** handle transliteration between scripts, nicknames, married
or maiden names, or a payer who drops a middle name; those go to a person. A payer with a company suffix (LTD, LLC, GMBH...)
never fits a person. A wrong setting can only queue more lines, never post a wrong one. The full table is in
[docs/reference.md](docs/reference.md#names-the-matcher-handles). Unverified: none of this was checked against real bank data
from any country, and every test person is made up.

**Not legal or compliance advice.** Co-lending rules and the partner's share differ by country and by agreement; the 80 is an
illustration. Check yours with whoever is responsible for it. The agent only records the split you tell it to.

More: [locale settings in detail](docs/reference.md#locale-settings-in-detail). Designs:
[locale](docs/superpowers/specs/2026-11-17-global-locale-design.md),
[no partner](docs/superpowers/specs/2026-11-17-no-partner-design.md),
[names](docs/superpowers/specs/2026-11-18-names-design.md).

## The review queue, suggestions and learning

### The queue

`review.build` puts two kinds of line in `data\queue.csv`: those the matcher queued, and those posting found already paid.
Each row has the bank line, the reason it is there, and the candidate loans with the borrower's name and the instalment due
nearest the payment. You can work the queue on the Review tab or in Excel.

**In Excel.**

1. Open `data\queue.csv`.
2. On each row you can decide, fill in the last columns and leave the others alone: `decision` (`post` or `reject`),
   `loan_ref` (the loan to post to, not needed for `reject`), `approver` (your name), `note` (why; it becomes the reason in
   the audit log as `manual: <note>`), and optionally `remember` (write `no` to approve without learning, see below).
3. Leave a row blank to decide it later. Save as CSV and close Excel.
4. Run `.venv\Scripts\python -m review.apply`. This posts money to Fineract, so `start.ps1` never runs it.

What `review.apply` does: **`post`** goes through the same posting code as the automatic postings (duplicate guard, split,
ledger row, audit row, your name as approver). The amount is posted as the bank sent it, even if it differs from the
instalment, because a person approved it. **`reject`** writes a `rejected` row to the audit log and the line leaves the queue.
A row it cannot apply (no approver, an unknown loan_ref, Fineract refused) stays in the queue with `PROBLEM: ...` at the start
of `note`. Fix it and run again.

Good to know:

- The date and amount posted come from the bank statement, not from `queue.csv`.
- `txn_id` is written like `UTR507296189749`. Without the letters Excel saves a 12-digit number as `5.07296E+11` and the id is
  lost. Do not edit that column.
- It is safe to run `review.build` and `review.apply` as often as you like. A rebuild keeps what you filled in. A line in the
  ledger is never posted again, even if an old copy of `queue.csv` is put back.
- **The page does the same thing.** A click writes the same columns into `data\queue.csv`, then runs `review.apply` on that one
  row, so the guards and the audit are identical.

**Partner report.** `data\partner_report.csv` has one row per loan per month (`month, loan_ref, payments, amount,
partner_share, lender_share`) and a `TOTAL` row. If its totals differ from the ledger's, `review.report` stops with an error
instead of writing it.

**Demo.** `review.simulate` stands in for the person on synthetic data only. It fills in `queue.csv` from the answer key
(approver `demo reviewer (simulated)`) and never overwrites a decision already there.

Design: [review queue](docs/superpowers/specs/2026-11-16-review-queue-design.md),
[page](docs/superpowers/specs/2026-11-16-ui-design.md).

### Suggestions for unclear lines

Many queued lines are "no loan fits": a relative paid, the name is misspelt, or the rules could not read it. The Review tab
shows up to 5 likely loans for such a line, ranked by how the payer's name compares with each borrower's and by the
instalment due nearest the payment. This runs on your computer with no key and sends nothing out. Each card shows the facts
(how the names compare, the instalment and its due date, whether the amount matches).

**A suggestion never posts, approves or decides anything.** "Use this loan" only fills the loan box, and the person still
approves. The audit log records that the loan came from a suggestion, in front of the note.

Optionally, **Ask AI to rank these**, per line and only when you click it, sends that line's narration and the loan_ref and
name of up to 5 shortlisted borrowers, nothing else, and shows the AI's order marked "AI suggestion, check it". It needs the
AI reader set up and a second consent tick on the Connect tab.

Measured on the synthetic statement only: of 257 queued lines that have a true loan, the true loan was in the top 5 for every
case type, and first in 72.9% to 96.0%. Treat that as an upper bound, because the synthetic relatives are easy. Details:
[docs/reference.md](docs/reference.md#suggestions-for-unclear-lines-17-nov-2026). Design:
[suggestions](docs/superpowers/specs/2026-11-17-suggestions-design.md).

### Learning from approvals

A payer's name is a weak signal; the account the money came from is a stronger one. When a person approves a queued line for a
loan, the agent can remember "this payer account belongs to this loan". The next payment from that account is then matched to
that loan even if the name is mangled, under the same rules as every other automatic post.

- **What counts as a payer account** (never a name): the UPI ID in a UPI narration, and the payer's IBAN (only if its check
  digits are right) or account number in a camt.053 or MT940 statement. Free text from NEFT or IMPS is not read.
- **When it posts.** A line from a learned account posts to that loan only if an instalment of exactly this amount is due on it
  within 5 days, the payer's name does not fit another borrower, and no look-alike loan owes the same amount then. Otherwise
  the line is queued with the learned loan first.
- **What it never does.** It never learns from an automatic post, a reject, a failed post, the demo reviewer, or a line with
  no bank reference. An approval to a different loan turns the account off (a shared or family account) until a person turns it
  back on. Nothing is overwritten; every change is a new row.
- **On the page.** Each approval has a box "Remember this payer account for this loan". The **Learned payer accounts** panel on
  the Review tab lists every account (masked), its loans and status, with **Turn off** and **Turn on for <loan>**. To forget
  everything, delete `data\payer_links.csv`; the audit keeps what was learned and by whom.
- **Privacy.** `payer_links.csv` holds account identifiers, which are personal data. It stays in `data\` (not in git), is never
  sent to an AI, and every page and log shows an account masked to its kind and last 4 characters.
- **Measured honestly.** On the synthetic data, learning does **not** shrink the queue and it never posted to a wrong loan.
  The gain would come on a real feed where a regular payer's name is unreadable; that was not measured. Details:
  [docs/reference.md](docs/reference.md#learning-from-approvals-18-nov-2026). Design:
  [learning](docs/superpowers/specs/2026-11-18-learning-design.md). The UPI layouts and account fields were checked against
  made-up files only.

## How it works inside

```
  bank statement (CSV, camt.053, MT940)        Fineract: loans and instalments
            |                                              |
   statement.import_bank / import_structured      statement.fetch_schedules
            |                                              |
   data\bank_statement.csv                        data\schedules.json
            \_____________________  ___________________/
                                  \/
                          matcher.run   (fixed rules; names by matcher.names)
                                  |
                          data\decisions.csv      post / queue / ignore, each with a reason
                  ________________|________________
                 |                                 |
            posting.run                       review.build
     (repayment to Fineract, UTR as       data\queue.csv -> a person (page or Excel)
      externalId, split, ledger, audit)             |
                 |                             review.apply (same posting code)
                 |_________________ ________________|
                                   \/
                  data\ledger.csv, data\audit.csv, review.report -> partner report
                         posting.verify checks the ledger against Fineract
```

**The matcher** is fixed rules; no language model decides anything. A repeated txn_id is ignored. A loan account in the
narration names the loan. Otherwise the payer's name and the exact amount must fit one loan with an instalment due within 5
days. Two loans that fit, a look-alike borrower (same surname and first initial) owing the same amount, a different amount,
or no fit all go to the queue with the candidates and a reason. Rules:
[matcher design](docs/superpowers/specs/2026-11-16-matcher-design.md).

**Posting.** For each `post` decision, in order: skip it if its txn_id is already in the ledger or was queued by an earlier
run; read the loan from Fineract and, if the matched instalment is already paid or the loan is closed, queue it instead; post
the repayment with the line's date, the UTR as `externalId` and `receiptNumber`, and the channel as payment type; split it by
`PARTNER_SHARE_PERCENT` (the partner share is rounded half-up to the currency's smallest unit and the lender gets the rest, so
the two always add up); append one row to `data\ledger.csv` and one to `data\audit.csv`. A failed post is logged and the run
carries on. It is safe to re-run: if a run stops after Fineract took a payment but before the ledger row was written, the next
run finds that payment by its `externalId` and records it without posting again (`recovered`). Design:
[posting design](docs/superpowers/specs/2026-11-16-posting-design.md).

**Module map**

| Folder or file | What it holds |
|---|---|
| `seed/` | Synthetic borrowers and loans (`generate.py`) and their loader for the sandbox (`load_fineract.py`); the connector wrapper |
| `statement/` | Fetch instalments (`fetch_schedules`), generate the synthetic statement (`generate`), import a CSV (`import_bank`), camt.053 and MT940 (`import_structured`), `parse` for dates and amounts |
| `matcher/` | The rules (`match.py`, with `FORMATS` for each bank's narrations), name matching (`names.py`), payer accounts (`payer.py`), `run`, `evaluate` |
| `posting/` | `post` (one repayment and its split), `run`, `verify` |
| `review/` | `build` (queue), `apply`, `report`, `suggest`, `learn`, `simulate`, and the two `measure_*` commands |
| `ai/` | The optional AI reader: `client` (the services), `layout`, `narration`, `rank` |
| `ui/` | The local page: `serve.py` (standard library only) and `index.html` |
| `locale_settings.py` | The country presets and the locale settings |
| `scripts/check_connection.py` | The start-up connection check |
| `start.ps1`, `Start.bat` | Windows start-up |
| `tests/` | The unit tests and made-up fixture statements |

**Data files in `data\`** (git-ignored; created as you go)

| File | What it holds |
|---|---|
| `bank_export.csv` | Your own bank export, if you saved one here |
| `bank_statement.csv` | The credits the agent works from: `txn_id, date, amount, narration` |
| `statement.json` | Notes which bank's narrations the statement holds |
| `schedules.json` | Every loan's instalments, read from Fineract |
| `decisions.csv` | The matcher's decision and reason for each line |
| `ledger.csv` | Every posted repayment and its lender/partner split |
| `audit.csv` | Every action with its reason and approver |
| `queue.csv` | The lines waiting for a person |
| `partner_report.csv` | One row per loan per month, checked against the ledger (`repayments_report.csv` in no-partner mode) |
| `layouts.json` | Column maps you confirmed, by bank format |
| `ai_cache.json`, `ai_suggest_cache.json` | Cached AI answers (only if the AI reader is used) |
| `ai_consent.json` | Which consent ticks you gave, and for which service |
| `payer_links.csv` | Payer accounts learned from approvals (personal data) |
| `answer_key.csv`, `bank_export_hdfc.csv` | Synthetic demo only: the answer key and an HDFC-layout test export |

## Measured results and honest limits

**Everything measured here is on synthetic data**: made-up borrowers, loans and bank lines that the same project generated. It
says the rules are sound on that data. It does not say how the project would do on a real bank feed.

**Measured on 16 to 18 Nov 2026**, on 200 clients and 260 loans in the local sandbox (1,654 statement credits):

| Measure | Result | Target |
|---|---|---|
| Wrong automatic postings | 0 of 1,348 posts | under 0.5% |
| Share auto-posted of lines the key says to post | 100.0% | at least 80% |
| Duplicates posted | 0 | 0 |
| Lines sent to a person | 273 (all of them ones the key also queues) | not a target |
| Ledger and partner report against Fineract | equal, 0 problems | equal |

The targets are goals to measure against. The first full run posted 1,341 payments (on an earlier 259-loan build); after a
person (simulated) worked the queue, the ledger held 1,596 and loans in arrears fell from 194 to 144. Per-step detail is in
[docs/reference.md](docs/reference.md#measured-results-in-detail).

**Limits you should know**

- **No real data.** No real bank export, in any format, has been through it. HDFC, ICICI, SBI and Axis support comes from
  secondary sources. The camt.053 and MT940 readers and every non-India preset were written from descriptions and tested on
  made-up files only.
- **No live AI call.** No call to any AI provider has been made; everything was tested with fake responses.
- **Not tested as a restricted user.** A full run as a Fineract user holding only the five permissions listed above was not done.
- **Not clicked through by hand.** All tabs were rendered in headless Chrome and exercised over HTTP and in the tests, but
  nobody has clicked through them in a browser, and `Start.bat` was not double-clicked in that test session.
- **Learning does not shrink the queue** on the synthetic data (see above).
- **One local user, no login.** The approver is a typed name.
- **One currency per run.** No conversion.
- **Not production-ready, and no regulator has approved it.** It is not legal or compliance advice. Co-lending rules and
  splits differ by country and by agreement.

## Troubleshooting

| What you see | What to do |
|---|---|
| The `Start.bat` window stays open with a red `STOP` line | Read the line; it names the fix. If Docker is not running, start Docker Desktop, wait until it says "Engine running", and double-click again. |
| `Connection failed`, or the first check fails | Fineract is not running or `MIFOSX_BASE_URL` is wrong. It must end in `/fineract-provider/api/v1`. Fix it on the Connect tab and press Save (a hand edit of `.env` needs a restart of the page). |
| `Error 400` on every call | Unknown tenant: fix `MIFOSX_TENANT_ID`. |
| `Error 401` | Wrong user or password. |
| `Error 403` | The user lacks a permission ([Connect, step 1](#steps)) or cannot see the loans' office. |
| Everything is slow (2 seconds a call) | Use `127.0.0.1` instead of `localhost` in `MIFOSX_BASE_URL`. |
| `Connector not found in vendor/mcp-mifosx` | Do [Connect, step 2](#steps). |
| `REFUSED, nothing written` on import | A credit's amount or date could not be read, or every date (or amount) could be read two ways. Fix that row, or choose the date order or number format on the Statement tab, then import again. |
| `loan_ref '...' is not a known loan` | The loan is newer than `data\schedules.json`, or the reference is mistyped. Run "Read the loans" again. |
| `queue.csv is open in another program` | Close it in Excel, then press the button again. |
| `this txn_id is already in Fineract, but not on this loan` | That UTR was posted to another loan earlier. Find it in Mifos before deciding; the agent will not post it a second time. |
| The queue is empty | Nothing is waiting. New unclear lines arrive with the next Match or `review.build`. |
| "this page is out of date: reload it" | The page's server was restarted. Reload. |
| The Mifos link asks you to log in | Log in to Mifos once in that browser at the address the link uses (`127.0.0.1` and `localhost` count as different sites), then click again. |
| `cannot listen on 127.0.0.1:8000` | Another program has the port: `python -m ui.serve --port 8010`. |
| The AI test says the key was refused, or nothing answers | Check the key and the model name on the Connect tab. The page says which. Everything else works without the AI reader. |

To start over: stop the sandbox with `docker compose stop`, or wipe it with `docker compose down -v` in
`vendor\mcp-mifosx\agentic-mifos-sandbox` and delete `.env`.

## Project structure, tests and contributing

```
Start.bat, start.ps1     Windows start-up          spec.md      the problem and the worked example
seed/ statement/ matcher/ posting/ review/ ai/ ui/   the code (see the module map above)
locale_settings.py       country presets           tests/       unit tests and fixture statements
docs/                    reference.md, and design notes in docs/superpowers/specs/
.env.example             every setting, with comments
```

**Tests.** No Fineract is needed:

```powershell
python -m unittest discover -s tests
```

The suite has 438 tests and takes about 70 seconds. It uses made-up data and fake AI responses, so it makes no network calls.

**Contributing.** Keep the rules above: the model only reads, nothing posts without the rules or a person, and anything
unclear goes to the queue. Add a test with each change. Adding a bank's narration layout is one line in `FORMATS`
(`matcher/match.py`); a new column layout needs no code (a confirmed column map is enough). Real bank exports are the most
useful thing to try; do not commit one, because `data\` and `.env` are git-ignored on purpose. The design note for each part
is in [docs/superpowers/specs/](docs/superpowers/specs/).

## Reference

- [docs/reference.md](docs/reference.md): the AI reader in detail, the names the matcher handles, locale settings in
  detail, measured results step by step, Fineract API findings, and the India co-lending note.
- Design notes, one per part: [matcher](docs/superpowers/specs/2026-11-16-matcher-design.md),
  [posting](docs/superpowers/specs/2026-11-16-posting-design.md),
  [bank statement](docs/superpowers/specs/2026-11-16-bank-statement-design.md),
  [bank import](docs/superpowers/specs/2026-11-16-bank-import-design.md),
  [review queue](docs/superpowers/specs/2026-11-16-review-queue-design.md),
  [page](docs/superpowers/specs/2026-11-16-ui-design.md),
  [AI reader](docs/superpowers/specs/2026-11-17-ai-reader-design.md),
  [locale](docs/superpowers/specs/2026-11-17-global-locale-design.md),
  [no partner](docs/superpowers/specs/2026-11-17-no-partner-design.md),
  [suggestions](docs/superpowers/specs/2026-11-17-suggestions-design.md),
  [camt.053 and MT940](docs/superpowers/specs/2026-11-18-camt-mt940-design.md),
  [learning](docs/superpowers/specs/2026-11-18-learning-design.md),
  [names](docs/superpowers/specs/2026-11-18-names-design.md).

**Findings that shape the design** (full notes in [docs/reference.md](docs/reference.md#fineract-api-findings)):

- Fineract refuses a second repayment with the same `externalId` (HTTP 400, `value.must.be.unique`). So the bank's UTR is
  sent as `externalId`, and `receiptNumber` too. Only `externalId` is unique in Fineract's database; the other fields are not.
- `GET /loans/{loanId}/transactions/external-id/{externalId}` finds a repayment by its UTR. This is how a crashed run recovers.
- Call the API at `127.0.0.1`, not `localhost`: on Windows `localhost` made each call wait about 2 seconds.
- The connector's own repayment tool cannot send an `externalId` or a date, so this project calls the connector's HTTP adapter
  for repayments. The connector is used as it is.
- Fineract has no built-in proportional co-lending split. The split ledger is this project's own, outside Fineract.

**An India example, co-lending only.** The 80:20 default is an illustration, not a rule. RBI (the Reserve Bank of India)
published Co-Lending Arrangements Directions, 2025 (in force 1 January 2026) that, per law-firm summaries and not the RBI text,
require each lender to keep at least 10% of every loan. This project does not claim RBI compliance. See
[docs/reference.md](docs/reference.md#co-lending-rules-an-india-example).

## Author

Built by Raman Lamba: [ramanlambaportfolio.vercel.app](https://ramanlambaportfolio.vercel.app).

## License

MIT, see [LICENSE](LICENSE). The sandbox it runs against, Apache Fineract and the openMF connector, have their own
(Apache 2.0) licenses; this repo does not include them (`vendor/` is cloned by `start.ps1` and git-ignored).
