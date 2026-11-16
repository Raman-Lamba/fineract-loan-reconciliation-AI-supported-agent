# Milestone 5: review queue, partner report, end-to-end demo

Goal (spec.md): staff handle only the unclear lines, every action has a recorded reason and approver,
and the partner bank gets its share with a report an auditor can check.

## Decision: the queue is a CSV file a person edits in Excel

Staff already reconcile in Excel (spec.md). A CSV needs no server, login or install, so it keeps the
project simple to deploy. A web page can come later if someone needs one.

## 1. Build the queue: `python -m review.build`

Writes `data/queue.csv` with these columns: `txn_id, date, amount, narration, reason, candidates,
decision, loan_ref, approver, note`.

- **What goes in:** every matcher decision with outcome `queue`, plus every posting-audit row with
  action `queued` (for example, instalment already paid).
- **Candidates** are listed with the borrower's name and the instalment due, so a person can choose
  without opening Fineract.
- **Safe to rebuild:** rows a person has already filled in (`decision`, `loan_ref`, `approver`, `note`)
  are kept, matched by txn_id. Lines already in the ledger, or already rejected in the audit, are left out.

## 2. Apply a person's decisions: `python -m review.apply`

For each row with a `decision`:

- **`post`:** `loan_ref` and `approver` are required, and `loan_ref` must be a known loan. The line goes
  through the existing posting code (`posting/`), with the same duplicate guard, ledger split and
  audit, but the approver is the person's name and the reason is `manual: <note>`. A partial or
  overpaid amount is posted as it is, since a person approved it.
- **`reject`:** `approver` is required. The audit gets a `rejected` row, and the line leaves the queue.
- **Anything invalid:** the row stays in the queue with the problem written in `note`. Nothing is posted.

The posting fix carried over from milestone 4: `posting.run` logs an already-paid line once, not on
every run. If the audit already has a `queued` row for that txn_id, it skips the line.

## 3. Partner report: `python -m review.report`

Writes `data/partner_report.csv`, one row per loan per month, with these columns: `month, loan_ref,
payments, amount, partner_share, lender_share`. Below it come the totals. The report checks that its
totals equal the ledger's and stops with an error if they don't.

## 4. Demo reviewer: `python -m review.simulate` (synthetic data only)

This stands in for a person so the whole loop can be shown. It fills in `queue.csv` from the answer
key's new `true_loan_ref` column: the loan whose borrower really paid, which `statement.generate` now
writes for every line.

- **Ambiguous, partial, overpaid, paid by someone else:** `post` to the true loan.
- **Unrelated:** `reject`.
- **Approver:** `demo reviewer (simulated)`.

The matcher never reads the answer key. Only `matcher.evaluate` and this demo do.

## Deploy

Stdlib only. `start.ps1 -Reconcile` runs `review.build` and `review.report` after posting. Running
`review.apply`, or `review.simulate` followed by `review.apply`, is a separate deliberate step,
documented in the README, because it posts money a person signed off.
