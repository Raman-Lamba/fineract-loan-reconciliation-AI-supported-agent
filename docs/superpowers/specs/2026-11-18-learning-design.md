# Learning from approvals

Goal (spec.md): match each bank line to exactly one loan, or hand it to a person with the candidates and the reason.
Never guess, never post twice, every action logged with a reason and an approver. A payer's name is a weak signal; the
account the money came from is a stronger one. When a person approves a line for a loan, remember "this payer account
belongs to this loan", so the next payment from it matches even when the name is mangled. Same safety rules.

## Decision: an append-only CSV of links, one pure module, one new matcher rule

- `matcher/payer.py` (pure, standard library): `key(narration, bank)`, `iban_ok`, `mask`, `current(rows)`, `learn(...)`.
- `review/learn.py`: the file `data/payer_links.csv`, `record()` for review.apply, `view()`/`toggle()` for the page.
- `matcher/match.py`: rule 3, between "loan account in the narration" and "name". `matcher/run.py` reads the file.
- `review/measure_learning.py`: the replay. `statement/generate.py`: an optional repeat-payer statement.

No new dependency, no network, no AI call. With no links file the matcher's decisions are byte-identical
(`data/decisions.csv` sha256 9f2c9f7f... before and after, checked in a temp copy).

## Payer keys

Never a name. `iban:<IBAN, no spaces, upper case>`, `vpa:<UPI ID, lower case>`, `acct:<digits>`.

| Source | Read | Not read |
|---|---|---|
| UPI narration (`UPI/` or `UPI-`; banks synthetic, hdfc, icici) | the one word that is a whole VPA (`name@handle`) | two different VPAs anywhere in the line (a memo naming another) |
| camt.053 / MT940 (`payer \| remittance \| account \| channel`) | the account field: an IBAN whose mod-97 check holds, else 6-34 digits (UK sort code + account) | an IBAN-looking value that fails the check (a typo), letters that are not an IBAN |
| NEFT, IMPS, NACH, any other free text | nothing | a long number there can be the payer's, ours, or a reference |

UNVERIFIED (made-up data only): the HDFC and ICICI UPI layouts; camt.053 `DbtrAcct/Id/IBAN` and `Othr/Id`; MT940 `?31`;
UK sort code + account in `Othr/Id`. The test fixtures' IBANs (DE00..., NL00...) fail the check on purpose, so they give no
key. The synthetic statement's VPAs are `<first name>@<bank>`, shared by namesakes: a weak key.

## The links file

`data/payer_links.csv`: `payer_key, loan_ref, learned_from_txn, approver, learned_at, status` (`active`/`disabled`). Every
change is a new row; the last row of a key is its state. So the file is its own history, opens in Excel, and is
appended to, never rewritten. It is personal data (account identifiers): under `data/` (git-ignored), never sent to an AI,
shown and logged masked (`vpa:****axis`, `iban:****3000`: kind + last 4).

## Learning (`payer.learn`, called by `review.apply.run` after the post)

| Situation | Result |
|---|---|
| A person's approval posted now (`posted`), line has a key, key unknown, `remember` not "no" | append `active` link; audit `payer_learned` "learned payer account vpa:****axis for L0012" |
| Same key, same loan again | nothing |
| Active key, approval to a different loan | append `disabled` row (that loan); audit `payer_conflict`; regardless of `remember` (turning off is the safe way) |
| Key disabled (by a conflict or a person), any approval | nothing: never back on without a person |
| Reject, refused row (unknown typed loan_ref, no approver), failed or recovered post, automatic post, the demo reviewer (`review.simulate`), a NOREF line, a line with no key | nothing |

The page sends `remember` from the per-approval box "Remember this payer account for this loan", ticked by default only when
the account is new or already learned for a loan; a page request without it learns nothing. In `queue.csv` the new last
column `remember` is empty (learn) or `no` (do not).

## Rule 3 in the matcher

Only an `active` link to a loan in the schedules counts (a disabled one, or one to an unknown loan, falls through to the
name rule as if it did not exist). Then, in order:

1. no instalment of exactly this amount due within 5 days on the linked loan -> queue, linked loan first (then the loans
   the name fits): "payer account vpa:****axis was approved for L0012 before, but the amount/date does not fit";
2. the payer's name fits another borrower and not this one -> queue with both: "... but 'X' fits another borrower and not
   this one";
3. another loan the name fits, or the same family name and first initial (the matcher's look-alike rule), owes the same
   amount in the window -> queue: "... but N other loan(s) fit the name and the amount too". A shared family account is
   exactly this case; one approval does not settle it;
4. else post: "payer account vpa:****axis was approved for L0012 by Asha on 2026-11-18; instalment due 2026-12-05".

posting.post still checks the instalment live and the duplicate guard is unchanged. Gate 3 is a judgement call beyond the
brief's wording ("no competing candidate"): without it the replay posted one line to the wrong loan (two borrowers named
Deepa Reddy, the same instalment, the same synthetic VPA `deepa@paytm`). The price: on the repeat-payer statement it also sends
look-alike twins with their own accounts to a person, so learning shrank the queue by 0 instead of 27 (README).

## The page

A queued line with a known key says so ("was approved for L0012 before (learned)", or "approved for L0012 and L0034: it is
turned off"). The confirmation says what approving will do to the account. The Review tab's "Learned payer accounts" panel
lists keys masked, their loans, status, who and when, with Turn off / Turn on for <loan> (approver required, audited as
`payer_disabled` / `payer_enabled`), and "N payer accounts learned, M lines auto-matched this way" (M: automatic posts in
audit.csv whose reason starts "payer account"). The page names a link by a hash of the key; the key never leaves the files.

## Measurement

`python -m review.measure_learning`: replays a statement in date order in memory (each day matched with what was learned so
far; each queued line the answer key gives a loan is approved for it and taught); reports lines with a key, queue before
and after, links learned and turned off, posts by a learned account and how many wrong, and the lines a link queued that the
name rule alone would have posted. Only this command reads the answer key. Runs on `data/` and on the repeat-payer
statement (`generate(..., repeat_payers=True)`: same lines, amounts, dates and answer key; each borrower's UPI lines carry
one stable VPA of theirs, a relative pays from their own, and 4 surnames' first two loans share one family VPA). Results are
in the README, with the caveat that both statements are synthetic.

## Limits

- The replay approves each queued line the same day; real approvals come later, so fewer lines would benefit.
- A key learned for a loan that is later closed stays active; it then never fits an instalment and only queues with a reason.
- The mask keeps the last 4 characters, so two UPI IDs at the same bank handle (`...@okaxis`) look alike; the panel
  tells them apart by their loans.
- One reviewer, no login (as the page already is): the approver on a link is the typed name.
