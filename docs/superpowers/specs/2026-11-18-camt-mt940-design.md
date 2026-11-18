# Global rollout, stage B: camt.053 and MT940 statements

Date: 18 Nov 2026. Status: built. Every format detail is **unverified**: written from the public ISO 20022 and SWIFT
descriptions as remembered, tested only on hand-made files in `tests/fixtures/`. No real bank file has been through it.

## Goal

The agent matches bank payments to loans in Fineract, posts only the clear ones and queues the unclear lines for a person; it
never guesses and never posts twice. Before this stage it read CSV exports only. Most banks in Europe, the UK and many other
countries export ISO 20022 **camt.053** (XML) or SWIFT **MT940** (text). Both carry structured fields (bank reference,
end-to-end id, payer name, payer account, remittance text), so a line can be matched on the payer's own name field instead of
a name cut out of a free-text narration.

## Shape

One module, `statement/import_structured.py`, pure (no files): `detect(text)`, `read(text, settings)` and `read_bytes` for the
command line. Each reader yields raw entries; one shared step turns them into the same `txn_id, date, amount, narration` lines
the CSV importer writes, so the matcher, posting, review and the page need no change of their own.

- **Command line**: `python -m statement.import_bank <file>` reads the file once; a camt.053 or MT940 is read here whatever
  `--bank` says, and `statement.json` notes `camt053` or `mt940` as the bank. A CSV goes down the old path untouched.
- **Page**: the Statement tab's upload refuses a camt.052/054 or an attack file at once; "Read my statement" shows a preview of
  the lines (date, amount, payer, narration, reference) and the counts instead of the column pickers; "Looks right, import it"
  writes the statement and sets `BANK` to the format. No AI call: the fields are the file's own.

## Detection (by content, never by file name)

Text that starts with `<` is XML. A `DOCTYPE` or `ENTITY` anywhere refuses it before a parser sees it. A `BkToCstmrStmt`
element (any namespace prefix) is camt.053; `BkToCstmrAcctRpt` (camt.052) and `BkToCstmrDbtCdtNtfctn` (camt.054) and any other
XML are refused with the list of supported formats. Text with a `:20:` tag and a `:61:` tag at line starts (or straight after a
SWIFT `{4:` block) is MT940. Everything else is a CSV.

## Mapping

| Field | camt.053 | MT940 |
|---|---|---|
| credit or not | `CdtDbtInd` CRDT; `RvslInd` true is a reversal; `Sts` (or `Sts/Cd`) other than BOOK is not booked yet | mark `C`; `D` debit; `RC`/`RD` reversals |
| date | `BookgDt` (`Dt` or the date part of `DtTm`), else `ValDt` | the :61: entry (booking) date MMDD, else the value date YYMMDD; the year follows the value date across a new year |
| amount | `Amt` (a dot decimal); more decimals than the currency has is refused, not rounded | :61: amount (a comma decimal) |
| currency | `Amt/@Ccy`, else `Acct/Ccy` | the :60F:/:60M: opening balance of that statement |
| txn_id | `AcctSvcrRef` (entry, then transaction), else `TxId`, else `EndToEndId`; `NOTPROVIDED` is no reference | the bank reference after `//`, else the customer reference; `NONREF` is no reference |
| payer name | `RltdPties/Dbtr/Nm` or `Dbtr/Pty/Nm` (version 08 and later) | `?32` + `?33` of a `?nn` :86:; none in free text |
| remittance | `RmtInf/Ustrd`, `RmtInf/Strd/CdtrRefInf/Ref`, else `AddtlTxInf`/`AddtlNtryInf` | `?20`-`?29`, `?60`-`?63` (a space before SEPA keys `EREF+`, `SVWZ+`..), plus the :61: supplementary line; free text as it is |
| account | `DbtrAcct/Id/IBAN`, else `Othr/Id` (a UK sort code and account) | `?31` |
| channel | a configured `CHANNELS` name standing in `BkTxCd/Prtry/Cd` or `AddtlNtryInf`; ISO sub-family `ESCT`/`ESDD` count as SEPA | a configured name in `?00` or the :61: transaction type |

Narration: `payer name | remittance | account | channel`. A `|` inside a field becomes `/`, so position 0 is always the payer's
name field (empty when the file has none, which reads as `| remittance`). `matcher.match.FORMATS` has `camt053` and `mt940` with
that one rule, so the matcher's rules are unchanged: an SCPL loan account anywhere in the line, else the payer name and the
amount, post only when exactly one loan fits, otherwise queue. A credit with no reference gets `noref_id`, the CSV importer's own
(now a function in `import_bank.py`), and the matcher never posts it.

A reference becomes Fineract's external id and part of a URL path, so any character other than letters, digits and `. _ -`
becomes `-` (shortcut: two references differing only there would count as one; the second is then ignored, never posted twice).

## Never dropped quietly

Counted and printed: entries read, credits kept, debits skipped, reversals skipped, entries without a reference, and (when
non-zero) entries not yet booked and zero amounts. An entry whose amount, date, credit/debit mark or :61: line cannot be read,
or whose currency is not `CURRENCY`, stops the import with the entry's number, statement and reference, and nothing is written.

## Security

Standard library only (`xml.etree` after the refusal, `re`, `decimal`). No DOCTYPE or ENTITY (entity expansion and external
entities), size cap 20 MB (`MAX_BYTES`; the page's own upload limit of 5 MB is lower), at most 100,000 entries (`MAX_ENTRIES`),
nothing fetched from the network. Namespaces are dropped after parsing, so versions 02 to 12 read the same.

## Shortcuts and what is not done

- A batch booking (several `TxDtls` in one entry) is one line with no payer name, so a person decides it. Split per `TxDtls` if
  a lender's bank books incoming payments that way.
- MT940 two-digit years are read as 20YY. Latin-1 is accepted for an MT940 file that is not UTF-8 on the command line; the page
  reads files as UTF-8, so an umlaut in a Latin-1 file shows as a replacement character there.
- MT940 :86: dialects: the German `?nn` style and plain free text. The `/NAME/../REMI/..` style of some Dutch and Belgian banks,
  and any other, is kept as free text with no payer name (the optional AI reader or a person reads it). MT942 and camt.052/054
  are not supported.
- **Unverified**: every field path above, the `?nn` subfield numbers, the :61: layout (optional funds code, `NONREF`), the meaning
  of `RvslInd` and of `RC`/`RD`, and the ISO sub-family codes. The fixtures are made up from the descriptions, not taken from banks.
  A real export from the lender's bank is the real test: import it, read the preview and the first queue, and correct this module.
