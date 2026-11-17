# No partner mode (CO_LENDING=no)

Date: 17 Nov 2026. Status: built. Follows [2026-11-17-global-locale-design.md](2026-11-17-global-locale-design.md).

## Goal

The agent matches bank payments to loans in Fineract, posts the clear ones, splits each repayment between a partner bank and the
lender, and queues unclear lines for a person. A lender that does not co-lend has no partner, so the split, the partner column and
the partner report must disappear while matching, posting, review and audit stay as they are. With default settings every
existing output is byte-identical.

## The setting

`CO_LENDING` in `.env`: `yes` (default) or `no`, typed on the Connect tab as "Do you share your loans with a partner bank? Yes / No".
`locale_settings.load` reads it (anything else is a `ValueError` in plain words). With `no`, `Settings.partner_share` is `0`
whatever `PARTNER_SHARE_PERCENT` says and `Settings.co_lending` is false. Because the split is `partner = round(amount * share / 100)`,
`lender = amount - partner`, a zero share already gives `partner = 0.00` and `lender = amount` in Decimal for any minor unit
(`0`, `0.00`, `0.000`), so `posting.post.split`, the ledger writer, `posting.verify` and the duplicate guard are not touched at all.

## Ledger

Same file, same columns. In no-partner mode `partner_share` is `0.00` and `lender_share` equals the amount, so the sum check
(`partner + lender == amount`) still holds. Existing rows are never rewritten or migrated.

## Changing the mode

The setting applies to new postings only; saving it posts nothing. If `data/ledger.csv` has rows, `/api/settings` replies with
`needs_confirm` and the sentence "N payments are already recorded as split 80:20 (or 100% lender (no partner bank)); this change
affects only new payments", and saves only when the request carries `confirm_mode_change: true` (the page's tick box). The page shows
the same sentence as soon as the other answer is picked. Every change of mode appends one row to `audit.csv`:
`action = mode_changed`, empty `txn_id` and `loan_ref`, `reason = co-lending changed from yes to no`, `approver` = the Approver
box or "(no name given)". An empty ledger needs no confirmation but is still audited. Saving the same mode logs nothing.

## Wording

One helper, `locale_settings.words(settings)`, returns every phrase that differs: the report's file name and title, the lender
tile's label ("Lender's share" or "Collected"), the post log's "split each 80:20 in the ledger" or "recorded each in the ledger as the
lender's", the "(partner X, lender Y)" tail, and the Guide's diagram labels. The server's messages use it; the page gets it in its
config and in the settings view, and marks text with `data-w="key"` and co-lending-only cards with `data-co`.

## Report

`review.report.main(settings)` picks the file by mode: `data/partner_report.csv` (six columns) with a partner, `data/repayments_report.csv`
(`month, loan_ref, payments, amount`) without. The totals check runs on all four sums in both modes before anything is written.
The old file is not deleted when the mode changes. The page's download route is `/partner_report.csv` or `/repayments_report.csv`, only the
one that exists for the mode in force (the other is a 404). Console wording drops "partner".

## Not changed, on purpose

Matching, the queue, posting, `externalId` duplicate guard, the audit columns, the ledger columns, the review actions. The README keeps
80:20 as the co-lending example; the RBI and split notes are labelled as co-lending examples.

## Limits

- A ledger that mixes both modes is shown by the current mode: with no partner the report lists plain repayments, so earlier rows'
  partner shares are not visible in it (they remain in the ledger). Mode switching mid-book is allowed and warned about, not reconciled.
- The "Collected" tile is the ledger's lender-share total; after a switch it counts only the lender's part of earlier split rows.
- `spec.md` (the original problem statement) still describes the co-lending example.
