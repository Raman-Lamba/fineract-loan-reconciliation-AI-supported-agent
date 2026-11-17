# Bank statement import

Goal (spec.md): an agent matches messy bank lines to loans in Fineract, posts only clear ones, splits each
80:20 in an auditable ledger, and queues anything unclear for a person. Never guess, never post twice.
Until now `start.ps1 -Reconcile` always generated a synthetic statement. This adds the path a lender
needs: their own bank export in, the same `data/bank_statement.csv` out.

## What is verified

Nothing here was checked against a real bank export. The HDFC column layout and every bank narration
layout come from secondary sources. No real person's statement was used; all test data is generated.
What is measured is that a synthetic statement written in the HDFC layout comes back through the import
unchanged and is matched exactly as before.

## Command

`python -m statement.import_bank <export.csv> [--bank hdfc] [--out data/bank_statement.csv]`

Writes `txn_id, date, amount, narration`, credits only, in the export's order. Prints
`rows read: N credits kept: C debits skipped: D`. Exit code 1 and `REFUSED, nothing written` on any
refusal below.

## Rules

1. **Header row.** The first row holding all of the bank's column names (`LAYOUTS`: for HDFC `Date`,
   `Narration`, `Chq./Ref.No.`, `Withdrawal Amt.`, `Deposit Amt.`), compared without case or spaces.
   Columns are then read by name, so their order and any extra columns do not matter. Rows above it are
   the preamble. No such row: refuse.
2. **End of the table.** A blank or `*****` row after the first transaction, or a row with no readable
   date and nothing in either amount column, ends it. Later rows are the footer. A footer row with a
   readable date is refused: it means a blank row cut the table short.
3. **Credit or not.** An empty amount cell is 0. Deposit above 0: a credit, kept. Withdrawal above 0: a
   debit, skipped and counted. Neither: skipped and counted separately. Both: refuse.
4. **Amount.** Digits with Indian (`1,21,940.65`) or western grouping or none, at most 2 decimals; written
   as `121940.65`. Anything else in either amount column: refuse.
5. **Date.** `dd/mm/yy` (or `dd/mm/yyyy`) to ISO. A credit with any other date: refuse.
6. **txn_id.** The `Chq./Ref.No.` cell. If it is empty or all zeros, the one number of 12 or more digits
   in the narration (the UPI or IMPS reference); none or more than one: the credit is kept with a synthetic id (below). Leading zeros are padding
   and are always removed, from either source, so a payment has the same txn_id wherever it was read.
   Two references in one file that differ only in leading zeros are refused, because the matcher would
   take the second for a duplicate and ignore it.
7. **Narration.** Kept as it is, with line breaks and runs of spaces turned into one space.
8. **Duplicates are kept.** A repeated line still reaches the matcher, which ignores it with a reason.

Every refusal names the row, counted from the top of the file as Excel shows it.

## Matcher: one table for narration layouts

`FORMATS` in `matcher/match.py`: per bank, one entry per format: how the line starts, the separator, the
position of the payer's name. `payer_name(narration, bank)`, `decide(lines, schedules, bank)` and
`matcher.run --bank` use the bank's line; the default is `synthetic`. The loan account rule
(`SCPL` + 9 digits anywhere in the narration) is the same for every bank, so NACH lines need no entry.

| Bank | Entries | Source |
|---|---|---|
| synthetic | `UPI/ref/NAME`, `IMPS/P2A/ref/NAME`, `NEFT-utr-NAME` | ours, measured |
| hdfc | `UPI-NAME-vpa-..`, `NEFT CR-IFSC-NAME-..`, `IMPS-ref-NAME-..` | secondary sources, unverified |
| icici | `UPI/ref/../NAME`, `INF/NEFT/UTR/../NAME`, `MMT/IMPS/ref/../NAME` | secondary sources, unverified |
| sbi | `NEFT-CR-x-NAME`, `IMPS-ref-NAME` | secondary sources; the name position is an assumption |

The bank is given, never worked out: ICICI and the synthetic statement both start `UPI/`, SBI and HDFC both
start `IMPS-`, with the name in different places. A wrong position hands the name rule the wrong text;
posting still needs the exact instalment amount, the 5-day window and exactly one borrower who fits.

`posting.post.channel` now takes the first of UPI, NEFT, IMPS, NACH found as a word in the narration,
because bank layouts do not start with the bare channel (`NEFT CR-`, `INF/NEFT/`, `MMT/IMPS/`). Without
this, repayments from an imported statement would be posted with no payment type.

## Testing through the real path

`python -m statement.generate --hdfc` also writes `data/bank_export_hdfc.csv`: the same lines in the HDFC
layout, with a preamble, a bank-charge debit after every 40 credits, zero-padded references, some UPI rows
with an all-zero reference column (so the narration fallback is used), a comma in every NEFT narration,
and a summary footer. It draws nothing from the random stream, so `bank_statement.csv` and
`answer_key.csv` stay byte-identical. Importing it must give the same txn_id, date and amount per line,
which lets `matcher.evaluate` measure accuracy through the import.

## start.ps1 -Reconcile

If `data\bank_export.csv` exists: `fetch_schedules`, `import_bank --bank B`, `matcher.run --bank B`,
`posting.run`, `posting.verify`, `review.build`, `review.report`, with B from a `BANK=` line in `.env`
(default `hdfc`). No `matcher.evaluate`: a real statement has no answer key. Otherwise, as before. It
prints which source was used.

## Known limits (each marked `# shortcut:` in the code)

- Bank layouts are unverified; check each against a real export before relying on it.
- Only HDFC has a column layout, so `BANK=icici` or `sbi` stops at the import.
- The narration fallback reads all-digit references only; a NEFT UTR has letters.
- A cheque number is short and can repeat across payers; Fineract refuses the second at posting.
- The export must be UTF-8 CSV (not XLS).
- A credit with no reference at all (a cash deposit) is not refused. It gets the id
  `NOREF-<date>-<8 hex of sha256 of date, amount, narration and its position among identical rows>`: stable
  on a re-import, distinct for identical rows. The import prints their count and row numbers (the page shows
  the same notice). The matcher always queues a `NOREF-` line (never posts it, even if name and amount fit),
  a person decides it in the review queue, and an approval posts with that id as the Fineract external id, so
  the duplicate guard holds. The audit reason says no bank reference was used.
