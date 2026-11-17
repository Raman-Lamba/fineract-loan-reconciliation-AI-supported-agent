"""Stage A of the global rollout: the locale settings (currency, partner share, date order, number format,
channels), each in its own corner and then through the page. Nothing here touches data/, .env or a real Fineract.
"""
import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import locale_settings as loc  # noqa: E402
from ai import layout as ai_layout  # noqa: E402
from posting.post import channel, post_all, split  # noqa: E402
from review.report import totals as report_totals  # noqa: E402
from statement.generate import generate, hdfc_export  # noqa: E402
from statement.import_bank import convert  # noqa: E402
from statement.parse import infer_number_format, parse_amount, with_order  # noqa: E402
from tests.test_posting import PAYMENT_TYPES, FakeFineract, decision  # noqa: E402
from tests.test_ui import DemoData  # noqa: E402
from tests.test_ui_connect import Page  # noqa: E402
from ui import serve  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HEADER = "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance"


def settings(**values):
    """Settings as the .env lines would make them: India's defaults unless a line says otherwise."""
    return loc.load({name: str(value) for name, value in values.items()})


def hdfc(*rows):
    return io.StringIO("\n".join([HEADER, *rows]) + "\n", newline="")


def credit(day, amount="100.00", ref="500000000001"):
    return f"{day},UPI-A B-a@ybl,{ref},{day},,{amount},0"


def imported(rows, **values):
    lines, _ = convert(hdfc(*rows), "hdfc", settings=settings(**values))
    return lines


def refusal(test, rows, *words, **values):
    with test.assertRaises(ValueError) as caught:
        convert(hdfc(*rows), "hdfc", settings=settings(**values))
    for word in words:
        test.assertIn(word, str(caught.exception))
    return str(caught.exception)


def sbi_map(date_format="%d/%m/%Y", style="two_columns"):
    return {"headers": ["Date", "Details", "Ref", "Debit", "Credit"], "date": 0, "date_format": date_format,
            "narration": 1, "reference": 2, "style": style, "credit": 4, "debit": 3, "amount": -1, "type": -1}


def mapped(*rows, cmap=None, **values):
    text = "\n".join(["Date,Details,Ref,Debit,Credit", *rows]) + "\n"
    return convert(io.StringIO(text, newline=""), cmap=cmap or sbi_map(), settings=settings(**values))[0]


class DateOrderTest(unittest.TestCase):
    def test_each_explicit_order(self):
        self.assertEqual(imported([credit("05/09/26")], DATE_ORDER="day-first")[0]["date"], "2026-09-05")
        self.assertEqual(imported([credit("05/09/26")], DATE_ORDER="month-first")[0]["date"], "2026-05-09")
        self.assertEqual(imported([credit("12/31/26")], DATE_ORDER="month-first")[0]["date"], "2026-12-31")
        refusal(self, [credit("12/31/26")], "row 2", "date", DATE_ORDER="day-first")

    def test_the_setting_decides_the_order_of_a_numeric_format(self):
        self.assertEqual(with_order("%d/%m/%Y", "month-first"), "%m/%d/%Y")
        self.assertEqual(with_order("%m/%d/%y", "day-first"), "%d/%m/%y")
        self.assertEqual(with_order("%Y-%m-%d", "month-first"), "%Y-%m-%d")  # year first has no other order
        self.assertEqual(with_order("%d %b %Y", "month-first"), "%d %b %Y")  # a month name has no other order
        cell = "09/05/2026"
        self.assertEqual(mapped(f"{cell},x,1,,10.00", cmap=sbi_map("%m/%d/%Y"), DATE_ORDER="month-first")[0]["date"], "2026-09-05")
        self.assertEqual(mapped(f"{cell},x,1,,10.00", cmap=sbi_map("%m/%d/%Y"), DATE_ORDER="day-first")[0]["date"], "2026-05-09")

    def test_the_other_date_formats(self):
        for fmt, cell in (("%Y-%m-%d", "2026-09-05"), ("%Y/%m/%d", "2026/09/05"), ("%d.%m.%y", "05.09.26"),
                          ("%m-%d-%Y", "09-05-2026"), ("%b %d, %Y", '"Sep 05, 2026"'), ("%d %B %Y", "05 September 2026"),
                          ("%d-%b-%Y", "05-Sep-2026")):
            self.assertIn(fmt, ai_layout.DATE_FORMATS)
            order = "month-first" if fmt.startswith("%m") else "day-first"
            self.assertEqual(mapped(f"{cell},x,1,,10.00", cmap=sbi_map(fmt), DATE_ORDER=order)[0]["date"], "2026-09-05", fmt)

    def test_auto_from_a_date_with_a_first_part_above_12(self):
        lines = imported([credit("05/09/26", ref="500000000001"), credit("25/09/26", ref="500000000002")], DATE_ORDER="auto")
        self.assertEqual([line["date"] for line in lines], ["2026-09-05", "2026-09-25"])

    def test_auto_from_a_date_with_a_second_part_above_12(self):
        lines = imported([credit("05/09/26", ref="500000000001"), credit("09/25/26", ref="500000000002")], DATE_ORDER="auto")
        self.assertEqual([line["date"] for line in lines], ["2026-05-09", "2026-09-25"])

    def test_auto_reads_the_whole_file_debits_included(self):
        debit = "25/09/26,CHQ,77,25/09/26,10.00,,0"
        lines = imported([credit("05/09/26"), debit], DATE_ORDER="auto")
        self.assertEqual(lines[0]["date"], "2026-09-05")

    def test_auto_refuses_when_every_date_is_ambiguous(self):
        message = refusal(self, [credit("05/09/26", ref="500000000001"), credit("06/07/26", ref="500000000002")],
                          "day-first or month-first", "Statement tab", "row 2", DATE_ORDER="auto")
        self.assertIn("does not guess", message)

    def test_auto_refuses_a_file_that_uses_both_orders(self):
        refusal(self, [credit("25/09/26", ref="500000000001"), credit("09/25/26", ref="500000000002")],
                "both ways", DATE_ORDER="auto")

    def test_auto_needs_no_answer_when_the_dates_are_not_ambiguous(self):
        lines = mapped("2026-09-05,x,1,,10.00", cmap=sbi_map("%Y-%m-%d"), DATE_ORDER="auto")
        self.assertEqual(lines[0]["date"], "2026-09-05")

    def test_india_keeps_day_first_and_the_old_hdfc_dates(self):
        self.assertEqual(imported([credit("05/09/26"), credit("05/09/2026", ref="500000000002")])[1]["date"], "2026-09-05")
        refusal(self, [credit("12/31/26")], "date")  # India: day first, as before


class NumberFormatTest(unittest.TestCase):
    def parse(self, cell, fmt="1,234.56", digits=2, indian=False, suffix=False):
        return parse_amount(cell, fmt, digits, indian, 7, "Amount", suffix)

    def test_each_format(self):
        for fmt, cell in (("1,234.56", "1,234,567.89"), ("1.234,56", "1.234.567,89"), ("1 234,56", "1 234 567,89"),
                          ("1 234,56", "1\u00a0234\u00a0567,89"), ("1 234,56", "1\u202f234\u202f567,89"),
                          ("1'234.56", "1'234'567.89")):
            self.assertEqual(self.parse(cell, fmt), Decimal("1234567.89"), (fmt, cell))
        self.assertEqual(self.parse("8450", "1.234,56"), Decimal("8450"))
        self.assertEqual(self.parse("0,5", "1.234,56"), Decimal("0.5"))

    def test_indian_grouping_only_for_india(self):
        self.assertEqual(self.parse("1,23,456.78", indian=True), Decimal("123456.78"))
        with self.assertRaises(ValueError):
            self.parse("1,23,456.78", indian=False)
        self.assertEqual(self.parse("1,234,567.00", indian=True), Decimal("1234567.00"))

    def test_a_mark_of_another_format_is_refused_with_its_row_and_column(self):
        for cell, fmt in (("1.234,56", "1,234.56"), ("1,234.56", "1.234,56"), ("1,234.56", "1 234,56"),
                          ("12,34.56", "1,234.56"), ("8450.001", "1,234.56"), ("1.2.3", "1.234,56")):
            with self.assertRaises(ValueError) as caught:
                self.parse(cell, fmt)
            self.assertIn("row 7", str(caught.exception))
            self.assertIn("'Amount'", str(caught.exception))

    def test_the_currency_sets_the_decimals(self):
        self.assertEqual(self.parse("12.345", digits=3), Decimal("12.345"))
        with self.assertRaises(ValueError):
            self.parse("12.3456", digits=3)
        with self.assertRaises(ValueError):
            self.parse("1500.50", digits=0)
        self.assertEqual(self.parse("1,500", digits=0), Decimal("1500"))

    def test_negatives_and_suffixes(self):
        self.assertEqual(self.parse("(1,234.56)"), Decimal("-1234.56"))
        self.assertEqual(self.parse("-1.234,56", "1.234,56"), Decimal("-1234.56"))
        self.assertEqual(self.parse("\u22121,234.56"), Decimal("-1234.56"))
        self.assertEqual(self.parse("1,234.56-"), Decimal("-1234.56"))
        self.assertEqual(self.parse("1,234.56 DR", suffix=True), Decimal("-1234.56"))
        self.assertEqual(self.parse("1.234,56 Cr", "1.234,56", suffix=True), Decimal("1234.56"))
        self.assertEqual(self.parse("1,234.56CR", suffix=True), Decimal("1234.56"))
        with self.assertRaises(ValueError):
            self.parse("1,234.56 Cr")  # a suffix only where the layout says amounts carry one
        self.assertEqual(self.parse(""), Decimal(0))

    def test_auto_infers_from_the_whole_column(self):
        for bodies, fmt in ((["8,450.00", "12.5"], "1,234.56"), (["1.234,56", "12,5"], "1.234,56"),
                            (["1 234,56", "5,25"], "1 234,56"), (["1\u202f234,56"], "1 234,56"),
                            (["1'234.56"], "1'234.56"), (["1.234,56", "1.234"], "1.234,56"),
                            (["1,234", "10.50"], "1,234.56"), (["1,234,567"], "1,234.56"), (["1.234.567"], "1.234,56"),
                            (["1,23,456.78"], "1,234.56"), (["8450", "100"], "1,234.56")):
            self.assertEqual(infer_number_format(bodies, 2), fmt, bodies)

    def test_auto_refuses_what_it_cannot_tell(self):
        for bodies in (["1.234"], ["1.234", "2.500"], ["1,234"]):
            with self.assertRaises(ValueError) as caught:
                infer_number_format(bodies, 2)
            self.assertIn("Statement tab", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            infer_number_format(["1,234.56", "1.234,56"], 2)
        self.assertIn("more than one style", str(caught.exception))

    def test_auto_through_an_import(self):
        rows = [credit("05/09/26", '"1.234,56"', "500000000001"), credit("06/09/26", '"250,00"', "500000000002")]
        lines = imported(rows, NUMBER_FORMAT="auto")
        self.assertEqual([line["amount"] for line in lines], ["1234.56", "250.00"])
        message = refusal(self, [credit("05/09/26", '"1.234"', "500000000001")], "number format", "Statement tab",
                          NUMBER_FORMAT="auto")
        self.assertIn("1.234", message)

    def test_an_import_in_each_format_and_a_wrong_one_names_the_row(self):
        self.assertEqual(imported([credit("05/09/26", '"1.234,56"')], NUMBER_FORMAT="1.234,56")[0]["amount"], "1234.56")
        self.assertEqual(imported([credit("05/09/26", '"1\'234.56"')], NUMBER_FORMAT="1'234.56")[0]["amount"], "1234.56")
        refusal(self, [credit("05/09/26", '"1.234,56"')], "row 2", "amount", NUMBER_FORMAT="1,234.56")

    def test_negative_debits_are_skipped_and_negative_credits_refused(self):
        for cell in ("(1,234.56)", "-1,234.56", "1,234.56-"):
            lines = mapped(f'05/09/2026,x,1,"{cell}",', f"06/09/2026,y,2,,10.00")
            self.assertEqual([line["amount"] for line in lines], ["10.00"], cell)
            with self.assertRaises(ValueError) as caught:
                mapped(f'05/09/2026,x,1,,"{cell}"')
            self.assertIn("row 2", str(caught.exception))

    def test_signed_and_suffix_styles_in_other_formats(self):
        cmap = {**sbi_map(style="signed"), "amount": 3, "credit": -1, "debit": -1}
        lines = mapped('05/09/2026,x,1,"1.234,56",', '06/09/2026,y,2,"(250,00)",', '07/09/2026,z,3,"100,00-",', cmap=cmap,
                       NUMBER_FORMAT="1.234,56")
        self.assertEqual([line["amount"] for line in lines], ["1234.56"])
        cmap = {**sbi_map(style="suffix"), "amount": 3, "credit": -1, "debit": -1}
        lines = mapped('05/09/2026,x,1,"1.234,56 Cr",', '06/09/2026,y,2,"20,00 Dr",', cmap=cmap, NUMBER_FORMAT="1.234,56")
        self.assertEqual([line["amount"] for line in lines], ["1234.56"])

    def test_minor_units_in_the_imported_amount(self):
        self.assertEqual(imported([credit("05/09/26", "12.345")], CURRENCY="KWD")[0]["amount"], "12.345")
        self.assertEqual(imported([credit("05/09/26", "1500")], CURRENCY="JPY")[0]["amount"], "1500")
        refusal(self, [credit("05/09/26", "12.345")], "amount")  # INR has 2 decimals
        refusal(self, [credit("05/09/26", "1500.00")], "amount", CURRENCY="JPY")

    def test_a_statement_currency_column_that_differs_is_refused(self):
        text = "Date,Details,Ref,Debit,Credit,Currency\n05/09/2026,x,1,,10.00,USD\n"
        cmap = {**sbi_map(), "headers": ["Date", "Details", "Ref", "Debit", "Credit", "Currency"]}
        with self.assertRaises(ValueError) as caught:
            convert(io.StringIO(text, newline=""), cmap=cmap, settings=settings())
        self.assertIn("row 2", str(caught.exception))
        self.assertIn("one currency per run", str(caught.exception))
        lines, _ = convert(io.StringIO(text, newline=""), cmap=cmap, settings=settings(CURRENCY="USD", COUNTRY="United States",
                                                                                      DATE_ORDER="day-first", NUMBER_FORMAT="1,234.56"))
        self.assertEqual(len(lines), 1)
        lines, _ = convert(io.StringIO(text.replace("USD", "inr"), newline=""), cmap=cmap, settings=settings())
        self.assertEqual(len(lines), 1)  # case is not a difference


class SplitTest(unittest.TestCase):
    def test_the_default_is_the_old_80_20_to_the_paisa(self):
        old = lambda total: (Decimal(total) * Decimal("0.80")).quantize(Decimal("0.01"), ROUND_HALF_UP)  # noqa: E731
        for cents in list(range(0, 3000)) + list(range(1_000_000, 1_000_400)):
            amount = f"{Decimal(cents) / 100:.2f}"
            self.assertEqual(split(amount), (old(amount), Decimal(amount) - old(amount)), amount)

    def test_other_shares_round_half_up_and_sum_exactly(self):
        for share, digits, amount, partner, lender in (
                ("62.5", 2, "0.20", "0.13", "0.07"),          # 0.125 -> up
                ("62.5", 2, "8450.00", "5281.25", "3168.75"),
                ("50", 2, "0.01", "0.01", "0.00"),             # 0.005 -> up
                ("0", 2, "8450.00", "0.00", "8450.00"),
                ("100", 2, "8450.00", "8450.00", "0.00"),
                ("50", 0, "5", "3", "2"),                      # 2.5 -> 3 (no decimals: JPY)
                ("80", 0, "1999", "1599", "400"),              # 1599.2 -> 1599
                ("62.5", 3, "0.003", "0.002", "0.001"),        # 0.001875 -> 0.002 (KWD)
                ("62.5", 3, "0.001", "0.001", "0.000"),        # 0.000625 -> 0.001
                ("33.333", 2, "100.00", "33.33", "66.67")):
            cfg = replace(loc.DEFAULT, partner_share=Decimal(share), digits=digits)
            got = split(amount, cfg)
            self.assertEqual(got, (Decimal(partner), Decimal(lender)), (share, digits, amount))
            self.assertEqual(sum(got), Decimal(amount))
            self.assertEqual(got[0].as_tuple().exponent, -digits)

    def test_always_sums_to_the_amount(self):
        for digits in (0, 2, 3):
            for share in ("0", "1", "12.5", "33.333", "62.5", "80", "99.99", "100"):
                cfg = replace(loc.DEFAULT, partner_share=Decimal(share), digits=digits)
                for n in range(1, 400, 7):
                    amount = f"{Decimal(n * 37) / Decimal(10) ** digits:.{digits}f}"
                    partner, lender = split(amount, cfg)
                    self.assertEqual(partner + lender, Decimal(amount))
                    self.assertGreaterEqual(lender, 0)

    def test_the_share_must_be_from_0_to_100(self):
        for value in ("0", "100", "62.5", "0.01", " 80 "):
            self.assertEqual(loc.load({"PARTNER_SHARE_PERCENT": value}).partner_share, Decimal(value))
        for value in ("-1", "100.01", "abc", "NaN", "Infinity", "8 0"):
            with self.assertRaises(ValueError, msg=value):
                loc.load({"PARTNER_SHARE_PERCENT": value})

    def test_the_ratio_text(self):
        self.assertEqual(loc.DEFAULT.ratio, "80:20")
        self.assertEqual(settings(PARTNER_SHARE_PERCENT="62.5").ratio, "62.5:37.5")
        self.assertEqual(settings(PARTNER_SHARE_PERCENT="100").ratio, "100:0")

    def test_posting_writes_the_configured_split_into_the_ledger(self):
        fake = FakeFineract()
        narrations = {"100000000001": "UPI/1/RAMESH KUM"}
        (audit, ledger), = post_all([decision()], narrations, {"L1": 7}, fake, PAYMENT_TYPES, set(),
                                    settings=settings(PARTNER_SHARE_PERCENT="62.5"))
        self.assertEqual((audit["action"], ledger["partner_share"], ledger["lender_share"]), ("posted", "5281.25", "3168.75"))

    def test_the_minor_unit_table_and_fineract(self):
        self.assertEqual([settings(CURRENCY=code).minor for code in ("INR", "EUR", "JPY", "KWD", "BHD", "OMR")], [2, 2, 0, 3, 3, 3])

        class Fc:
            def __init__(self, answer):
                self.answer = answer

            def execute_get(self, endpoint, params=None):
                return self.answer

        asked = {"selectedCurrencyOptions": [{"code": "JPY", "decimalPlaces": 0}, {"code": "XYZ", "digitsAfterDecimal": 4}]}
        self.assertEqual(loc.with_fineract_digits(settings(CURRENCY="XYZ"), Fc(asked)).minor, 4)
        self.assertEqual(loc.with_fineract_digits(settings(CURRENCY="KWD"), Fc(asked)).minor, 3)  # not listed: the table
        self.assertEqual(loc.with_fineract_digits(settings(CURRENCY="KWD"), Fc({"error": "x"})).minor, 3)
        self.assertEqual(loc.with_fineract_digits(settings(CURRENCY="JPY"), Fc(asked)).minor, 0)

    def test_report_totals_keep_the_currencys_decimals(self):
        ledger = [{"txn_id": "1", "date": "2026-09-05", "loan_ref": "L1", "amount": "1500", "partner_share": "1200",
                   "lender_share": "300"}]
        from review.report import by_loan_month
        self.assertEqual(report_totals(by_loan_month(ledger), ledger, 0)["amount"], "1500")


class ChannelTest(unittest.TestCase):
    CASES = {
        "India": [("UPI/1/A B/x@okaxis", "UPI"), ("NEFT-1-A B", "NEFT"), ("IMPS/P2A/1", "IMPS"), ("NACH/ACH DR/1", "NACH")],
        "United Kingdom": [("FASTER PAYMENTS RECEIPT J SMITH", "FASTER PAYMENTS"), ("faster   payments from j smith", "FASTER PAYMENTS"),
                           ("BACS CREDIT LOAN", "BACS"), ("CHAPS IN", "CHAPS"), ("DIRECT DEBIT REFUND", "DIRECT DEBIT")],
        "Eurozone (SEPA)": [("SEPA INSTANT CREDIT", "SEPA INSTANT"), ("SEPA CREDIT TRANSFER", "SEPA"), ("SCT INST", "SCT"),
                            ("SDD CORE", "SDD")],
        "United States": [("ACH CREDIT PAYROLL", "ACH"), ("WIRE IN", "WIRE"), ("ZELLE FROM J", "ZELLE"), ("CHECK 1001", "CHECK")],
        "Brazil": [("PIX RECEBIDO", "PIX"), ("TED 123", "TED"), ("DOC 9", "DOC"), ("BOLETO PAGO", "BOLETO")],
        "Kenya": [("MPESA RECEIPT QH1", "MPESA"), ("PESALINK IN", "PESALINK"), ("EFT CREDIT", "EFT")],
    }

    def test_each_preset_finds_its_channels(self):
        for country, cases in self.CASES.items():
            channels = loc.load({"COUNTRY": country}).channels
            for narration, expected in cases:
                self.assertEqual(channel(narration, channels), expected, (country, narration))

    def test_a_channel_is_a_whole_word(self):
        channels = ("UPI", "SEPA", "SEPA INSTANT")
        for narration in ("UPIDEMO", "SUPI", "SEPAX CREDIT", "XSEPA", "SEPAINSTANT", "NOTHING HERE"):
            self.assertEqual(channel(narration, channels), "", narration)
        self.assertEqual(channel("a SEPA. b UPI", channels), "SEPA")  # the first in the narration
        self.assertEqual(channel("anything", ()), "")
        self.assertEqual(channel("UPI/1/A B"), "UPI")  # India's, with no settings given

    def test_the_channel_becomes_the_payment_type_of_that_name_when_there_is_one(self):
        uk = settings(COUNTRY="United Kingdom")
        for types, expected in (({"Faster Payments": 9, "BACS": 3}, 9), ({"BACS": 3}, None)):
            fake = FakeFineract()
            narrations = {"100000000001": "FASTER PAYMENTS RECEIPT A B"}
            list(post_all([decision()], narrations, {"L1": 7}, fake, types, set(), settings=uk))
            self.assertEqual(fake.posts[0][1].get("paymentTypeId"), expected)

    def test_a_custom_list_is_used(self):
        cfg = settings(CHANNELS="mobile money, card ,mobile money")
        self.assertEqual(cfg.channels, ("MOBILE MONEY", "CARD"))
        self.assertEqual(channel("MOBILE   MONEY IN", cfg.channels), "MOBILE MONEY")


class PresetTest(unittest.TestCase):
    def test_a_preset_fills_every_setting(self):
        table = {"India": ("INR", "day-first", "1,234.56", ("UPI", "NEFT", "IMPS", "NACH"), "en-IN"),
                 "United Kingdom": ("GBP", "auto", "auto", ("FASTER PAYMENTS", "BACS", "CHAPS", "DIRECT DEBIT"), "en-GB"),
                 "Eurozone (SEPA)": ("EUR", "auto", "auto", ("SEPA", "SEPA INSTANT", "SCT", "SDD"), "en-IE"),
                 "United States": ("USD", "auto", "auto", ("ACH", "WIRE", "ZELLE", "CHECK"), "en-US"),
                 "Brazil": ("BRL", "auto", "auto", ("PIX", "TED", "DOC", "BOLETO"), "pt-BR"),
                 "Kenya": ("KES", "auto", "auto", ("MPESA", "PESALINK", "EFT"), "en-KE")}
        for country, (currency, order, fmt, channels, ui) in table.items():
            got = loc.load({"COUNTRY": country})
            self.assertEqual((got.currency, got.date_order, got.number_format, got.channels, got.ui_locale),
                             (currency, order, fmt, channels, ui), country)
            self.assertEqual(got.partner_share, Decimal(80))
        self.assertEqual(loc.load({}), loc.DEFAULT)

    def test_every_value_can_be_changed_after_choosing_a_preset(self):
        got = loc.load({"COUNTRY": "Kenya", "CURRENCY": "usd", "DATE_ORDER": "day-first", "NUMBER_FORMAT": "1 234,56",
                        "CHANNELS": "A, B", "PARTNER_SHARE_PERCENT": "55"})
        self.assertEqual((got.currency, got.date_order, got.number_format, got.channels, got.partner_share),
                         ("USD", "day-first", "1 234,56", ("A", "B"), Decimal(55)))
        self.assertEqual(loc.load({"NUMBER_FORMAT": "1\u202f234,56"}).number_format, "1 234,56")

    def test_other_has_no_currency_until_one_is_typed(self):
        with self.assertRaises(ValueError) as caught:
            loc.load({"COUNTRY": "Other"})
        self.assertIn("CURRENCY", str(caught.exception))
        self.assertEqual(loc.load({"COUNTRY": "Other", "CURRENCY": "CHF"}).currency, "CHF")

    def test_bad_values_are_refused_in_plain_words(self):
        for values in ({"COUNTRY": "Atlantis"}, {"CURRENCY": "RUPEE"}, {"CURRENCY": "I1R"}, {"DATE_ORDER": "sideways"},
                       {"NUMBER_FORMAT": "1;234"}):
            with self.assertRaises(ValueError, msg=values):
                loc.load(values)

    def test_the_unverified_presets_say_so(self):
        source = (ROOT / "locale_settings.py").read_text(encoding="utf-8")
        self.assertIn("UNVERIFIED", source)
        self.assertIn("Using it outside India", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("unverified", (ROOT / "README.md").read_text(encoding="utf-8").lower())


class ConnectionTest(DemoData):
    def check(self, cfg, **answers):
        return {r["check"]: r for r in serve.check_connection(self.fake(**answers), cfg)}

    def test_the_currency_checked_is_the_one_set(self):
        results = self.check(settings(COUNTRY="United Kingdom"))
        self.assertIs(results["GBP is an enabled currency"]["ok"], False)  # the sandbox has INR only
        self.assertIn("Pound Sterling (GBP)", results["GBP is an enabled currency"]["fix"])
        self.assertIn("Enabled: INR", results["GBP is an enabled currency"]["detail"])
        both = {"selectedCurrencyOptions": [{"code": "INR"}, {"code": "EUR", "decimalPlaces": 2}]}
        ok = self.check(settings(COUNTRY="Eurozone (SEPA)"), currencies=both)
        self.assertIs(ok["EUR is an enabled currency"]["ok"], True)

    def test_a_currency_with_no_preset_name_is_still_named_in_the_fix(self):
        results = self.check(settings(COUNTRY="Other", CURRENCY="CHF"))
        self.assertIn("add CHF", results["CHF is an enabled currency"]["fix"])

    def test_fineractss_decimals_are_compared_with_the_table(self):
        got = self.check(settings(), currencies={"selectedCurrencyOptions": [{"code": "INR", "decimalPlaces": 3}]})
        self.assertIn("Fineract keeps 3 decimals for INR", got["INR is an enabled currency"]["detail"])
        self.assertIs(got["INR is an enabled currency"]["ok"], True)

    def test_channels_found_and_not_found_are_listed_and_never_fail(self):
        eu = settings(COUNTRY="Eurozone (SEPA)")
        both = {"selectedCurrencyOptions": [{"code": "EUR"}]}
        results = self.check(eu, currencies=both, paymenttypes=[{"id": 1, "name": "SEPA"}, {"id": 2, "name": "Sct"}])
        row = results["Payment types SEPA, SEPA INSTANT, SCT, SDD exist"]
        self.assertIs(row["ok"], True)
        self.assertEqual(row["fix"], "")
        self.assertIn("Found as payment types: SEPA, SCT", row["detail"])
        self.assertIn("Not found: SEPA INSTANT, SDD", row["detail"])
        none = self.check(eu, currencies=both, paymenttypes=[])["Payment types SEPA, SEPA INSTANT, SCT, SDD exist"]
        self.assertIs(none["ok"], True)
        self.assertIn("Found as payment types: none", none["detail"])

    def test_a_loan_in_another_currency_is_reported_plainly(self):
        page = {"totalFilteredRecords": 2, "pageItems": [
            {"id": 1, "accountNo": "000000001", "externalId": "L9001", "currency": {"code": "INR"}},
            {"id": 2, "accountNo": "000000002", "externalId": "L9002", "currency": {"code": "USD"}}]}
        row = self.check(settings(), loans=page)["Loans exist"]
        self.assertIs(row["ok"], False)
        self.assertIn("1 of the 2 loans are in USD, but CURRENCY is INR", row["detail"])
        self.assertIn("nothing is converted", row["detail"])
        self.assertIn("one currency at a time", row["fix"].lower())

    def test_the_first_loans_currency_is_checked_too(self):
        loan = serve.DemoFineract(self.loans).execute_get("loans/1")
        got = self.check(settings(), **{"loans/1": {**loan, "currency": {"code": "USD"}}})
        row = got["A loan's schedule can be read"]
        self.assertIs(row["ok"], False)
        self.assertIn("is in USD, but CURRENCY is INR", row["detail"])
        fine = self.check(settings(), **{"loans/1": {**loan, "currency": {"code": "INR"}}})
        self.assertIs(fine["A loan's schedule can be read"]["ok"], True)


class PageTest(Page):
    def settings(self):
        return self.get("/api/settings")

    def test_a_preset_saved_from_the_form_comes_back_as_its_values(self):
        saved = self.post("/api/settings", {"values": {"COUNTRY": "Kenya"}})
        self.assertEqual(saved["done"], "Saved.")
        self.assertEqual(saved["values"]["CURRENCY"], "KES")  # a preset's fill-ins are what the page shows
        self.assertEqual(saved["locale"]["ui_locale"], "en-KE")
        self.assertEqual(saved["values"]["CHANNELS"], "MPESA,PESALINK,EFT")
        self.assertIn("Kenya", saved["locale"]["presets"])
        self.assertIn("COUNTRY=Kenya", self.env.read_text())

    def test_the_whole_form_round_trips_and_secrets_stay_out(self):
        first = self.post("/api/settings", {"values": {"COUNTRY": "Brazil", "CURRENCY": "brl", "PARTNER_SHARE_PERCENT": "62.5",
                                                      "DATE_ORDER": "day-first", "NUMBER_FORMAT": "1.234,56",
                                                      "CHANNELS": "pix, ted"}})
        self.assertEqual(first["values"]["CURRENCY"], "BRL")
        self.assertEqual(first["values"]["CHANNELS"], "PIX,TED")
        again = self.post("/api/settings", {"values": first["values"]})  # the form sends back what it was shown
        self.assertEqual(again["values"], first["values"])
        got = loc.from_env(self.env)
        self.assertEqual((got.country, got.currency, got.partner_share, got.date_order, got.number_format, got.channels),
                         ("Brazil", "BRL", Decimal("62.5"), "day-first", "1.234,56", ("PIX", "TED")))
        self.assertIn("MIFOSX_PASSWORD=", self.env.read_text())  # untouched; request() checks it is never echoed
        self.assertNotIn("MIFOSX_PASSWORD", again["values"])
        self.assertEqual(again["locale"]["ratio"], "62.5:37.5")

    def test_a_bad_value_is_refused_and_nothing_is_written(self):
        before = self.env.read_text()
        for values in ({"PARTNER_SHARE_PERCENT": "101"}, {"CURRENCY": "RUPEE"}, {"COUNTRY": "Atlantis"},
                       {"DATE_ORDER": "sideways"}, {"NUMBER_FORMAT": "1;234"}, {"COUNTRY": "Other", "CURRENCY": ""}):
            result = self.post("/api/settings", {"values": values})
            self.assertIn("problem", result, values)
            self.assertEqual(self.env.read_text(), before)

    def test_an_empty_box_goes_back_to_the_countrys_preset(self):
        self.post("/api/settings", {"values": {"COUNTRY": "Kenya", "CURRENCY": "USD"}})
        self.assertEqual(self.settings()["values"]["CURRENCY"], "USD")
        self.post("/api/settings", {"values": {"CURRENCY": ""}})
        self.assertNotIn("CURRENCY=", self.env.read_text())
        self.assertEqual(self.settings()["values"]["CURRENCY"], "KES")

    def test_defaults_show_india_and_a_bad_hand_edit_does_not_break_the_page(self):
        view = self.settings()
        self.assertEqual((view["values"]["COUNTRY"], view["values"]["CURRENCY"], view["values"]["PARTNER_SHARE_PERCENT"],
                          view["values"]["DATE_ORDER"], view["values"]["NUMBER_FORMAT"], view["values"]["CHANNELS"]),
                         ("India", "INR", "80", "day-first", "1,234.56", "UPI,NEFT,IMPS,NACH"))
        self.assertEqual(view["locale"]["problem"], "")
        self.env.write_text(self.env.read_text() + "\r\nPARTNER_SHARE_PERCENT=999\r\n", newline="")
        self.assertIn("not usable", self.settings()["locale"]["problem"])
        self.assertEqual(self.request("GET", "/")[0], 200)

    def test_the_page_is_given_the_money_format_for_its_locale(self):
        self.post("/api/settings", {"values": {"COUNTRY": "United Kingdom"}})
        _, page = self.request("GET", "/")
        config = json.loads(page.split("const CONFIG = ", 1)[1].split(";\nconst $", 1)[0])
        self.assertEqual(config["money"], {"currency": "GBP", "locale": "en-GB", "ratio": "80:20", "minor": 2})

    def test_an_ambiguous_statement_is_refused_on_the_statement_tab_until_the_order_is_chosen(self):
        rows = [HEADER, "05/09/26,UPI-A B-a@ybl,500000000001,05/09/26,,100.00,0",
                "06/07/26,UPI-C D-c@ybl,500000000002,06/07/26,,200.00,0"]
        self.post("/api/upload", {"name": "x.csv", "text": "\n".join(rows) + "\n"})
        self.post("/api/settings", {"values": {"DATE_ORDER": "auto"}})
        read = self.post("/api/read", {})
        self.assertIn("day-first or month-first", read["problem"])
        self.assertIn("Statement tab", read["problem"])
        self.post("/api/settings", {"values": {"DATE_ORDER": "month-first"}})
        read = self.post("/api/read", {})
        self.assertEqual([line["date"] for line in read["sample"]], ["2026-05-09", "2026-06-07"])
        done = self.post("/api/import", {"map": read["map"], "bank": "hdfc"})
        self.assertEqual(done["done"]["credits"], 2)

    def test_posting_logs_the_configured_split_and_currency(self):
        self.post("/api/settings", {"values": {"PARTNER_SHARE_PERCENT": "62.5", "CURRENCY": "INR"}})
        self.post("/api/loans")
        self.post("/api/match", {"bank": "synthetic"})
        pending = self.get("/api/run")["match"]["pending"]
        result = self.post("/api/post", {"count": pending["count"], "total": pending["total"]})
        self.assertIn("split each 62.5:37.5", result["log"][0])
        self.assertIn("Posted in all (INR)", result["log"][-2])
        with (self.data / "ledger.csv").open(newline="") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(Decimal(row["partner_share"]) + Decimal(row["lender_share"]), Decimal(row["amount"]))
        self.assertEqual(Decimal(row["partner_share"]), (Decimal(row["amount"]) * Decimal("0.625")).quantize(Decimal("0.01"), ROUND_HALF_UP))


class DefaultsTest(unittest.TestCase):
    def test_default_settings_import_the_generated_statement_exactly(self):
        loans = [{"loan_ref": f"L{i}", "loan_id": i, "account_no": f"{i:09d}", "client_id": i, "firstname": f"F{i}",
                  "lastname": "Kumar", "instalments": [{"due": "2026-09-05", "amount": 1000.0 + i}]} for i in range(1, 30)]
        lines, _ = generate(loans)
        out = io.StringIO(newline="")
        csv.writer(out).writerows(hdfc_export(lines))
        explicit = settings(COUNTRY="India", CURRENCY="INR", PARTNER_SHARE_PERCENT="80", DATE_ORDER="day-first",
                            NUMBER_FORMAT="1,234.56", CHANNELS="UPI,NEFT,IMPS,NACH")
        self.assertEqual(explicit, loc.DEFAULT)
        got, _ = convert(io.StringIO(out.getvalue(), newline=""), "hdfc")
        self.assertEqual(got, convert(io.StringIO(out.getvalue(), newline=""), "hdfc", settings=explicit)[0])
        self.assertEqual([(g["txn_id"], g["date"], g["amount"]) for g in got],
                         [(g["txn_id"], g["date"], g["amount"]) for g in lines])

    def test_the_old_amount_forms_still_read_and_the_old_refusals_still_refuse(self):
        for cell, expected in (("8,450.00", "8450.00"), ("1,21,940.50", "121940.50"), ("121,940", "121940.00"), ("8450", "8450.00")):
            self.assertEqual(imported([credit("05/09/26", f'"{cell}"')])[0]["amount"], expected, cell)
        for cell in ('"84,50.00"', "8450.001", "8.450,00", "-8450.00", "8450.00 Cr", "Rs 100", "1e3"):
            refusal(self, [credit("05/09/26", cell)], "row 2", "amount")

    def test_no_rupee_sign_or_indian_locale_outside_the_india_preset(self):
        rupee, india = chr(0x20B9), "en-IN"
        for path in [*ROOT.glob("*.py"), *(p for d in ("ai", "matcher", "posting", "review", "seed", "statement", "ui", "scripts")
                                           for p in (ROOT / d).glob("*.*") if p.suffix in (".py", ".html", ".ps1"))]:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(rupee, text, path)
            if path.name != "locale_settings.py":
                self.assertNotIn(india, text, path)
        page = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Intl.NumberFormat", page)
        self.assertNotIn("80:20", page)

    def test_the_old_constants_are_gone(self):
        import posting.post as post
        self.assertFalse(hasattr(post, "PARTNER_SHARE"))
        self.assertNotIn("80:20", (ROOT / "ui" / "serve.py").read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class BrowserMoneyTest(unittest.TestCase):
    """The page's own money() function, run in node (its Intl is the browser's): symbol and grouping follow the locale."""

    def run_money(self, currency, locale_name, minor=2, amount="1234567.5"):
        page = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        code = page.split("let MONEY = CONFIG.money", 1)[1].split("function count", 1)[0]
        script = ("const CONFIG = {money: %s};\nlet MONEY = CONFIG.money" % json.dumps(
            {"currency": currency, "locale": locale_name, "ratio": "80:20", "minor": minor}) + code +
                  "\nprocess.stdout.write(money(%s));" % json.dumps(amount))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "money.js"
            path.write_text(script, encoding="utf-8")
            return subprocess.run(["node", str(path)], capture_output=True, text=True, encoding="utf-8", check=True).stdout

    def test_each_locale(self):
        india = self.run_money("INR", "en-IN")
        self.assertEqual(india, chr(0x20B9) + "12,34,567.50")  # lakh grouping comes from the locale
        self.assertEqual(self.run_money("EUR", "de-DE"), "1.234.567,50\u00a0\u20ac")
        self.assertEqual(self.run_money("GBP", "en-GB"), "\u00a31,234,567.50")
        self.assertEqual(self.run_money("USD", "en-US"), "$1,234,567.50")
        self.assertIn("1.234.567,50", self.run_money("BRL", "pt-BR"))
        self.assertEqual(self.run_money("JPY", "en-US", 0, "1500"), "\u00a51,500")

    def test_a_locale_or_currency_the_browser_rejects_falls_back_to_the_iso_code(self):
        self.assertEqual(self.run_money("INR", "xx-not a locale", 2, "8450"), "INR 8450.00")
        self.assertEqual(self.run_money("1NR", "en-IN", 2, "8450"), "1NR 8450.00")


if __name__ == "__main__":
    unittest.main()
