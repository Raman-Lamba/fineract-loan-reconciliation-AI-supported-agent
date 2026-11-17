# Global rollout, stage A: locale settings

Date: 17 Nov 2026. Status: built.

## Goal

The agent matches bank payments to loans in Fineract, posts only the clear ones, splits each between a partner bank and the
lender, and queues the unclear lines for a person. It must work for a lender in any country. Before this stage India was
wired in: INR and the rupee sign with lakh grouping, the channels UPI/NEFT/IMPS/NACH, day-first dates only, a fixed 80:20
split and `.`-decimal amounts only. Now each of those is a setting typed on the Connect tab and saved into `.env`, and the
defaults reproduce the old behaviour exactly.

Stage B (camt.053 and MT940 importers) and stage C (name conventions) are not part of this stage.

## The settings

| Setting | Values | Default |
|---|---|---|
| `COUNTRY` | India, United Kingdom, Eurozone (SEPA), United States, Brazil, Kenya, Other | India |
| `CURRENCY` | ISO 4217 code | INR |
| `PARTNER_SHARE_PERCENT` | 0 to 100 inclusive, decimals allowed | 80 |
| `DATE_ORDER` | `auto`, `day-first`, `month-first` | `day-first` for India, `auto` for the other presets |
| `NUMBER_FORMAT` | `auto`, `1,234.56`, `1.234,56`, `1 234,56`, `1'234.56` | `1,234.56` for India, `auto` for the other presets |
| `CHANNELS` | comma list of payment-type names | `UPI,NEFT,IMPS,NACH` for India |

One module, `locale_settings.py`, holds the preset table, the `Settings` value, `load(values)` (strict: a bad value raises a
`ValueError` in plain words) and the minor-unit table. A preset is only the table that fills the Connect form's boxes; after that
every box is the person's. `Settings` is a frozen dataclass whose default is India's, and every function that needs one takes it
as an optional last argument defaulting to that, so existing callers and tests are unchanged.

## Where each setting acts

- **Split** (`posting.post.split`): `partner = round_half_up(amount * share / 100, minor unit)`, `lender = amount - partner`, in
  Decimal only, so the two sum exactly for any share and any minor unit. The old `PARTNER_SHARE` constant is gone. Messages use
  `Settings.ratio` (`80:20`, `62.5:37.5`).
- **Minor unit**: Fineract's own decimal places for the currency when it can be asked (`locale_settings.fineract_digits`, used
  where posting happens), else a table of exceptions (JPY 0; KWD, BHD, OMR and a few others 3; everything else 2). The statement
  import has no Fineract client, so it uses the table; the connection test says if Fineract disagrees.
- **Dates** (`statement.parse`, `statement.import_bank`): the format list grew (ISO, month-first numeric, `%d.%m.%y`, month
  names). The `DATE_ORDER` setting, not the model or the format picked, decides which of day and month comes first in a numeric
  format (`with_order`; year-first and month-name formats have only one order). With `auto`, `convert()` runs a first pass over
  the whole file that only records the numeric dates it sees and every amount cell, works the order out, then runs the real
  pass. A first part above 12 means day-first; a second part above 12 means month-first; both in one file is refused; all
  ambiguous is refused, naming the column and the first row, and asking the person to choose on the Statement tab. The file
  is never guessed. `ai/layout.py` shares the list; the model's choice of format only sets separators and year width.
- **Amounts** (`statement.parse.parse_amount`): one regex per number format, with Indian grouping read only when the country is
  India (`1,23,456.78`), and as many decimals as the currency has. `(1,234.56)`, `-1.234,56`, a Unicode minus and a trailing minus
  are negative; a Cr or Dr suffix is read only in the layout style that says amounts carry one (so a stray `8450.00 Cr` in a
  credit column is still refused, as before). A negative number in a debit column is a debit; in a credit column it is refused.
  With `auto`, `infer_number_format` looks at the whole column: both marks in one value show the decimal mark; a repeated mark,
  a space or an apostrophe is a thousands mark; one mark and 1 or 2 digits is a decimal mark. One mark and exactly 3 digits
  (`1.234`) could be either and, if nothing else in the column decides, is refused. Conflicting evidence is refused.
- **Channels** (`posting.post.channel`): the first configured name that stands as a whole word in the narration (longest names
  first, so `SEPA INSTANT` is found before `SEPA`; spaces in a name match any run of spaces). It is posted as the Fineract payment
  type of that name (compared case-insensitively) when one exists, otherwise with none, as before.
- **Single currency**: a statement column named Currency, Ccy or Curr whose value on a credit row differs from `CURRENCY` refuses
  the import with the row number; the connection test fails if the loans Fineract lists are in another currency. Nothing is
  converted.
- **The page**: money is shown with `Intl.NumberFormat(locale, {style: "currency", currency})`, the locale coming from the preset,
  and falls back to the ISO code and a plain number if the browser rejects either. Server-written text, CSV and report files keep
  plain `1234.56` with a dot and no symbol. The connection test's names follow the settings (`GBP is an enabled currency`,
  `Payment types SEPA, SEPA INSTANT, SCT, SDD exist`); the channels check lists what was found and what was not and never fails
  on its own.

## What did not change, on purpose

Money amounts, dates and txn ids never come from a model. An ambiguous amount or date refuses the import with the row and column
named. The matcher is unchanged (it is currency-blind; see the shortcut note on `_paise` for 3-decimal currencies, where the
worst case is a line queued). The posting duplicate guard and the audit are unchanged. The ledger, audit, queue and report column
names are unchanged (single currency per run); the partner report prints the currency on the console, not in its header, so
a default run's files are byte-identical.

## Changed existing tests

- `tests/test_ai.py::test_a_date_that_does_not_parse_is_refused_with_its_row`: used `%m/%d/%Y` as the example of a format the
  importer does not read; it is now a valid format, so the example is `%d/%m/%Y %H`.
- `tests/test_ui.py::test_approve_writes_the_decision_into_queue_csv_then_runs_review_apply`: `review.apply.run` is now also given
  the settings, so the call assertion allows `settings=ANY`.
- `tests/test_ui.py::test_a_missing_payment_type_is_named`: became `..._but_is_not_a_failure`. The channels check is
  informational by design, so a missing payment type no longer fails it; it is still named in the detail.

## Verification and limits

Presets other than India's are unverified (see the README, "Using it outside India"). The decimal-places key names read from
Fineract are unverified. Stage B will add camt.053 and MT940; stage C name conventions and the loan-account prefix.
Co-lending rules and split percentages differ by country; this is not legal or compliance advice.
