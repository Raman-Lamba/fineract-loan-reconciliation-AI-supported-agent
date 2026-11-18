# Reference

Detail that the [README](../README.md) summarises. Everything measured here was measured on synthetic data (made-up
borrowers, loans and bank lines). Nothing here says how the project would do on a real bank feed.

Contents: [AI reader in detail](#ai-reader-in-detail), [Names the matcher handles](#names-the-matcher-handles),
[Locale settings in detail](#locale-settings-in-detail), [Measured results in detail](#measured-results-in-detail),
[Fineract API findings](#fineract-api-findings), [Co-lending rules: an India example](#co-lending-rules-an-india-example).

## AI reader in detail

Optional. Without a key (and, for a model on this computer, without a model chosen) the page and every command work
without it: HDFC exports are found by themselves and the columns of any other layout are chosen by hand from dropdowns.

### What it reads

With an AI service chosen on the Connect tab and the consent box ticked once, it reads two things the fixed rules cannot.

- **A statement layout it has not seen.** From the first 20 rows it proposes the header row, the date column and date
  format, the narration, the reference, and how money is written (debit and credit columns, an amount with a Dr/Cr column,
  an amount ending in Dr or Cr, or a signed amount). The proposal is checked against the whole file: every column must
  exist and every date and amount in the file must read with it. A map that fails is not used and the reason is shown. A
  person confirms it with "Looks right, import it". The confirmed map is stored in `data\layouts.json` under a hash of the
  header row, so the same bank format is never sent again.
- **Narrations the rules cannot read** (a bank with no line in `FORMATS`, or a narration in a style the line does not
  cover). About 40 narrations per request, it returns the payer's name, the channel, the reference and a loan account if
  one is written there. Answers are cached in `data\ai_cache.json` by a hash of the narration, the provider and the model,
  so a re-run costs nothing and switching provider or model never reuses another model's answers.

### Services and models

| Choice (`AI_PROVIDER`) | Key line in `.env` | Address | Default model; others in the dropdown |
|---|---|---|---|
| Claude (`claude`, the default) | `ANTHROPIC_API_KEY` | `api.anthropic.com` (Messages API) | chosen by Quality (below); `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5` for a typed `AI_MODEL` |
| ChatGPT / OpenAI (`openai`) | `OPENAI_API_KEY` | `https://api.openai.com/v1` | `gpt-6.1-sol`; `gpt-6-astra`, `gpt-6.1-terra`, `gpt-6-luna` |
| Gemini (`gemini`) | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-3.8-flash`; `gemini-3.6-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-4-hydrogen` |
| Other (`other`) | `AI_API_KEY` (not needed on this computer) | `AI_BASE_URL`, yours, for example `http://localhost:11434/v1` | `AI_MODEL`, yours, for example `llama3.1` (examples only) |

**Quality (Claude only).** `AI_QUALITY`, chosen on the Connect tab as a dropdown:

| Quality | Layout detection | Reading narrations | Ranking (Ask AI) |
|---|---|---|---|
| `balanced` (the default) | `claude-opus-5-5` | `claude-sonnet-5-5` | `claude-opus-5-5` |
| `best` | `claude-opus-5-5` | `claude-opus-5-5` | `claude-opus-5-5` |
| `cheapest` | `claude-haiku-5-5` | `claude-haiku-5-5` | `claude-haiku-5-5` |

Why the split: the layout is worked out once per bank format and a mistake there touches every row, so it gets the
strongest model. Reading narrations is the bulk of the work. Ranking runs only when a person clicks Ask AI and needs
judgement. The cost is small in every setting. A statement is roughly 1,600 short narrations; that is an estimate, not a
measurement, and no live call has been made. Changing only the quality does not ask for consent again (consent is per
service). Test AI uses the narration model and says which one it was.

`AI_MODEL` overrides Quality for Claude (one model for every task) and is the default for the other services. The Connect
tab says when it is overriding and has a button to clear it. The model box takes a typed name as well as the list. Each
service has its own key line, so a key is never sent to another service, and `AI_BASE_URL` counts for `other` only. The page
has one write-only key box and saves the key under the right name. "Test AI" sends one fixed question ("reply with ok", no
statement data) through the chosen service and says in plain English whether it worked, with a fix if not (key refused,
wrong address or model name, nothing answering, rate limit).

**A model on your own computer.** Choose Other, address `http://localhost:11434/v1` (Ollama's OpenAI-compatible address), a
model you have pulled (`ollama pull llama3.1`), no key. Then no narration leaves the computer, and the consent text says
so. Any other address must be `https://`; a plain `http://` one is refused unless it is on this computer.

### What it never does

It never chooses a loan, posts, approves or changes an outcome. Its fields only fill a blank that the rules left
(`matcher.match.decide(..., extracted)`), and the rules then decide exactly as with a name they read themselves: the exact
instalment amount, a due date within 5 days, one borrower who fits, no look-alike owing the same. It never overrides a
rule's reading. A name or loan account must appear word for word in the narration and a reference must appear exactly, or it
is dropped. Amount, date and txn_id always come from the statement's own columns. If the model's answer is malformed, cut
off, refused, or the service does not answer, the line takes the rules' path and, if they cannot read it, goes to a person.
Every reason that rests on a field the model read says "(read by the AI reader)" in the decision and the audit trail.

A weaker or smaller model (a small local one especially) reads fewer narrations and fewer layouts correctly. That costs you
time, never a wrong posting: whatever it gets wrong or leaves out is dropped by the same checks and the line goes to the
review queue. So the measure of how well a model reads a given bank is the review-queue count. Run the same statement with
each model you are considering and keep the one that leaves the fewest lines for a person at a price you accept.

### Privacy and consent

Before the first use the page shows what is sent, to whom and with which model, and asks for a tick, for example "I
understand narration text, which can include people's names, is sent to Google's Gemini API (model gemini-3.8-flash)". The
tick is stored in `data\ai_consent.json` with the provider (and, for Other, the address). **Changing provider, or the
address of Other, asks again.** Without the tick no request is made.

- Sent for a layout: the first 20 rows with every digit replaced by 9 (column names and narration words stay; no amount,
  balance, date value or account number).
- Sent for narrations: only the narration text of lines the rules could not read.
- Never sent: amounts, balances, account numbers from other columns, anything from `.env`, learned payer accounts.
- The key goes only in the service's own header (`x-api-key` for Claude, `Authorization: Bearer` for the others). It is
  never shown on the page, put in a response or log, or committed (`.env` is git-ignored).

### How it talks to each service

Plain HTTPS with `urllib` (no SDK, no new dependency). Claude: the Messages API with JSON-schema structured output at effort
`low` and Anthropic's server-side refusal fallback. The other three: OpenAI-style chat completions (`/chat/completions`),
asking for a `json_schema` response format, then (if the service answers 400 or 422) JSON mode, then plain "reply with JSON
only" with the schema in the prompt. Our own parser strips a code fence or a sentence around the JSON, and the answer is then
checked like any other. Only a finished answer is accepted (`end_turn` for Claude, `stop` for the others); a refusal or a
cut-off one leaves the line unread. One timeout of 120 seconds, one retry on 429 or 5xx. A layout read is one small request
and a batch of 40 narrations is a few thousand tokens in and out. The price is per token, so check your provider's current
price list (no figure here is measured).

### Not verified

Live calls to any provider (Anthropic, OpenAI, Google, a local Ollama) have **not** been made. Everything was tested with
fake responses only (`tests/test_ai.py`, `tests/test_ai_providers.py`, `tests/test_ai_quality.py`,
`tests/test_ui_connect.py`). What was checked in the providers' own documentation on 2026-11-17 (page reads, not calls):

- Gemini: the OpenAI-compatible base URL and the model ids `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite`,
  `gemini-3.1-flash-lite` (labelled stable) and `gemini-4-hydrogen` (listed, no stability label). Google's page shows
  structured output only through the OpenAI libraries, not the raw `response_format` field, so that field is **unverified**
  for Gemini; the fallbacks to JSON mode and plain JSON are what protect against it.
- OpenAI: the model ids `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna` as listed on its models page, and that
  Chat Completions supports Structured Outputs and JSON mode. The exact nested `json_schema` request fields and the
  `max_completion_tokens` name are from long-standing practice, **unverified** against the current docs.
- Claude: model ids and request shape as in the first build; not re-checked since.
- Other: no preset; the example model names are **unverified**.

The ICICI, SBI and Axis test statements in `tests/fixtures/` are hand-made from secondary research notes, marked so in their
first line, and use invented people. No real statement of those banks has been through it.

Design: [AI reader](superpowers/specs/2026-11-17-ai-reader-design.md).

## Names the matcher handles

Whether a payer's name fits a borrower is decided by `matcher/names.py` (standard library only, no network, no AI). It
forgives what is the same person written differently, and nothing more. Two borrowers who both fit go to a person with both.
The same-surname, same-first-initial, same-amount look-alike rule stays, compared on the normalised names.

| Handled | Example (borrower / payer as the bank wrote it) |
|---|---|
| Case, accents (Latin and Greek letters only) | José García / JOSE GARCIA |
| Word order | Ramesh Kumar / KUMAR RAMESH |
| Titles at the start (Mr, Mrs, Ms, Dr, Prof, Sri, Shri, Smt and a few others) | Ramesh Kumar / SHRI RAMESH KUMAR |
| Punctuation and apostrophes | Sean O'Brien / SEAN OBRIEN; Ramesh Kumar / R. KUMAR |
| Particles (van, der, de, da, das, bin, ibn, al, von...) inside a name | Jan van der Berg / JAN BERG, JAN VANDERBERG |
| Hyphenated and double surnames, several given names | Anne-Marie Dupont-Leclerc / A DUPONT LECLERC; José García López / J GARCIA LOPEZ |
| An extra middle name or patronymic in the payer, when the given name is in full | Ivan Sidorov / IVAN PETROVICH SIDOROV |
| Family name first (`NAME_ORDER=family-first`), with an initial | Yamada Taro / YAMADA T; Kovács István / KOVACS I |
| Single-name people, exactly | Sukarno / SUKARNO |
| Joint payers (A AND B, A & B, A / B, A, B), each person tried | Anita Nair / RAMESH KUMAR AND ANITA NAIR |
| Non-Latin scripts, by whole words (no prefix or initial); Chinese, Japanese, Korean and Thai only as the whole name | Иван Сидоров / ИВАН СИДОРОВ, И СИДОРОВ; 山田 太郎 / 山田太郎 |

**Deliberately not handled** (each goes to a person): transliteration between scripts (Ivan Sidorov is no fit for ИВАН
СИДОРОВ; the local suggestions and the optional AI ranking on the Review tab can still help), nicknames (Bob for Robert),
married or maiden names, a payer who drops a borrower's middle or second family name (Grace Akinyi Otieno / GRACE OTIENO),
German UE for Ü (Müller / MUELLER: both spellings exist as different surnames). A payer with a company suffix (LTD, LLC, INC,
GMBH, SA, AG, BV, SARL, PLC, PVT, LIMITED, OOO, KK and a few more) never fits a person, so a name the bank cut to end in SA is
queued too. TRADERS or ENTERPRISES alone is not a company mark. `auto` is the strictest choice: it accepts either order but
only whole names, so an initial never picks a borrower.

**A wrong setting can only queue more.** On the Indian synthetic statement `western` gives 1,348 posts, 0 wrong;
`family-first` 1,265 posts, 0 wrong; `auto` 706 posts, 0 wrong. `family-first` and `auto` keep the western look-alike check
as well, so a look-alike is never missed because of the order chosen.

**Unverified.** None of this was checked against real bank data from any of these countries. The title, particle and company
lists are short lists from general knowledge, and every test person is made up. In particular unverified: how banks in each
country write names in the payer field; whether your Fineract's client names put the family name first (Fineract's display
name is built given name first, so `family-first` is for a book where staff typed the family name into the first-name box);
that "Sri" is dropped as a title only when two more words follow (it is also an Indonesian given name); and Greek accent
stripping. The AI statement reader still keeps a name only if it has Latin letters.

Design: [names](superpowers/specs/2026-11-18-names-design.md).

## Locale settings in detail

| Setting | Meaning | Default |
|---|---|---|
| `COUNTRY` | A preset that fills the boxes below as a starting point. Change any of them afterwards. | India |
| `CURRENCY` | ISO 4217 code. **Test connection** checks it is enabled in your Fineract and that your loans are in it. Decimals come from Fineract's own setting for the currency where it can be read, else a small built-in table (JPY 0; KWD, BHD, OMR and a few others 3; the rest 2). | INR |
| `CO_LENDING` | `yes` or `no`: do you share your loans with a partner bank? | yes |
| `PARTNER_SHARE_PERCENT` | Co-lending only. The partner bank's share, 0 to 100, decimals allowed (62.5). The lender gets the amount minus that share, so the two always add up; the partner share is rounded half-up to the currency's minor unit. | 80 |
| `DATE_ORDER` | `day-first`, `month-first` or `auto`. With `auto` the whole file decides: a date with a first part above 12 is day-first, a second part above 12 is month-first. If every date could be read both ways, the import is **refused** and you choose on the Statement tab. It never guesses. | day-first (India); `auto` for the other presets |
| `NUMBER_FORMAT` | `1,234.56`, `1.234,56`, `1 234,56` (space, no-break or narrow no-break space), `1'234.56`, or `auto`. With `auto` the whole amount column decides (a value with both marks shows which is the decimal; a repeated mark is thousands; one mark and 1 or 2 digits is a decimal). If every amount is like `1.234` it could be either, so the import is **refused** and you choose. Indian grouping (`1,23,456.78`) is read for India. `(1,234.56)`, `-1.234,56`, `1,234.56-` and a Cr or Dr suffix are read as signs. | 1,234.56 (India); `auto` for the other presets |
| `CHANNELS` | Comma-separated payment-type names. A narration containing one as a whole word is posted with the Fineract payment type of that name if there is one, else with none. **Test connection** lists which exist (informational, never a failure). | UPI,NEFT,IMPS,NACH |
| `NAME_ORDER` | `western` (the first word of a borrower's name in Fineract is the given name), `family-first` (it is the family name: East Asia, Hungary) or `auto` (either; whole names only, no initials or cut-short names). | western (every preset) |

The presets are India, United Kingdom, Eurozone (SEPA), United States, Brazil, Kenya and Other (nothing filled in). Each is
one small table in `locale_settings.py`. **Unverified:** every non-India preset's channel names (typical scheme names, not
what your Fineract calls its payment types), the choice of `auto` for dates and numbers, the currency names used in the
connection test's hints, the minor-unit table (check ISO 4217 for a currency that matters to you), the browser locale used to
show money (an unknown locale or currency falls back to the ISO code and a plain number), and the decimal-places field names
read from Fineract (`decimalPlaces`, or `digitsAfterDecimal`; a miss falls back to the table).

**One currency per run.** Loans, statement, ledger, audit log, queue and report are all in the one currency in `CURRENCY`. The
files keep plain `1234.56` amounts with a dot and no symbol. A loan in another currency fails the connection test; a
statement with a `Currency` column whose value differs from `CURRENCY` is refused with its row number. Nothing is converted.

**Not yet global:** the `SCPL` loan-account prefix is still the synthetic lender's (one line in `matcher/match.py`). The
demo seed (`seed.load_fineract`) is India-only: it creates INR and the four Indian payment types, for the sandbox only.

Designs: [locale](superpowers/specs/2026-11-17-global-locale-design.md),
[no partner](superpowers/specs/2026-11-17-no-partner-design.md).

## Measured results in detail

All on the synthetic data, on 16 to 18 Nov 2026, against the local sandbox unless a line says otherwise.

### Matcher (16 Nov 2026; 259 loans, 1,644 statement lines, 1,341 posts)

| Measure | Result | Target |
|---|---|---|
| Wrong auto-postings | 0 of 1,341 posts (0.00%) | under 0.5% |
| Auto-posted share | 1,341 of 1,341 lines the answer key says to post (100.0%) | at least 80% |
| Duplicates posted | 0 | 0 |

All 271 queued lines are ones the key also queues (86 ambiguous, 68 partial, 53 overpaid, 48 paid by another person, 16
unrelated). The statement's name mangling follows a few fixed patterns that the rules were written for, so these numbers say
the rules are sound on this data, not how they would do on a real bank feed. The later 260-loan build (twins added) gave 1,348
posts, 273 queued and 33 ignored of 1,654 lines, again with 0 wrong.

### Posting and the review queue (16 Nov 2026)

The first full `posting.run` posted all 1,341 `post` decisions (1 of them recovered, from the trial that checked Fineract's
duplicate guard); none was already paid and none failed. A second full run posted nothing. `posting.verify` printed:

```
ledger rows checked against Fineract: 1341, problems: 0
payments posted: 1341
rupees posted:   18,129,477.71
partner share:   14,503,581.84
lender share:    3,625,895.87
loans in arrears: 194 of 259
```

Then `review.build` made 271 queue lines (121 "amount differs: partial or overpaid", 86 "2 loans fit", 64 "no loan
fits"). `review.simulate` (the demo stand-in for a person) decided 255 `post` and 16 `reject`; `review.apply` posted 255
(3,908,968.81 rupees), rejected 16, 0 problems, 0 left. A second `review.apply` did nothing, and with the 271 decided rows put
back (a copy saved from Excel) it reported 271 "applied earlier" and left the ledger and audit files unchanged. Then:

```
ledger rows checked against Fineract: 1596, problems: 0
payments posted: 1596
rupees posted:   22,038,446.52
partner share:   17,630,756.98
lender share:    4,407,689.54
loans in arrears: 144 of 259
```

Loans in arrears fell from 194 to 144. All 144 were explained loan by loan: 76 still owe an instalment due on or before 31
October (a missed instalment, a partial payment, or both; posting a partial payment as it is leaves the rest of that
instalment overdue, which is correct) and 68 owe only an instalment due 1 to 15 November, after the statement ends. Five loans
show as `Overpaid` in Fineract because overpaid lines were posted in full. What to do with the extra (refund it or hold it) is
left to a person.

### Bank import (16 Nov 2026)

`statement.generate --hdfc` writes the synthetic statement as an HDFC-layout export, `data\bank_export_hdfc.csv`: account
details on top, 41 bank-charge debits among the credits, zero-padded references (80 UPI rows have none in the column), 372
narrations with a comma, a summary at the bottom. `data\bank_statement.csv` and `data\answer_key.csv` come out byte for byte
as before. Importing the export read 1,695 rows, kept 1,654 credits, skipped 41 debits, and gave the same txn_id, date and
amount on all 1,654 lines. `matcher.run --bank hdfc` on it made the same decision on every line as on the synthetic statement
(1,348 posts, 0 wrong), and `posting.run` then posted nothing.

### Page (16 and 17 Nov 2026)

Against the live sandbox, over HTTP: the page, the summary, all seven connection checks passing, the arrears count (145 of
260, 0 ledger problems, about 22 seconds), the partner report download, and the refusals (wrong Host, no token, wrong token, a
txn_id not in the queue). The Run tab was checked the same way: Read the loans (260 loans, 4,587 instalments, 26 seconds) and
Match (1,348 would post, 273 to a person, 33 ignored, the same decision on every line as `matcher.run`). Nothing was
approved, rejected or posted on the sandbox in these checks (the ledger and audit files had the same sha256 before and
after). Approve, reject, a refused row, and posting from the Run tab with its confirmation were run in `--demo` mode and in
the unit tests. Every tab was rendered in headless Chrome. Nobody has clicked through the page by hand, and `Start.bat` was
not double-clicked in that session.

### Suggestions for unclear lines (17 Nov 2026)

`python -m review.measure_suggestions` (the only command that reads the answer key for this). Of 273 queued lines, 257 have a
true loan.

| Case | Lines | True loan first | True loan in the 5 | Lines with any suggestion |
|---|---|---|---|---|
| paid by someone else | 50 | 48 (96.0%) | 50 (100%) | 50 |
| ambiguous (2 loans fit) | 85 | 62 (72.9%) | 85 (100%) | 85 |
| partial | 69 | 61 (88.4%) | 69 (100%) | 69 |
| overpaid | 53 | 43 (81.1%) | 53 (100%) | 53 |
| unrelated (no loan) | 16 | - | - | 0 |

The two "paid by someone else" misses are exact ties between twin loans, shown side by side. The synthetic relatives share the
borrower's surname and pay the exact instalment on time, which is what the scoring rewards, so treat these as an upper bound
and measure on your own queued lines. Design: [suggestions](superpowers/specs/2026-11-17-suggestions-design.md).

### Learning from approvals (18 Nov 2026)

`python -m review.measure_learning` replays a statement in date order in memory; each queued line the answer key gives a loan
is approved for it at once.

| Statement | Lines with a payer account | Queue before -> after | Posted by a learned account (wrong) | Of those, the name rule would have queued | Queued because of a learned account (name rule would have posted) | Accounts turned off as shared |
|---|---|---|---|---|---|---|
| `data\bank_statement.csv` | 895 of 1,654 (54%), 188 accounts | 273 -> 324 | 2 (0) | 0 | 69 (51) | 67 of 90 |
| repeat-payer statement | 920 of 1,654 (56%), 277 accounts | 273 -> 273 | 81 (0) | 2 | 41 (2) | 2 of 101 |

**On this synthetic data learning does not shrink the queue**, and it never posted to a wrong loan. In the default statement a
UPI ID is just the payer's first name and a bank (`sanjay@oksbi`), shared by every Sanjay, so learned accounts collide, get
turned off, or queue a namesake's clear payment: 51 more lines for a person. In the repeat-payer statement
(`python -m statement.generate --repeat-payers` writes it to `data\repeat_payers\`; the default files stay byte for byte the
same) every borrower pays from one UPI ID of their own. There 81 lines post by account, but the name rule would have posted 79
of them anyway. In a run without the look-alike check, one payment went to the wrong one of two Deepa Reddys sharing
`deepa@paytm`: a shared account is exactly what one approval cannot rule out. The gain would come on a real feed where a
regular payer's name is unreadable or is someone else's; that was not measured.

Design: [learning](superpowers/specs/2026-11-18-learning-design.md).

## Fineract API findings

Read from the Fineract source at the commit the sandbox pins (`apache/fineract@7ac780192`). The repayment fields and the
duplicate guard were then exercised against the running sandbox on 16 Nov 2026.

- A second repayment with the same `externalId` is refused with HTTP 400, `Failed data validation due to:
  value.must.be.unique.`
- `GET /loans/{loanId}/transactions/external-id/{externalId}` returns the transaction, or 404 if that loan has none with that
  id.
- Call the API at `127.0.0.1`, not `localhost`: on Windows each call to `localhost` waited about 2 seconds (IPv6 is tried
  first), which made a full posting run 20 times slower. `start.ps1` writes `127.0.0.1` into a new `.env`; in an older `.env`,
  change the host in `MIFOSX_BASE_URL` by hand.

**Where the bank txn_id goes: `externalId` on the repayment.** `POST /loans/{id}/transactions?command=repayment` accepts
`transactionDate`, `transactionAmount`, `externalId`, `note`, `paymentTypeId`, `accountNumber`, `checkNumber`, `routingCode`,
`receiptNumber`, `bankNumber` (`LoanTransactionValidatorImpl.getRepaymentParameters`).

| Field | Limit | Unique in the database? |
|---|---|---|
| `externalId` | 100 chars | Yes: constraint `external_id_UNIQUE` on `m_loan_transaction` |
| `receiptNumber`, `bankNumber`, `checkNumber`, `routingCode`, `accountNumber` | 50 chars each | No |
| `note` | free text | No |

Only `externalId` makes Fineract itself refuse a second posting of the same payment. So: `externalId` = UTR (the guard),
`receiptNumber` = UTR again (it is what the web app shows), `paymentTypeId` = channel.

**The connector's repayment tool cannot carry it.** `make_loan_repayment(loan_id, amount, payment_type_id)` always posts with
today's date and sends no `externalId` or `receiptNumber`. We call the connector's own HTTP adapter
(`tools.mcp_adapter.fineract_client`) for repayments and seeding. The connector is used as it is, not modified.

**Co-lending: no built-in support for a proportional split.** Fineract has an optional investor module
(`fineract-investor`, "external asset owner") that records the sale of a *whole* loan to another owner and its buy-back. It
has no per-loan participation ratio and does not divide repayments. The split ledger is ours, outside Fineract.

**How the Mifos links were checked.** The URL pattern, the Transactions columns and the payment details panel were read from
the code of the Mifos web app the sandbox runs (`openmf/web-app:1.14`: hash routing, routes `clients/:clientId` >
`loans-accounts` > `:loanId` > `general` / `transactions` / `transactions/:id`). They were not clicked through in a logged-in
browser. The menu names used in the README's permission step come from the same code.

## Co-lending rules: an India example

This applies to co-lending only. The 80:20 default is an illustration, not a rule. Co-lending rules and the split percentages
differ by country and by arrangement. RBI (the Reserve Bank of India) published Co-Lending Arrangements Directions, 2025 (in
force 1 January 2026), which require each lender to keep at least 10% of every loan and the borrower to be charged a blended
interest rate. The 2020 model they replace required the NBFC (non-banking financial company) to keep at least 20%. These
points come from law-firm summaries, not the RBI text. This project does not claim RBI compliance and is not legal or
compliance advice.

The accuracy targets used in the plan (under 0.5% wrong auto-postings, at least 80% auto-posted, no duplicates) are goals to
measure against, not results.
