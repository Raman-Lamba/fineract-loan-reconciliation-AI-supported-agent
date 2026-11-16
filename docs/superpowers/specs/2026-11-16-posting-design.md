# Milestone 4: post repayments to Fineract and keep the 80:20 split ledger

Goal (spec.md): a matched payment is recorded in Fineract the same day, so the borrower stops looking
overdue, and its money is split between the partner bank and the lender in a ledger an auditor can check.

## Command

`python -m posting.run [--limit N]`. It reads `data/decisions.csv` (outcome `post` only) and writes to
Fineract and `data/`. It is safe to re-run: a payment is never posted or split twice.

## Per decision, in order

1. **Already in the ledger** (txn_id in `data/ledger.csv`) -> skip.
2. **Live check:** read the loan from Fineract. If the matched instalment is already fully paid, or the
   loan is closed, do not post; write an audit row `queued: instalment already paid`. (This removes the
   `shortcut:` in `matcher/match.py`. Such lines join the review queue in milestone 5.)
3. **Post:** `POST loans/{id}/transactions?command=repayment` with the line's date and amount,
   `externalId` = txn_id (Fineract's own duplicate guard), `receiptNumber` = txn_id, `paymentTypeId`
   from the narration's channel (UPI, NEFT, IMPS, NACH), and `note` = the matcher's reason.
4. **Fineract says the externalId already exists** (an earlier run crashed after posting but before
   the ledger write) -> look the transaction up by externalId and carry on to step 5 with it. Do not post again.
5. **Split and record:** partner share = amount x 0.80 rounded half-up to the paisa; lender share =
   amount - partner share, so the two always add up exactly (use `Decimal`, never float). Append to
   `data/ledger.csv`: `txn_id, date, loan_ref, fineract_txn_id, amount, partner_share, lender_share`.
6. **Audit:** append to `data/audit.csv`: `at, txn_id, action, loan_ref, reason, approver`. The approver
   is `auto (matcher rules)` for automatic postings. Every attempt is logged: posted, recovered, queued
   or failed. A failed post is logged and the run carries on.

The 80:20 ratio is one constant (`PARTNER_SHARE`). Per-loan ratios are left out until something needs them.

## Verify

`python -m posting.verify` checks that every ledger row has a matching Fineract transaction (same
externalId and amount), that partner and lender shares add up to the amount, and that no txn_id appears twice.
It prints totals: payments posted, rupees posted, the partner and lender totals, and how many loans are in arrears.

## Deploy

Stdlib only. `start.ps1 -Reconcile` runs posting after the matcher. The ledger and audit files are CSV in `data/`.
