"""No-partner mode (CO_LENDING=no): a lender that does not co-lend. The split, the partner column and the partner
report disappear; matching, posting, review and audit are untouched. Nothing here touches data/, .env or a real Fineract.
"""
import csv
import io
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import locale_settings as loc  # noqa: E402
import posting.run as files  # noqa: E402
from posting.post import post_all, split  # noqa: E402
from posting.verify import check, totals  # noqa: E402
from review import report  # noqa: E402
from tests.test_posting import PAYMENT_TYPES, FakeFineract, decision  # noqa: E402
from tests.test_ui_connect import Page  # noqa: E402

NO = loc.load({"CO_LENDING": "no"})
LEDGER = [
    {"txn_id": "1", "date": "2026-09-05", "loan_ref": "L1", "fineract_txn_id": "9", "amount": "8450.00",
     "partner_share": "0.00", "lender_share": "8450.00"},
    {"txn_id": "2", "date": "2026-09-28", "loan_ref": "L1", "fineract_txn_id": "10", "amount": "0.03",
     "partner_share": "0.00", "lender_share": "0.03"},
    {"txn_id": "3", "date": "2026-10-05", "loan_ref": "L2", "fineract_txn_id": "11", "amount": "1000.33",
     "partner_share": "0.00", "lender_share": "1000.33"}]


class SettingTest(unittest.TestCase):
    def test_the_default_is_co_lending_and_is_the_old_default(self):
        self.assertEqual(loc.load({}), loc.DEFAULT)
        self.assertTrue(loc.DEFAULT.co_lending)
        self.assertEqual(loc.load({"CO_LENDING": "yes", "PARTNER_SHARE_PERCENT": "62.5"}).partner_share, Decimal("62.5"))

    def test_no_means_a_zero_partner_share_whatever_the_percentage_box_says(self):
        for text in ("no", "No", " NO "):
            got = loc.load({"CO_LENDING": text, "PARTNER_SHARE_PERCENT": "62.5"})
            self.assertEqual((got.co_lending, got.partner_share), (False, Decimal(0)))

    def test_anything_but_yes_or_no_is_refused_in_plain_words(self):
        with self.assertRaisesRegex(ValueError, "CO_LENDING must be yes or no"):
            loc.load({"CO_LENDING": "maybe"})

    def test_the_form_view_shows_the_mode(self):
        self.assertEqual(loc.view(loc.DEFAULT)["values"]["CO_LENDING"], "yes")
        self.assertEqual(loc.view(NO)["values"]["CO_LENDING"], "no")


class SplitTest(unittest.TestCase):
    def test_the_lender_gets_everything_and_the_two_add_up(self):
        for currency, amount in (("INR", "8450.00"), ("INR", "0.03"), ("JPY", "1234"), ("KWD", "12.345"), ("USD", "1000.33")):
            settings = loc.load({"CO_LENDING": "no", "CURRENCY": currency, "PARTNER_SHARE_PERCENT": "80"})
            partner, lender = split(amount, settings)
            self.assertEqual((partner, lender), (Decimal(0), Decimal(amount)), (currency, amount))
            self.assertEqual(partner + lender, Decimal(amount))
            self.assertEqual(partner.as_tuple().exponent, -settings.minor)  # 0.00, 0 or 0.000: the currency's own places

    def test_with_a_partner_nothing_changed(self):
        self.assertEqual(split("8450.00"), (Decimal("6760.00"), Decimal("1690.00")))
        self.assertEqual(split("8450.00", loc.load({"CO_LENDING": "yes"})), (Decimal("6760.00"), Decimal("1690.00")))


class LedgerTest(unittest.TestCase):
    def rows(self, settings):
        narrations = {"100000000001": "UPI/000001/RAMESH KUM/ramesh@okaxis/emi sep"}
        return list(post_all([decision()], narrations, {"L1": 7}, FakeFineract(), PAYMENT_TYPES, set(), settings=settings))

    def test_the_columns_stay_and_the_partner_share_is_zero(self):
        (audit, ledger), = self.rows(NO)
        self.assertEqual(list(ledger), files.LEDGER)
        self.assertEqual((ledger["amount"], ledger["partner_share"], ledger["lender_share"]), ("8450.00", "0.00", "8450.00"))
        self.assertEqual((audit["action"], audit["approver"]), ("posted", "auto (matcher rules)"))

    def test_the_default_ledger_row_is_the_old_one(self):
        (_, ledger), = self.rows(loc.DEFAULT)
        self.assertEqual((ledger["partner_share"], ledger["lender_share"]), ("6760.00", "1690.00"))

    def test_verify_still_checks_a_no_partner_ledger(self):
        in_fineract = {row["txn_id"]: row["amount"] for row in LEDGER}
        self.assertEqual(check(LEDGER, in_fineract), [])
        self.assertEqual(totals(LEDGER)["lender_share"], totals(LEDGER)["amount"])
        broken = [{**LEDGER[0], "lender_share": "8449.00"}]
        self.assertIn("shares do not add up", check(broken, {"1": "8450.00"})[0])

    def test_the_duplicate_guard_is_unchanged(self):
        self.assertEqual(list(post_all([decision()], {"100000000001": "x"}, {"L1": 7}, FakeFineract(), PAYMENT_TYPES,
                                       {"100000000001"}, settings=NO)), [])


class WordingTest(unittest.TestCase):
    def test_one_helper_gives_both_wordings(self):
        co, solo = loc.words(loc.DEFAULT), loc.words(NO)
        self.assertEqual(co["report_file"], "partner_report.csv")
        self.assertEqual(solo["report_file"], "repayments_report.csv")
        self.assertEqual(co["recorded"], "split each 80:20 in the ledger")
        self.assertEqual(co["lender_tile"], "Lender's share")
        self.assertEqual(solo["lender_tile"], "Collected")
        self.assertEqual(co.keys(), solo.keys())
        for key, text in solo.items():
            self.assertNotIn("partner", text.replace("no partner", "").replace("partner bank)", "").lower(), key)

    def test_a_different_percentage_shows_in_the_co_lending_words(self):
        self.assertIn("62.5:37.5", loc.words(loc.load({"PARTNER_SHARE_PERCENT": "62.5"}))["recorded"])


class ReportTest(unittest.TestCase):
    def write(self, ledger, settings, folder):
        with mock.patch.object(files, "DATA", folder), mock.patch.object(files, "read", return_value=ledger), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            name = report.main(settings)
        return name, (folder / name).read_text(), out.getvalue()

    def test_no_partner_writes_the_plain_report_under_its_own_name(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            name, text, console = self.write(LEDGER, NO, Path(folder))
            self.assertEqual(name, "repayments_report.csv")
            self.assertFalse((Path(folder) / "partner_report.csv").exists())
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(list(rows[0]), ["month", "loan_ref", "payments", "amount"])
        self.assertEqual([(r["month"], r["loan_ref"], r["payments"], r["amount"]) for r in rows],
                         [("2026-09", "L1", "2", "8450.03"), ("2026-10", "L2", "1", "1000.33"), ("TOTAL", "", "3", "9450.36")])
        self.assertNotIn("partner", text.lower())
        self.assertIn("repayments report: 2 rows", console)
        self.assertNotIn("partner", console.lower())
        self.assertNotIn("lender share", console)
        self.assertIn("totals equal the ledger's: yes", console)

    def test_co_lending_keeps_the_old_name_and_columns(self):
        import tempfile
        ledger = [{**r, "partner_share": str(split(r["amount"])[0]), "lender_share": str(split(r["amount"])[1])} for r in LEDGER]
        with tempfile.TemporaryDirectory() as folder:
            name, text, console = self.write(ledger, loc.DEFAULT, Path(folder))
            self.assertFalse((Path(folder) / "repayments_report.csv").exists())
        self.assertEqual(name, "partner_report.csv")
        self.assertEqual(text.splitlines()[0], "month,loan_ref,payments,amount,partner_share,lender_share")
        self.assertEqual(text.splitlines()[-1], "TOTAL,,3,9450.36,7560.28,1890.08")
        self.assertIn("partner report: 2 rows", console)
        self.assertIn("partner share: 7,560.28", console)

    def test_the_totals_check_still_stops_a_report_that_disagrees_with_the_ledger(self):
        rows = report.by_loan_month(LEDGER)
        with self.assertRaises(SystemExit):
            report.totals(rows[:-1], LEDGER)
        self.assertEqual(report.totals(rows, LEDGER)["amount"], "9450.36")


class ConnectTest(Page):
    LEDGER_ROW = {"txn_id": "1", "date": "2026-09-05", "loan_ref": "L9001", "fineract_txn_id": "9", "amount": "8450.00",
                  "partner_share": "6760.00", "lender_share": "1690.00"}

    def put_ledger(self, rows=1):
        write = files.appender("ledger.csv", files.LEDGER)
        for n in range(rows):
            write({**self.LEDGER_ROW, "txn_id": str(n + 1)})
        write.close()
        return (self.data / "ledger.csv").read_bytes()

    def audit(self):
        path = self.data / "audit.csv"
        if not path.exists():
            return []
        with path.open(newline="") as f:
            return list(csv.DictReader(f))

    def save(self, mode, **more):
        return self.post("/api/settings", {"values": {"CO_LENDING": mode}, **more})

    def test_the_form_starts_on_yes_and_carries_the_wording_for_it(self):
        view = self.get("/api/settings")
        self.assertEqual(view["values"]["CO_LENDING"], "yes")
        self.assertEqual(view["locale"]["words"]["report_file"], "partner_report.csv")
        self.assertEqual(view["locale"]["mode_warning"], "")  # no payments recorded: nothing to warn about

    def test_the_page_has_the_two_choice_control_and_hides_partner_things_by_data_attributes(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Do you share your loans with a partner bank?", page)
        self.assertIn('name="CO_LENDING" value="yes"', page)
        self.assertIn('name="CO_LENDING" value="no"', page)
        self.assertIn('id="c-share-label"', page)  # the percentage box, hidden by the page when No is picked
        self.assertIn("data-co", page)
        self.assertIn('"report_file": "partner_report.csv"', page)

    def test_saving_no_with_an_empty_ledger_needs_no_confirmation_and_is_audited(self):
        got = self.save("no", approver="Asha")
        self.assertEqual(got["done"], "Saved.")
        self.assertEqual(got["values"]["CO_LENDING"], "no")
        self.assertEqual(got["locale"]["words"]["report_file"], "repayments_report.csv")
        self.assertIn("CO_LENDING=no", self.env.read_text())
        row, = self.audit()
        self.assertEqual((row["action"], row["approver"], row["reason"], row["txn_id"]),
                         ("mode_changed", "Asha", "co-lending changed from yes to no", ""))
        self.assertRegex(row["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")

    def test_the_audit_names_nobody_when_the_name_box_is_empty(self):
        self.save("no")
        self.assertEqual(self.audit()[0]["approver"], "(no name given)")

    def test_a_ledger_with_rows_warns_and_refuses_without_the_confirm(self):
        ledger = self.put_ledger(2)
        before = self.env.read_bytes()
        got = self.save("no", approver="Asha")
        self.assertIn("2 payments are already recorded as split 80:20; this change affects only new payments", got["problem"])
        self.assertTrue(got["needs_confirm"])
        self.assertEqual(self.env.read_bytes(), before)
        self.assertEqual(self.audit(), [])
        self.assertEqual((self.data / "ledger.csv").read_bytes(), ledger)
        self.assertIn("2 payments are already recorded", self.get("/api/settings")["locale"]["mode_warning"])
        for bad in ("yes", 1, "true"):  # only a real true counts
            self.assertIn("problem", self.save("no", confirm_mode_change=bad))

    def test_with_the_confirm_it_saves_audits_and_never_rewrites_the_ledger(self):
        ledger = self.put_ledger(1)
        got = self.save("no", approver="Asha", confirm_mode_change=True)
        self.assertEqual(got["done"], "Saved.")
        self.assertEqual((self.data / "ledger.csv").read_bytes(), ledger)
        row, = self.audit()
        self.assertEqual((row["action"], row["approver"], row["reason"]),
                         ("mode_changed", "Asha", "co-lending changed from yes to no"))
        # And back: the warning now names the no-partner mode.
        self.assertIn("1 payment is already recorded as 100% lender (no partner bank)", self.get("/api/settings")["locale"]["mode_warning"])
        self.assertIn("problem", self.save("yes"))
        self.assertEqual(self.save("yes", confirm_mode_change=True)["values"]["CO_LENDING"], "yes")
        self.assertEqual([r["reason"] for r in self.audit()][-1], "co-lending changed from no to yes")
        self.assertEqual((self.data / "ledger.csv").read_bytes(), ledger)

    def test_saving_the_same_mode_needs_no_confirm_and_logs_nothing(self):
        self.put_ledger(3)
        self.assertEqual(self.save("yes")["done"], "Saved.")
        self.assertEqual(self.audit(), [])
        self.assertEqual(self.post("/api/settings", {"values": {"BANK": "sbi"}})["done"], "Saved.")

    def test_a_bad_value_is_refused_and_nothing_is_written(self):
        before = self.env.read_bytes()
        self.assertIn("CO_LENDING must be yes or no", self.save("maybe")["problem"])
        self.assertEqual(self.env.read_bytes(), before)

    def test_changing_the_setting_posts_nothing(self):
        self.save("no")
        self.assertEqual([p for p in self.fc.posts if p[0] != "authentication"], [])
        self.assertFalse((self.data / "ledger.csv").exists())


class RunAndDownloadTest(Page):
    def test_posting_in_no_partner_mode_records_it_all_as_the_lenders_and_says_so(self):
        self.post("/api/settings", {"values": {"CO_LENDING": "no"}})
        self.post("/api/match")
        result = self.post("/api/post", {"count": 1, "total": "5200.00"})
        ledger, = files.read("ledger.csv")
        self.assertEqual((ledger["amount"], ledger["partner_share"], ledger["lender_share"]), ("5200.00", "0.00", "5200.00"))
        text = " ".join(result["log"])
        self.assertIn("recorded each in the ledger as the lender's", text)
        self.assertIn("Checked the ledger against Fineract: 1 rows, 0 problems", text)
        self.assertNotIn("partner", text.lower())
        self.assertNotIn("80:20", text)

    def test_posting_with_defaults_keeps_the_split_wording(self):
        self.post("/api/match")
        text = " ".join(self.post("/api/post", {"count": 1, "total": "5200.00"})["log"])
        self.assertIn("split each 80:20 in the ledger", text)
        self.assertIn("(partner 4,160.00, lender 1,040.00)", text)

    def test_the_download_is_the_report_that_exists(self):
        self.post("/api/match")
        self.post("/api/post", {"count": 1, "total": "5200.00"})
        self.assertEqual(self.request("GET", "/partner_report.csv")[0], 200)
        self.assertEqual(self.request("GET", "/repayments_report.csv")[0], 404)
        self.post("/api/settings", {"values": {"CO_LENDING": "no"}, "confirm_mode_change": True})
        status, text = self.request("GET", "/repayments_report.csv")
        self.assertEqual(status, 200)
        rows = list(csv.DictReader(text.splitlines()))
        self.assertEqual(list(rows[0]), ["month", "loan_ref", "payments", "amount"])
        self.assertEqual((rows[-1]["month"], rows[-1]["amount"]), ("TOTAL", "5200.00"))
        self.assertEqual(self.request("GET", "/partner_report.csv")[0], 404)

    def test_the_page_config_follows_the_mode(self):
        self.post("/api/settings", {"values": {"CO_LENDING": "no"}})
        _, page = self.request("GET", "/")
        self.assertIn('"report_file": "repayments_report.csv"', page)
        self.assertIn('"co_lending": false', page)


class DefaultsUnchangedTest(unittest.TestCase):
    def test_the_default_report_file_is_byte_for_byte_the_old_one(self):
        import tempfile
        ledger = [{**r, "partner_share": str(split(r["amount"])[0]), "lender_share": str(split(r["amount"])[1])} for r in LEDGER]
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(files, "DATA", Path(folder)), \
                mock.patch.object(files, "read", return_value=ledger), mock.patch("sys.stdout", io.StringIO()):
            report.main()  # no settings given: read from DATA.parent/.env, which has none, so the defaults
            got = (Path(folder) / "partner_report.csv").read_bytes()
        self.assertEqual(got, (b"month,loan_ref,payments,amount,partner_share,lender_share\r\n"
                               b"2026-09,L1,2,8450.03,6760.02,1690.01\r\n2026-10,L2,1,1000.33,800.26,200.07\r\n"
                               b"TOTAL,,3,9450.36,7560.28,1890.08\r\n"))


if __name__ == "__main__":
    unittest.main()
