"""camt.053 and MT940 statements: read from the made-up fixtures (format details unverified against real bank
files), through the command line, the matcher and the page. Nothing reaches a Fineract or an AI service."""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from locale_settings import DEFAULT  # noqa: E402
from matcher.match import decide, payer_name  # noqa: E402
from statement import import_structured as structured  # noqa: E402
from tests.test_ui_connect import ENV, Page  # noqa: E402

FIXTURES = Path(__file__).with_name("fixtures")
EUR = replace(DEFAULT, country="Eurozone (SEPA)", currency="EUR", channels=("SEPA", "SEPA INSTANT", "SCT", "SDD"))
GBP = replace(DEFAULT, country="United Kingdom", currency="GBP",
              channels=("FASTER PAYMENTS", "BACS", "CHAPS", "DIRECT DEBIT"))
SETTINGS = {"camt053_sepa.xml": EUR, "camt053_uk.xml": GBP, "camt053_multi.xml": EUR, "mt940_german.sta": EUR,
            "mt940_multi.txt": GBP}


def text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def read(name, settings=None):
    return structured.read(text(name), settings or SETTINGS[name])


def loan(ref, first, last, due, amount, account="000000001"):
    return {"loan_ref": ref, "loan_id": int(ref[1:]), "account_no": account, "client_id": 1, "firstname": first,
            "lastname": last, "instalments": [{"due": due, "amount": amount}]}


class Camt053Test(unittest.TestCase):
    def test_sepa_credits_debits_reversals_and_a_missing_reference(self):
        fmt, lines, counts = read("camt053_sepa.xml")
        self.assertEqual(fmt, "camt053")
        self.assertEqual(lines[:2], [
            {"txn_id": "FAKEBANK0001", "date": "2026-11-05", "amount": "250.00",
             "narration": "Anna Weber | Rate November Darlehen | DE00999988880000000011 | SEPA"},
            # no AcctSvcrRef and TxId NOTPROVIDED: the EndToEndId; the structured creditor reference is the remittance
            {"txn_id": "E2E-FISCHER-7", "date": "2026-11-06", "amount": "180.25",
             "narration": "Jonas Fischer | RF00FAKE7 |  | SEPA"}])
        self.assertTrue(lines[2]["txn_id"].startswith("NOREF-2026-11-06-"))  # value date: no booking date given
        self.assertEqual((lines[2]["amount"], lines[2]["narration"]), ("75.00", "Lena Hoffmann | Bareinzahlung"))
        self.assertEqual(counts, {"rows": 5, "credits": 3, "debits": 1, "empty": 0, "reversals": 1, "pending": 0,
                                  "no_reference": [5]})

    def test_uk_gbp_with_sort_code_and_account_and_an_entry_not_yet_booked(self):
        _, lines, counts = read("camt053_uk.xml")
        self.assertEqual(lines, [{"txn_id": "FAKEUK-FP-0001", "date": "2026-11-10", "amount": "312.40",
                                  "narration": "Oliver Bennett | NOV INSTALMENT | 00000012345678 | FASTER PAYMENTS"}])
        self.assertEqual((counts["debits"], counts["pending"], counts["reversals"]), (1, 1, 0))

    def test_two_statements_two_accounts_a_batch_and_a_bar_in_the_text(self):
        _, lines, counts = read("camt053_multi.xml")
        self.assertEqual([line["txn_id"] for line in lines], ["FAKEMULTI-A1", "FAKEMULTI-A2", "FAKEMULTI-B1"])
        self.assertEqual(lines[0]["narration"], "Sanne de Vries | termijn november / lening")  # '|' cannot move a field
        self.assertEqual(payer_name(lines[1]["narration"], "camt053"), "")  # a batch has no one payer: a person decides
        self.assertIn("BATCH OF 2 PAYMENTS", lines[1]["narration"])
        self.assertEqual(counts["credits"], 3)

    def test_namespace_versions_and_prefixes_read_the_same(self):
        base = text("camt053_sepa.xml")
        expected = structured.read(base, EUR)
        for version in ("02", "04", "08", "10", "12"):
            variant = base.replace("camt.053.001.02", f"camt.053.001.{version}")
            self.assertEqual(structured.read(variant, EUR), expected, version)
        no_namespace = base.replace(' xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"', "")
        self.assertEqual(structured.read(no_namespace, EUR), expected)

    def test_a_doctype_or_entity_is_refused_before_parsing(self):
        with mock.patch.object(structured.ET, "fromstring") as parser:
            for attack in (text("camt053_doctype_attack.xml"),
                           '<?xml version="1.0"?><!ENTITY x "y"><Document><BkToCstmrStmt/></Document>',
                           '<!doctype Document SYSTEM "http://example.invalid/x.dtd"><Document/>'):
                with self.assertRaises(ValueError) as caught:
                    structured.read(attack, EUR)
                self.assertIn("DOCTYPE or ENTITY", str(caught.exception))
            parser.assert_not_called()

    def test_an_oversize_file_and_too_many_entries_are_refused(self):
        with mock.patch.object(structured, "MAX_BYTES", 1000), self.assertRaises(ValueError) as caught:
            read("camt053_sepa.xml")
        self.assertIn("over", str(caught.exception))
        with mock.patch.object(structured, "MAX_ENTRIES", 4), self.assertRaises(ValueError) as caught:
            read("camt053_sepa.xml")
        self.assertIn("more than 4 entries", str(caught.exception))

    def test_another_currency_is_refused_with_the_entry_named(self):
        with self.assertRaises(ValueError) as caught:
            read("camt053_sepa.xml", GBP)
        self.assertIn("entry 1 (statement FAKE-STMT-2026-11-05, reference FAKEBANK0001)", str(caught.exception))
        self.assertIn("'EUR' but CURRENCY is GBP", str(caught.exception))

    def test_more_decimals_than_the_currency_has_is_refused_not_rounded(self):
        with self.assertRaises(ValueError) as caught:
            structured.read(text("camt053_sepa.xml").replace(">250.00<", ">250.005<", 1), EUR)
        self.assertIn("more decimals", str(caught.exception))

    def test_an_unreadable_entry_stops_the_import(self):
        for old, new, words in ((">180.25<", ">180,25<", "cannot read the amount"),
                                ("<CdtDbtInd>CRDT</CdtDbtInd>\n        <Sts>", "<CdtDbtInd>X</CdtDbtInd><Sts>",
                                 "credit or a debit"),
                                ("<BookgDt><Dt>2026-11-06</Dt></BookgDt>", "", "no readable booking or value date")):
            with self.assertRaises(ValueError) as caught:
                structured.read(text("camt053_sepa.xml").replace(old, new, 1), EUR)
            self.assertIn(words, str(caught.exception))

    def test_a_reference_is_made_safe_for_a_url(self):
        _, lines, _ = structured.read(text("camt053_sepa.xml").replace("FAKEBANK0001", "FAKE/BANK 0001?"), EUR)
        self.assertEqual(lines[0]["txn_id"], "FAKE-BANK-0001-")


class Mt940Test(unittest.TestCase):
    def test_german_subfields_reversal_and_no_reference(self):
        fmt, lines, counts = read("mt940_german.sta")
        self.assertEqual(fmt, "mt940")
        self.assertEqual(lines[0], {"txn_id": "FAKEBK000101", "date": "2026-11-18", "amount": "250.00",
                                    "narration": "Anna Weber | EREF+E2E-WEBER-12 SVWZ+Rate Dezember Darlehen | "
                                                 "DE00999988880000000011 | SEPA"})
        self.assertEqual(lines[1]["txn_id"], "E2E-FISCHER-8")  # no bank reference: the customer's
        self.assertEqual(payer_name(lines[1]["narration"], "mt940"), "Jonas Fischer")  # ?32 and ?33 joined
        self.assertTrue(lines[2]["txn_id"].startswith("NOREF-2026-11-18-"))  # NONREF and nothing after //
        self.assertEqual(counts, {"rows": 5, "credits": 3, "debits": 1, "empty": 0, "reversals": 1, "pending": 0,
                                  "no_reference": [5]})

    def test_two_statements_and_free_text(self):
        _, lines, counts = read("mt940_multi.txt")
        self.assertEqual(lines, [
            {"txn_id": "FAKEUK-940-01", "date": "2026-11-10", "amount": "312.40",
             "narration": "| OLIVER BENNETT NOV INSTALMENT FASTER PAYMENT"},
            {"txn_id": "FAKEUK-940-04", "date": "2026-11-11", "amount": "95.00",
             "narration": "| /ORDP//NAME/Grace Okafor/REMI/USTD//loan 4472/"}])  # the /NAME/ style: free text
        self.assertEqual((counts["rows"], counts["debits"], counts["reversals"]), (4, 1, 1))
        self.assertEqual(payer_name(lines[0]["narration"], "mt940"), "")  # free text: no name is guessed

    def test_the_booking_date_across_a_new_year_and_the_value_date_fallback(self):
        head = ":20:X\n:60F:C261231EUR0,00\n"
        _, lines, _ = structured.read(head + ":61:2612310102C10,00NTRFNONREF//B1\n:61:270102C11,00NTRFNONREF//B2\n", EUR)
        self.assertEqual([line["date"] for line in lines], ["2027-01-02", "2027-01-02"])
        _, lines, _ = structured.read(head + ":61:2701021231C10,00NTRFNONREF//B1\n", EUR)
        self.assertEqual(lines[0]["date"], "2026-12-31")

    def test_currency_comes_from_the_opening_balance(self):
        with self.assertRaises(ValueError) as caught:
            read("mt940_german.sta", GBP)
        self.assertIn("entry 1 (statement FAKE-STA-1118)", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            structured.read(":20:X\n:61:261118C10,00NTRFNONREF//B1\n", EUR)
        self.assertIn("(not given)", str(caught.exception))

    def test_an_unreadable_61_line_stops_the_import(self):
        with self.assertRaises(ValueError) as caught:
            structured.read(":20:X\n:60F:C261117EUR0,00\n:61:26111C10,00\n", EUR)
        self.assertIn("cannot read the :61: line", str(caught.exception))


class DetectTest(unittest.TestCase):
    def test_by_content_not_by_name(self):
        for name, fmt in (("camt053_sepa.xml", "camt053"), ("camt053_multi.xml", "camt053"), ("mt940_german.sta", "mt940"),
                          ("mt940_multi.txt", "mt940"), ("sbi_style.csv", "csv"), ("axis_style.csv", "csv")):
            self.assertEqual(structured.detect(text(name)), fmt, name)
        self.assertIsNone(structured.read(text("sbi_style.csv"), DEFAULT))

    def test_camt052_054_and_other_xml_are_refused_in_plain_words(self):
        for root, words in (("BkToCstmrAcctRpt", "camt.052"), ("BkToCstmrDbtCdtNtfctn", "camt.054"), ("Invoice", "not a camt.053")):
            with self.assertRaises(ValueError) as caught:
                structured.detect(f'<?xml version="1.0"?><Document xmlns="urn:x"><{root}/></Document>')
            self.assertIn(words, str(caught.exception))
            self.assertIn("camt.053 (XML) and SWIFT MT940", str(caught.exception))


class MatcherTest(unittest.TestCase):
    LOANS = [loan("L1", "Anna", "Weber", "2026-11-05", 250.0, "000000001"),
             loan("L2", "Jonas", "Fischer", "2026-11-06", 180.25, "000000002"),
             loan("L3", "Lena", "Hoffmann", "2026-11-06", 75.0, "000000003")]

    def test_a_clear_camt_line_posts_and_a_noref_line_never_does(self):
        _, lines, _ = read("camt053_sepa.xml")
        decisions = decide(lines, self.LOANS, "camt053")
        self.assertEqual([(d["outcome"], d["loan_ref"]) for d in decisions], [("post", "L1"), ("post", "L2"), ("queue", "")])
        self.assertIn("no bank reference", decisions[2]["reason"])  # name and amount fit L3, still a person decides
        self.assertEqual(decisions[2]["candidates"], ["L3"])

    def test_two_loans_that_fit_are_queued_as_for_a_csv(self):
        _, lines, _ = read("camt053_sepa.xml")
        twins = self.LOANS + [loan("L4", "Anna", "Weber", "2026-11-04", 250.0, "000000004")]
        first = decide(lines, twins, "camt053")[0]
        self.assertEqual((first["outcome"], first["candidates"]), ("queue", ["L1", "L4"]))

    def test_mt940_names_post_and_free_text_is_queued(self):
        loans = [loan("L1", "Anna", "Weber", "2026-11-18", 250.0), loan("L2", "Jonas", "Fischer", "2026-11-17", 180.25)]
        _, lines, _ = read("mt940_german.sta")
        self.assertEqual([d["outcome"] for d in decide(lines, loans, "mt940")], ["post", "post", "queue"])
        _, lines, _ = read("mt940_multi.txt")
        decisions = decide(lines, [loan("L5", "Oliver", "Bennett", "2026-11-10", 312.40)], "mt940")
        self.assertEqual(decisions[0]["outcome"], "queue")
        self.assertIn("no payer name", decisions[0]["reason"])


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)

    def run_import(self, name, env_text):
        (self.dir / ".env").write_text(env_text)
        out = self.dir / "bank_statement.csv"
        done = subprocess.run([sys.executable, "-m", "statement.import_bank", str(FIXTURES / name), "--bank", "hdfc",
                               "--out", str(out), "--env", str(self.dir / ".env")],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)
        return done, out

    def test_each_fixture_into_the_standard_statement(self):
        envs = {EUR: "CURRENCY=EUR\nCHANNELS=SEPA,SEPA INSTANT,SCT,SDD\n",
                GBP: "CURRENCY=GBP\nCHANNELS=FASTER PAYMENTS,BACS,CHAPS,DIRECT DEBIT\n"}
        for name, settings in SETTINGS.items():
            done, out = self.run_import(name, envs[settings])
            self.assertEqual(done.returncode, 0, done.stderr)
            fmt, lines, counts = read(name)
            with out.open(newline="", encoding="utf-8") as f:
                self.assertEqual(list(csv.DictReader(f)), lines, name)
            self.assertEqual(out.read_text(encoding="utf-8").splitlines()[0], "txn_id,date,amount,narration")
            self.assertEqual(json.loads((self.dir / "statement.json").read_text())["bank"], fmt)  # not --bank hdfc
            self.assertIn(f"entries read: {counts['rows']} credits kept: {counts['credits']} debits skipped: "
                          f"{counts['debits']} reversals skipped: {counts['reversals']} entries without a reference: "
                          f"{len(counts.get('no_reference', []))}", done.stdout)

    def test_a_refusal_writes_nothing(self):
        for name in ("camt053_doctype_attack.xml", "camt053_sepa.xml"):  # the second: EUR file, INR settings
            done, out = self.run_import(name, "")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("REFUSED, nothing written", done.stderr)
            self.assertFalse(out.exists())


class PageTest(Page):
    def setUp(self):
        super().setUp()
        self.env.write_text(ENV + "CURRENCY=EUR\r\nCHANNELS=SEPA\r\n", newline="")

    def upload(self, name, body=None):
        return self.post("/api/upload", {"name": name, "text": body if body is not None else text(name)})

    def test_each_type_previews_and_imports_without_columns_or_ai(self):
        for name, fmt in (("camt053_sepa.xml", "camt053"), ("mt940_german.sta", "mt940")):
            self.assertIn("MT940" if fmt == "mt940" else "camt.053", self.upload(name)["done"])
            read = self.post("/api/read", {"ai": True})
            self.assertEqual((read["format"], read["counts"]["credits"], read["counts"]["reversals"]), (fmt, 3, 1))
            self.assertEqual(read["sample"][0]["payer"], "Anna Weber")
            done = self.post("/api/import", {"map": None, "bank": "hdfc"})
            self.assertEqual((done["bank"], done["done"]["no_reference"]), (fmt, [5]))
            with (self.data / "bank_statement.csv").open(newline="", encoding="utf-8") as f:
                self.assertEqual(list(csv.DictReader(f)), structured.read(text(name), EUR)[1])
            self.assertIn(f"BANK={fmt}", self.env.read_text())
            self.assertEqual(self.get("/api/run")["bank"], fmt)
        self.assertEqual(self.opener.requests, [])

    def test_a_gbp_file_with_eur_settings_is_refused_and_nothing_is_written(self):
        before = (self.data / "bank_statement.csv").read_bytes()
        self.upload("camt053_uk.xml")
        self.assertIn("CURRENCY is EUR", self.post("/api/read", {})["problem"])
        self.assertIn("Nothing was imported", self.post("/api/import", {"bank": "camt053"})["problem"])
        self.assertEqual((self.data / "bank_statement.csv").read_bytes(), before)

    def test_an_attack_or_a_camt054_is_refused_at_upload(self):
        self.assertIn("DOCTYPE or ENTITY", self.upload("camt053_doctype_attack.xml")["problem"])
        self.assertIn("camt.054", self.upload("n.xml", "<Document><BkToCstmrDbtCdtNtfctn/></Document>")["problem"])
        self.assertFalse((self.data / "bank_export.csv").exists())


if __name__ == "__main__":
    unittest.main()
