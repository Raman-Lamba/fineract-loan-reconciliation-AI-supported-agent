# Stage C: names from other countries

Goal (spec.md): match each bank line to exactly one loan, or hand it to a person with the candidates and the reason.
Never guess. Stage C widens one input to that decision, "does this payer's name fit this borrower", so that a
name which genuinely fits one borrower is found in more countries, while a name that could fit several, or fits
only weakly, still goes to a person.

## Decision: one pure module, conservative by construction

`matcher/names.py` (standard library: `unicodedata`, `re`) holds normalising, splitting and the fit rules.
`matcher.match.decide` and `review.suggest` call it; nothing else in the matcher changed (exact amount, the 5-day
window, the duplicate guard, "two that fit go to a person"). No network call and no AI call were added.
`ai/narration.py` still keeps a model's name only if it appears word for word in the narration (and has Latin
letters), unchanged.

The safety rule for every tolerance: it may only recognise the same person written differently (case, accents,
word order, punctuation, a cut-short field, a script's own normal form). Anything else is no fit.

## Normal form

`normalise(text)`: NFKC, casefold, combining marks dropped after Latin and Greek letters only (in Cyrillic, Й is
not И; in Devanagari a vowel sign is part of the letter), apostrophes removed (O'Brien = OBRIEN), other punctuation
and digits become spaces, hyphens kept inside a word, and leading titles (Mr, Dr, Sri, Shri, Smt...) dropped while
two words remain (so "Sri Wahyuni", where Sri is a given name, keeps it). `tokens(name)` is the list of words.
ß casefolds to ss (WEISS = Weiß); Ü is not taken for UE (MUELLER is not Müller): both surnames exist.

## Given and family name: NAME_ORDER

Fineract gives one `clientName`; `statement.fetch_schedules` still writes `firstname, lastname =
clientName.partition(" ")` (unchanged files and pages) and now also `name_tokens` (the normalised words). A loan
from an older `schedules.json` without `name_tokens` is read from its first and last name, with the same result.

- `western` (default, every preset): the first word is the given name, the rest the family name(s).
- `family-first`: the first word is the family name (East Asia, Hungary). UNVERIFIED how often a Fineract holds
  names this way: its display name is built given name first, so this is for a book where staff typed the family
  name into the first-name box.
- `auto`: either reading, but whole words only: no initial, prefix, cut-short or run-together tolerance, because
  with the order unknown a partial word could stand for either name.

## Rules of `fits(payer, borrower, order)`

| Rule | Fits | Does not fit |
|---|---|---|
| (a) every borrower word in the payer, any order | José García López / GARCIA LOPEZ JOSE | |
| (b) family name whole + given name equal, 3+ letter prefix or initial | Ramesh Kumar / R KUMAR | RA KUMAR |
| (c) family name cut to 2+ letters; run together and cut | RAMESH KUM; RAMESHKUMA | RAMESH K |
| (d) extra middle name/patronymic, given name in full, not in first place | Ivan Sidorov / IVAN PETROVICH SIDOROV | PYOTR IVAN SIDOROV |
| (e) single-name person: exact only | Sukarno / SUKARNO | SUKARN, SUKARNO HATTA |
| (f) joint payer split on AND, &, /, comma (each part two or more words) | Anita Nair / RAMESH KUMAR AND ANITA NAIR | |
| (g) company suffix in the payer never fits a person | | RAMESH KUMAR PVT LTD |

Particles (van, der, de, la, da, das, bin, ibn, al, von...) are ignored when followed by another name, but never as a
borrower's first word (Al Smith) and never as the last word (Lakshmi Das). The payer is also tried as written, so
DAS LAKSHM still fits Lakshmi Das. A hyphenated word fits written whole, joined or apart.

Scripts: letters of different scripts never fit (no transliteration). A script without letter case (Arabic,
Hebrew, Devanagari...) fits by whole words only. A script without spaces between words (CJK, kana, Hangul, Thai,
Lao, Khmer, Myanmar) fits only as the same whole name, or the same words in another order.

## The look-alike rule

"Another borrower with the same surname and first initial owes the same amount: queue both" stays, on normalised
keys (`alike_keys`: family name joined, first letter of the given name). `western` uses its own reading only (so
the default is byte-identical). `family-first` and `auto` use both readings: a look-alike missed would be a wrong
post, one too many only a queued line. Measured: with `family-first` alone the Indian synthetic statement gave 41
wrong posts (all same-surname-same-initial pairs read the other way round); with both readings, 0.

## The local suggester

`review.suggest` reads names in the same normal form (accents, titles), tries the payer's words with and without
particles, and reads the borrower's given and family name by NAME_ORDER (both for `auto`). Its scores and labels
are otherwise unchanged.

## Measured (17-18 Nov 2026 synthetic data)

- `data\decisions.csv` from the synthetic statement: sha256 9f2c9f7f... before and after; the HDFC export imported
  to a temp folder and matched with `--bank hdfc`: the same hash.
- `review.measure_suggestions`: identical before and after (hit@1 214 of 257, hit@5 257 of 257).
- On the same data: `western` 1,348 posts, 0 wrong; `family-first` 1,265 posts, 0 wrong; `auto` 706, 0 wrong.
- The multi-country cases live in `tests/test_names.py` (made-up people), not in `data/`.

## Not done, on purpose

Transliteration, nicknames, married or maiden names, a payer dropping a borrower's middle or second family name,
Mac/Mc equivalence, UE/Ü. All unverified against any real bank's payer field.
