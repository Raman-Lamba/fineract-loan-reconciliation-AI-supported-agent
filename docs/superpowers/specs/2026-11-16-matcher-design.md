# Milestone 3: matcher and accuracy report

Goal (spec.md): match each bank line to exactly one loan, or hand it to a person with the candidates and
the reason. Never guess. This milestone decides; it does not post (milestone 4 posts).

## Decision: deterministic rules decide, not a language model

Every auto-posting must be explainable to an auditor and repeatable. A rule-based matcher gives the same
answer every run, costs nothing per line and can be measured against the answer key exactly. A language
model can be added later for what rules handle badly (reading odd narrations, drafting the note a person
sees in the queue), but it never turns a queue into a post on its own.

## Inputs and outputs

- In: `data/bank_statement.csv` and `data/schedules.json`. Never `answer_key.csv`.
- Out: `data/decisions.csv`: `txn_id, date, amount, outcome, loan_ref, reason, candidates`
  (`outcome` is post, queue or ignore; `candidates` lists loan refs separated by `;`).

## Rules, in order

1. **Duplicate:** the txn_id was already seen earlier in this statement -> ignore.
2. **Loan reference:** the narration has `SCPL<9-digit account>` -> that loan, if it has an instalment
   of exactly this amount due within 5 days; otherwise queue with that loan as the candidate.
3. **Name:** parse the payer name from the narration (UPI, NEFT, IMPS formats; since the bank import,
   the position of the name comes from the bank's line in `FORMATS`, see
   [2026-11-16-bank-import-design.md](2026-11-16-bank-import-design.md)). Candidates are loans
   with an instalment of exactly this amount due within 5 days of the line date, whose borrower's name
   fits the payer name: surname equal or a prefix of it (truncated), first name equal, a prefix of 3+
   letters, or its initial; either order; a single run-together token also fits. (Since stage C the fit is
   `matcher/names.py`'s, which keeps these rules and adds other conventions and scripts:
   [2026-11-18-names-design.md](2026-11-18-names-design.md).)
   - exactly one candidate, but another loan whose borrower has the same surname and first initial has
     the same amount due in the window -> queue with both (added while building: the answer key calls
     this ambiguous even when the line carries a full first name)
   - exactly one candidate -> post
   - more than one -> queue ("N loans fit")
   - none, but the name fits a loan whose instalment differs -> queue ("amount differs: partial or overpaid")
   - none -> queue ("no loan fits")

The 5-day window matches the generator's ambiguity rule, so the matcher queues every line the answer
key calls ambiguous.

## Accuracy report

`python -m matcher.evaluate` compares `decisions.csv` with `answer_key.csv` and prints, against the
targets in the README:

- wrong auto-postings: posts where the key says queue or ignore, or names another loan (target < 0.5% of posts)
- auto-posted share: posts among lines the key says to post (target >= 80%)
- duplicates posted (target 0)
- a confusion table by case

## Code

`matcher/match.py` (pure), `matcher/run.py` (reads and writes files), `matcher/evaluate.py`, tests in
`tests/test_matcher.py` with hand-built schedules: one test per rule, plus a same-name pair.
