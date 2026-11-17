# Suggestions for unclear lines (AI reader, stage two)

Goal (spec.md): the agent posts only clear lines and queues the rest for a person. Many queued lines are
"no loan fits" (a relative paid, a misspelt name, a name the rules could not read), and the person then had
to search Fineract by hand. Stage two shows that person a ranked shortlist of likely loans, each with the
facts to check it. A suggestion never posts, never approves, never changes an outcome or the queue's
decision column: the person still chooses and approves, exactly as before.

## Decision: two layers, local first

1. **Local suggester** (`review/suggest.py`, stdlib only, offline, no key). For each queued line it scores
   every loan and keeps up to 5:
   - **Name**: the payer's name (the bank's `FORMATS` field, or the narration's words minus boilerplate for a
     bank with no line) against the borrower's. Surname: whole or a prefix of 2+ letters (the bank's
     10-character UPI field cuts it) counts 1.0; a word of 4+ letters with difflib ratio >= 0.75 counts as
     misspelt. First name: same or a 3+ letter prefix 1.0, the initial 0.7, misspelt as above, otherwise
     "different first name (a relative?)". A name run together (`RAMESHKUMA`) is compared whole.
   - **Instalment**: the one due within 5 days of exactly this amount, else the one closest in time within 31
     days. Facts computed here: due date, instalment amount, whether the amount equals it, the difference,
     days early or late.
   - Kept only if the surname fits (name score >= 0.45) or the amount equals an instalment due within 5 days.
     Score = 0.55 name + 0.35 amount closeness + 0.10 date closeness. Confidence: high (surname fits and
     the amount equals an instalment due these days), medium (surname fits and an instalment is near, or the
     amount equals one), low otherwise.
   - The matcher's own candidates are left out (they are already on the page).
2. **Optional AI ranking** (`ai/rank.py`) for people who turned on the AI reader: on a click for one line,
   the model re-orders that line's shortlist and writes one sentence per loan.

## What is sent, and the second consent

Only when a person clicks "Ask AI to rank these" on a row (never in bulk): the narration as it stands (as
`ai.narration` sends it) and, for at most 5 shortlisted loans, the loan_ref and the borrower's name. Never an
amount, due date, account number, phone, address or balance (`ai.rank.listing`, `ai.rank.payload`).

A separate tick, recorded under `"suggest"` in `data/ai_consent.json` (the reader's stays at the top level):
"I understand that, for lines I ask about, the narration and up to 5 possible borrowers' names are sent to
<provider> (model <model>)", or for `other` on this computer "... the model X at <address>, which runs on this
computer: nothing leaves it". Changing provider, or the address of `other`, asks again, as for the reader.
Ask AI needs both ticks (`ai.client.sender(kind="suggest")`).

## Safety rules and where they are enforced

| Rule | Function |
|---|---|
| A suggestion never writes the queue's decision, posts or approves | `ui.serve.ask_ai` writes only the AI cache; the page's "Use this loan" only fills the loan box |
| Every loan_ref in the answer must be on the shortlist, once; else dropped | `ai.rank.valid` |
| Malformed answer, refusal or failed call: the local list alone, with a note | `ai.client.answer` / `chat_answer` raise `AIError`; `ai.rank.rank` returns `(None, why)`; `ui.serve.ask_ai` |
| Reasons are one line of plain text, at most 200 characters, set as text in the page | `ai.rank._reason`; `ui/index.html` uses `el()` text nodes only (a test checks the page has no `innerHTML`) |
| Facts beside a suggestion are ours, never the model's | `review.suggest.suggest`; `ai.rank.merge` keeps them and only adds `ai_reason` |
| Only narration + up to 5 names are sent | `ai.rank.listing`, `ai.rank.payload` |
| No call without a key, the reader's consent and the suggestions' consent | `ai.client.sender(kind="suggest")` |
| The shortlist is rebuilt on the server, never taken from the page | `ui.serve._shortlist` |
| The audit records where the idea came from; the approver is the person | `ui.serve.decide` + `ui.serve._origin`: `[suggested by local similarity]` or `[suggested by AI (<provider>/<model>)]` in front of the note, which `review.apply` writes as `manual: ...`; "AI" only if the cached ranking for this line and model holds that loan, and no tag if the loan is not on the shortlist |
| Cache per provider + model + narration + shortlist, checked again on read | `ai.rank.key`, `ai.rank.cached` (`data/ai_suggest_cache.json`) |

Provider-independent: the same sender, adapters, refusal handling and key handling as stage one
(`ai.client`); nothing provider-specific was added. `ai.client.scope` is the cache scope both stages use.

## The page

Review tab, per row: with no matcher candidates, "Suggestions" shows up to 5 cards (loan_ref, borrower,
likelihood, the name comparison, the instalment and whether the amount matches or by how much it differs, a
"Use this loan" button and the Mifos link), or "No likely loan found". Rows with candidates keep their cards
and get a collapsed "More possibilities (n)". "Ask AI to rank these" shows only when the AI reader is set up
and both ticks are given (`ai.suggest_ready` from `/api/settings`); otherwise a one-line hint. The AI's
reasons are marked "AI suggestion, check it". The second consent box is on the Connect tab.

## Measure: `python -m review.measure_suggestions [--data DIR]`

The only reader of `answer_key.csv` here. Over every queue-eligible line (matcher queue decisions plus
posting's already-paid lines), it reports per case how often the true loan is the first suggestion (hit@1)
or among the 5 (hit@5). The shortlist is measured without leaving out the matcher's candidates.

Measured 17 Nov 2026, synthetic statement (273 queued lines):

| Case | Lines | hit@1 | hit@5 | any suggestion |
|---|---|---|---|---|
| paid_by_other | 50 | 48 (96.0%) | 50 (100%) | 50 |
| ambiguous | 85 | 62 (72.9%) | 85 (100%) | 85 |
| partial | 69 | 61 (88.4%) | 69 (100%) | 69 |
| overpaid | 53 | 43 (81.1%) | 53 (100%) | 53 |
| unrelated | 16 | (no true loan) | | 0 |

The two paid_by_other misses at hit@1 are exact ties (twin loans with the same surname, amount and due
date), shown side by side. A first version let 3-letter words count as misspelt surnames, and "SALARY ADV"
was offered four Yadavs; requiring 4+ letters for a fuzzy match brought unrelated lines to 0 suggestions.

## Known limits

- The synthetic relatives pay the exact instalment on the due date under the borrower's surname, which is
  what the scoring rewards; real relatives with another surname (a married daughter) are not found by name,
  only by an exact amount. Measure on real queued lines before trusting the rates.
- shortcut: `NOT_NAMES` is a short English list of boilerplate words for banks with no `FORMATS` line; add a
  bank's own words when one turns up.
- The demo (`--demo`) has no line paid by a relative: its counts are pinned by existing tests. The Review
  tab was rendered in headless Chrome on a temp copy of the synthetic data instead.
- No live provider call was made; the AI layer was tested with fakes only.
