# Milestone 2: synthetic bank statement with an answer key

Goal (from spec.md): an agent matches bank payments to loans in Fineract, posts clear ones, splits them
80:20, and queues anything unclear for a person. This milestone builds the input it will be tested on.

## Outputs (in `data/`, git-ignored)

- `schedules.json`: every loan's instalments as Fineract computed them (fetched once).
- `bank_statement.csv`: `txn_id, date, amount, narration`. The only file the agent may read.
- `answer_key.csv`: `txn_id, date, case, expected_outcome, expected_loan_ref, true_loan_ref`. Only the accuracy report
  and the demo reviewer (milestone 5) read it.

## Steps

1. `python -m statement.fetch_schedules`: reads every loan with its repayment schedule from Fineract.
2. `python -m statement.generate`: pure, seeded, no network. Same schedules + seed = same files.

Lines cover every instalment due on or before 31 Oct 2026.

## Cases

| Case | Expected outcome |
|---|---|
| `clean`: full name, exact amount, due date | post |
| `messy_name`: truncated, initial, swapped order | post |
| `loan_ref`: NACH line with the loan account number, no name | post |
| `early_late`: 1-4 days off the due date | post |
| `partial`: 50-90% of the instalment | queue (a person decides) |
| `overpaid`: more than the instalment | queue (a person decides) |
| `ambiguous`: another loan has the same surname, first initial and amount within 5 days | queue |
| `paid_by_other`: a relative's name | queue |
| `duplicate`: a second line with the same txn_id | ignore |
| `unrelated`: a credit from someone who is not a borrower | queue |
| `missed`: borrower did not pay; answer key row with no statement line | none |

Partial and overpaid lines go to a person, by the user's decision on 16 Nov 2026.
`ambiguous` is decided after a line is drawn, so it can overrule `clean`, `messy_name` or `early_late`;
a `loan_ref` line names the loan exactly and is never ambiguous.
