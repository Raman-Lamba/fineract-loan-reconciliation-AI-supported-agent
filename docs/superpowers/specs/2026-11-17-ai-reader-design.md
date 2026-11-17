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
| Malformed, cut-off or refused answers fall back, for every provider | `ai.client.answer` (Claude) and `ai.client.chat_answer` (the rest) raise `AIError`; `ai.narration.extract` leaves the narration unread; `ui.serve.read_statement` falls back to choosing by hand |
| No request without a key (a local `other` model needs none) and the person's consent for this provider | `ai.client.sender` |
| Only narration text and masked rows leave the machine | `ai.layout.shape` (digits -> 9, 20 rows), `ai.narration.prompt` (narrations only) |
| The key is never shown, logged or returned | `ui.serve.settings_view` (only "saved"), `Handler.reply` (scrubs it from every JSON answer) |
| Reasons resting on a model's field say so | `matcher.match.AI`, appended to the reason, so the audit log shows it |

## Providers (added after stage one)

Only the transport changes; every rule above is provider-independent. `ai.layout` and `ai.narration` still
build one request (`ai.client.request`, Claude's body shape: system, one user message, JSON schema) and get
back a dict. Two adapters in `ai.client` carry it:

- **Claude** (`AI_PROVIDER=claude`, the default; key `ANTHROPIC_API_KEY`): unchanged. Plain HTTPS POST to
  `https://api.anthropic.com/v1/messages`, headers `x-api-key`, `anthropic-version: 2026-10-21`; `model`
  (`AI_MODEL`, default `claude-haiku-5-5`), `max_tokens`, `system`, one user message, `output_config: {effort:
  "low", format: {type: "json_schema", schema}}`; for models that support it (not Haiku) `fallbacks: "default"` with the
  `server-side-fallback-2026-07-01` beta header. Only `stop_reason == "end_turn"` is accepted.
- **OpenAI-style chat completions** (`openai`, `gemini`, `other`): POST `<base>/chat/completions` with
  `Authorization: Bearer <key>` (no header for a keyless local service), `model`, `messages` (system + user),
  `max_completion_tokens` (OpenAI) or `max_tokens`, and `response_format`. Format ladder, strictest first:
  `json_schema` (`strict: true`), `json_object`, none (the schema is then put in the system prompt in words).
  A 400 or 422 steps down one rung, remembered for that sender; other errors never do. Only
  `finish_reason == "stop"` is accepted; `refusal`/`content_filter` is a refusal, `length` a cut-off. The text
  may carry a code fence or a sentence around the JSON; the outermost braces are parsed, and the object then
  goes through the same checks as Claude's.

Presets (`ai.client.PROVIDERS`): `openai` -> `https://api.openai.com/v1`; `gemini` ->
`https://generativelanguage.googleapis.com/v1beta/openai/`; `other` -> `AI_BASE_URL` (which applies to `other`
only, so a stale address can never receive another provider's key). Each provider has its own key variable
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `AI_API_KEY` for `other`), and the Connect tab's one
write-only key box saves under the chosen provider's name. `other` needs an `https://` address unless it is on
this computer (localhost, 127.0.0.1, ::1), where no key is needed and the consent text says nothing leaves the
machine. Model lists for the dropdown live in the same table, each with a note of what was and was not verified
in the provider's docs on 2026-11-17 (Gemini URL and ids, OpenAI ids: read; the raw `json_schema` fields of both
and every `other` model name: unverified).

Timeout 120 s and one retry on 429 or 5xx (honouring `retry-after` up to 10 s) are the same for all.

**Caches** key on provider + model: `send.scope` (`provider:model`, plus the address for `other`) is hashed in
with the narration in `data/ai_cache.json`; an entry from before (a bare narration hash) is a miss. The layout
store `data/layouts.json` stays keyed by the header row only: it holds maps a person confirmed after the whole
file was checked, and it is also what the no-AI path reads, so it is not a model's answer to be invalidated.

**Consent** (`data/ai_consent.json`) records the provider (and for `other` the address). The consent text names
the provider and model; changing provider, or the address of `other`, asks again; changing only the model does
not. A file from before providers counts for Claude only.

**Test AI** (`/api/testai` -> `ai.client.try_it`): one fixed prompt, no statement data, through the same
`sender` (so it needs the key and the consent tick like any call), answered as pass/fail with a plain-English fix.

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
- Live calls to any provider (Anthropic, OpenAI, Google, a local model) were not made; everything was tested with fake responses. A weaker model sends more lines to a person, never a wrong posting: the review-queue count is the measure of how well a model reads a bank.
- The ICICI, SBI and Axis fixtures are hand-made from secondary notes and unverified.
- One local user and no login, as before.
