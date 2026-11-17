# AI statement reader (stage one) and the no-code page

Goal (spec.md): an agent matches messy bank lines to loans in Fineract, posts only clear ones, splits each
80:20 in an auditable ledger, and queues anything unclear for a person with candidates and reasons. Never
guess, never post twice. Until now it read only HDFC-layout exports and a few narration styles, and a person
needed a terminal and a text editor. This stage lets a language model READ any bank's statement, and lets a
person do everything from the page.

## Decision: the model reads, the rules decide

| The model may | The model may not |
|---|---|
| Propose a column map for a layout not seen before | Supply an amount, a date or a txn_id |
| Return payer_name, channel, reference, loan_account for a narration the rules cannot read | Choose a loan, post, approve, or change an outcome |
| | Override anything the rules read themselves |

Where each rule is enforced:

| Rule | Function |
|---|---|
| Fields fill a blank only; a rule's own reading always wins | `matcher.match.decide(..., extracted)`: SCPL account first, then the bank's `FORMATS` name, then the model's |
| A name or loan account must be in the narration word for word | `ai.narration.valid` and again `matcher.match._literal` |
| A reference must appear exactly, or it is discarded | `ai.narration.valid` |
| A loan account counts only if it is a known loan | `matcher.match.decide` (else the name rule decides) |
| The same exact-amount, 5-day, one-borrower, no-look-alike rules for every post | `matcher.match.decide`, unchanged |
| Money fields come from the file's own columns | `statement.import_bank.convert(..., cmap)`; the model's map is only a list of column numbers |
| A map is used only if every row of the file reads with it | `ai.layout.check` (runs the import itself) |
| A person confirms a map before it is saved | `ui.serve.import_statement` -> `ai.layout.save` |
| Malformed, cut-off or refused answers fall back | `ai.client.answer` raises `AIError`; `ai.narration.extract` leaves the narration unread; `ui.serve.read_statement` falls back to choosing by hand |
| No request without a key and the person's consent | `ai.client.sender` |
| Only narration text and masked rows leave the machine | `ai.layout.shape` (digits -> 9, 20 rows), `ai.narration.prompt` (narrations only) |
| The key is never shown, logged or returned | `ui.serve.settings_view` (only "saved"), `Handler.reply` (scrubs it from every JSON answer) |
| Reasons resting on a model's field say so | `matcher.match.AI`, appended to the reason, so the audit log shows it |

## Request shape

Plain HTTPS POST to `https://api.anthropic.com/v1/messages` with `urllib` (no SDK: the project is stdlib
only). Headers `x-api-key`, `anthropic-version: 2026-10-21`. Body: `model` (`AI_MODEL`, default
`claude-opus-5-5`), `max_tokens`, `system`, one user message, `output_config: {effort: "low", format:
{type: "json_schema", schema}}`; for models that support it, `fallbacks: "default"` with the
`server-side-fallback-2026-07-01` beta header. Thinking is left at the model's default. Timeout 120 s, one
retry on 429 or 5xx (honouring `retry-after` up to 10 s). Only `stop_reason == "end_turn"` is accepted.

## Layout

Order of lookup: the HDFC header names (no model); a map confirmed before, keyed by a sha256 of the
normalised header row (`data/layouts.json`); the model, if a key and consent exist; otherwise the person
chooses from dropdowns. A map: header row, date column and format (day-first formats only), narration,
reference or -1, and a style: `two_columns` (debit, credit), `type_column` (amount, Dr/Cr), `suffix`
(`8,450.00 Cr`), `signed` (`-590.00`). With a map, every transaction row's date must read, not only the
credits'. The HDFC layout, left unchanged on the page, still goes through `convert(..., "hdfc")`.

## Narrations

Only narrations with no SCPL account and no name under the bank's `FORMATS` line are sent, unique, 40 per
request. Validated answers are cached by sha256 of the narration (`data/ai_cache.json`) and checked again
when read back. A bank with no `FORMATS` line is `other`.

## The page

Tabs in a new person's order: Connect (form -> `.env`, merged line by line, secrets write-only, the running
Fineract client updated at once; Test connection), Statement (upload <= 5 MB, read, dropdowns, preview with
the chosen columns marked, import with refusals shown by row number), Run (read the loans, Match with a
table of outcomes and reasons without names, Post after a confirmation of the count and rupee total that the
server checks again), Review (unchanged), Guide (the old Setup tab). Match also rebuilds the review queue.
`statement.json` notes which bank's narrations the imported statement holds (tied by sha256), so the Run tab
reads it with the right `FORMATS` line; a statement nobody imported is the synthetic one.
`Start.bat` runs `start.ps1 -Ui`, which now opens the browser and starts the page even when the start-up
connection check fails (the Connect tab is where that is fixed).

## Known limits

- shortcut: day-first date formats only; a month-first export is refused.
- shortcut: the model's `reference` and `channel` are validated and cached but not used: txn_id stays the
  file's reference column or the one 12+ digit number in the narration, and the payment type stays
  `posting.post.channel`. Use them when a bank is found that needs them, still under the literal-text check.
- Live calls to Anthropic were not made; everything was tested with a fake model.
- The ICICI, SBI and Axis fixtures are hand-made from secondary notes and unverified.
- One local user and no login, as before.
