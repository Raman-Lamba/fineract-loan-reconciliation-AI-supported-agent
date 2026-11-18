"""Names from other countries (stage C): matcher.names on its own, then the matcher and the suggester end to end.

Every person here is made up. Each convention is tested both ways: the same person written differently fits,
and a different or only weakly similar name does not. Where a choice is conservative (Mueller is not taken for
Mueller with an umlaut, nothing is transliterated across scripts) the test says so.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import locale_settings as loc  # noqa: E402
import posting.run as files  # noqa: E402
from matcher.match import decide  # noqa: E402
from matcher.names import ORDERS, alike_keys, is_company, name_fits, normalise, tokens  # noqa: E402
from review.suggest import suggest  # noqa: E402
from statement.fetch_schedules import fetch  # noqa: E402
from tests.test_ui_connect import Page  # noqa: E402


def fit(payer, borrower, order="western"):
    return name_fits(payer, tokens(borrower), order)


class NormaliseTest(unittest.TestCase):
    def test_case_accents_titles_and_punctuation(self):
        self.assertEqual(normalise("  José   GARCÍA "), "jose garcia")
        self.assertEqual(normalise("Mr. R. Kumar"), "r kumar")
        self.assertEqual(normalise("SHRI Ramesh Kumar"), "ramesh kumar")
        self.assertEqual(normalise("O'Brien"), "obrien")
        self.assertEqual(normalise("Anne-Marie  Dupont--Leclerc"), "anne-marie dupont-leclerc")
        self.assertEqual(tokens("Anne-Marie Dupont-Leclerc"), ["anne-marie", "dupont-leclerc"])

    def test_a_title_is_kept_when_it_would_leave_one_word(self):
        # Sri is also a given name (Indonesia): "Sri Wahyuni" keeps it. UNVERIFIED how often banks drop it.
        self.assertEqual(tokens("Sri Wahyuni"), ["sri", "wahyuni"])
        self.assertEqual(tokens("Sri Ramesh Kumar"), ["ramesh", "kumar"])

    def test_marks_are_kept_outside_latin_and_greek(self):
        self.assertEqual(normalise("Иван Йорданов"), "иван йорданов")  # Й is not И: a letter, not an accent
        self.assertEqual(normalise("रमेश कुमार"), "रमेश कुमार")  # Devanagari vowel signs are letters' parts
        self.assertEqual(normalise("Γιώργος"), normalise("ΓΙΩΡΓΟΣ"))

    def test_companies(self):
        for name in ("Kumar Traders Pvt Ltd", "Garcia S.A.", "Muller GmbH", "Acme Holdings LLC", "Roga i Kopyta OOO"):
            self.assertTrue(is_company(tokens(name)), name)
        for name in ("Acme Traders", "Kumar Enterprises", "Ramesh Kumar"):
            self.assertFalse(is_company(tokens(name)), name)


class FitsTest(unittest.TestCase):
    def assertFits(self, borrower, *payers, order="western"):
        for payer in payers:
            self.assertTrue(fit(payer, borrower, order), f"{payer!r} should fit {borrower!r} ({order})")

    def assertNot(self, borrower, *payers, order="western"):
        for payer in payers:
            self.assertFalse(fit(payer, borrower, order), f"{payer!r} must not fit {borrower!r} ({order})")

    def test_todays_rules_still_hold(self):
        self.assertFits("Ramesh Kumar", "RAMESH KUMAR", "RAMESH KUM", "R KUMAR", "RAM KUMAR", "KUMAR RAMESH",
                        "KUMAR RAME", "RAMESHKUMAR", "RAMESHKUMA")
        self.assertFits("Lakshmi Das", "DAS LAKSHM", "LAKSHMI DAS")  # Das, a surname, is not the particle das
        self.assertNot("Ramesh Kumar", "GEETA KUMAR", "RA KUMAR", "RAMESH K", "KUMAR", "RAMESH", "R K STORES",
                       "RAMESHGUPTA")

    def test_accents(self):
        self.assertFits("José García", "JOSE GARCIA", "JOSÉ GARCÍA", "GARCIA JOSE", "J GARCIA", "jose garcía")
        self.assertNot("José García", "JUAN GARCIA", "JOSE GARZA")

    def test_umlauts_and_sharp_s_are_conservative(self):
        self.assertFits("Jürgen Müller", "JURGEN MULLER", "JÜRGEN MÜLLER")
        # UE for Ü is a German spelling rule, not a normalisation: assumed equal it would join two real surnames
        # (Muller and Mueller both exist). Such a line queues and a person decides.
        self.assertNot("Jürgen Müller", "JUERGEN MUELLER")
        self.assertFits("Heinz Weiß", "HEINZ WEISS")  # casefold: ß is written SS in capitals

    def test_dutch_and_belgian_particles(self):
        self.assertFits("Jan van der Berg", "JAN VAN DER BERG", "J VAN DER BERG", "JAN BERG", "JAN VANDERBERG",
                        "VAN DER BERG JAN", "BERG JAN")
        self.assertFits("Marie De Smet", "MARIE DE SMET", "MARIE DESMET", "MARIE SMET")
        self.assertNot("Jan van der Berg", "PIET VAN DER BERG", "JAN VAN DER MEER")
        # A particle-word as a given name is a name: "Al" here is not the Arabic article.
        self.assertFits("Al Smith", "AL SMITH", "SMITH AL")
        self.assertNot("Al Smith", "JOHN AL SMITH", "SMITH")

    def test_spanish_double_surnames(self):
        self.assertFits("José García López", "JOSE GARCIA LOPEZ", "GARCIA LOPEZ JOSE", "J GARCIA LOPEZ",
                        "JOSE GARCIA LOP", "JOSE GARCIA")  # the last two: cut short, as today's prefix rule
        self.assertNot("José García López", "MARIA GARCIA LOPEZ", "JOSE LOPEZ", "GARCIA LOPEZ")

    def test_portuguese_and_brazilian_full_names(self):
        self.assertFits("Maria das Graças Silva Santos", "MARIA DAS GRACAS SILVA SANTOS",
                        "MARIA GRACAS SILVA SANTOS", "MARIA DAS GRACAS SILVA SAN")
        # Cut to SA it reads as a company (S.A.): no fit, so stricter, never looser.
        self.assertNot("Maria das Graças Silva Santos", "MARIA DAS GRACAS SILVA SA")
        # The last surname alone is common in Brazil, but so is sharing it: that one is a person's call.
        self.assertNot("Maria das Graças Silva Santos", "MARIA SANTOS", "ANA DAS GRACAS SILVA SANTOS", "MARIA G S SANTOS")

    def test_arabic_names_in_latin_and_in_arabic(self):
        self.assertFits("Ahmed bin Khalid Al Mansouri", "AHMED BIN KHALID AL-MANSOURI", "AHMED KHALID AL MANSOURI",
                        "AHMED BIN KHALID AL MANSOURI")
        self.assertNot("Ahmed bin Khalid Al Mansouri", "OMAR BIN KHALID AL MANSOURI", "AHMED AL MANSOURI")
        self.assertFits("أحمد خالد المنصوري", "أحمد خالد المنصوري")
        self.assertNot("أحمد خالد المنصوري", "أحمد المنصوري", "خالد")  # no prefix or initial tolerance

    def test_russian_patronymics(self):
        self.assertFits("Ivan Sidorov", "IVAN PETROVICH SIDOROV", "SIDOROV IVAN PETROVICH", "IVAN SIDOROV")
        self.assertNot("Ivan Sidorov", "PYOTR SIDOROV", "PYOTR IVAN SIDOROV", "I P SIDOROV")
        self.assertFits("Ivan Petrovich Sidorov", "IVAN PETROVICH SIDOROV")
        self.assertFits("Иван Сидоров", "ИВАН СИДОРОВ", "И СИДОРОВ", "СИДОРОВ ИВАН ПЕТРОВИЧ")

    def test_indian_initials_and_titles(self):
        self.assertFits("K Ramesh", "RAMESH K", "K RAMESH", "SRI K RAMESH")
        self.assertFits("Ramesh Kumar", "SHRI RAMESH KUMAR", "MR R KUMAR", "DR. RAMESH KUMAR", "SMT RAMESH KUMAR")
        self.assertNot("K Ramesh", "RAMESH", "SURESH K")
        self.assertFits("रमेश कुमार", "रमेश कुमार", "कुमार रमेश")
        self.assertNot("रमेश कुमार", "रमेश", "र कुमार")

    def test_kenyan_names(self):
        self.assertFits("Wanjiru Kamau", "WANJIRU KAMAU", "KAMAU WANJIRU", "W KAMAU")
        self.assertFits("Grace Akinyi Otieno", "GRACE AKINYI OTIENO", "OTIENO GRACE AKINYI")
        self.assertNot("Wanjiru Kamau", "NJERI KAMAU")
        self.assertNot("Grace Akinyi Otieno", "GRACE OTIENO")  # the middle (family) name is missing: queued

    def test_family_name_first_in_latin(self):
        for order in ("western", "family-first", "auto"):
            self.assertFits("Yamada Taro", "YAMADA TARO", "TARO YAMADA", order=order)
        self.assertFits("Yamada Taro", "YAMADA T", "T YAMADA", "YAMADA TAR", order="family-first")
        self.assertNot("Yamada Taro", "YAMADA T", "T YAMADA", order="western")
        self.assertNot("Yamada Taro", "YAMADA T", "YAMADA TAR", "YAMADATARO", order="auto")  # no partial in auto
        self.assertFits("Wang Xiao Ming", "WANG XIAOMING", "WANG XIAO MING", "WANG X", order="family-first")
        self.assertFits("Kim Min-jun", "KIM MINJUN", "KIM MIN JUN", "KIM M", order="family-first")
        self.assertNot("Kim Min-jun", "KIM JIHOON", "LEE MINJUN", order="family-first")
        self.assertFits("Kovács István", "KOVACS ISTVAN", "KOVACS I", order="family-first")  # Hungary
        self.assertNot("Kovács István", "KOVACS I", order="western")

    def test_native_scripts_without_spaces_are_exact_only(self):
        for order in ("western", "family-first", "auto"):
            self.assertFits("山田 太郎", "山田太郎", "山田 太郎", "太郎 山田", order=order)
            self.assertNot("山田 太郎", "山田", "山田 花子", "山田太", order=order)
            self.assertFits("김민준", "김민준", order=order)
            self.assertNot("김민준", "김민", order=order)

    def test_another_script_is_never_a_fit(self):
        self.assertNot("山田 太郎", "YAMADA TARO", order="auto")
        self.assertNot("Иван Сидоров", "IVAN SIDOROV")
        self.assertNot("Ivan Sidorov", "ИВАН СИДОРОВ")
        self.assertNot("أحمد خالد المنصوري", "AHMED KHALID ALMANSOURI")
        self.assertNot("Ramesh Kumar", "रमेश कुमार")

    def test_single_name_people_fit_only_exactly(self):
        self.assertFits("Sukarno", "SUKARNO", "sukarno")
        self.assertNot("Sukarno", "SUKARN", "S", "SUKARNO HATTA", "HATTA SUKARNO")

    def test_a_company_never_fits_a_person(self):
        self.assertNot("Ramesh Kumar", "RAMESH KUMAR LTD", "RAMESH KUMAR PVT LTD", "KUMAR RAMESH GMBH")
        self.assertNot("José García", "GARCIA S.A.", "JOSE GARCIA SA")
        self.assertFits("Acme Traders Ltd", "ACME TRADERS LTD", "ACME TRADERS")
        self.assertNot("Acme Traders Ltd", "A TRADERS LTD", "ACME LTD")

    def test_joint_payers_are_split_and_each_part_tried(self):
        for joint in ("RAMESH KUMAR AND ANITA NAIR", "RAMESH KUMAR & ANITA NAIR", "RAMESH KUMAR / ANITA NAIR",
                      "RAMESH KUMAR, ANITA NAIR"):
            self.assertFits("Ramesh Kumar", joint)
            self.assertFits("Anita Nair", joint)
            self.assertNot("Suresh Patel", joint)
        self.assertFits("Ramesh Kumar", "KUMAR, RAMESH")  # one person written surname first, not two

    def test_hyphens_and_apostrophes(self):
        self.assertFits("Anne-Marie Dupont-Leclerc", "ANNE MARIE DUPONT LECLERC", "ANNEMARIE DUPONTLECLERC",
                        "A DUPONT-LECLERC", "ANNE-MARIE DUPONT")
        self.assertNot("Anne-Marie Dupont-Leclerc", "MARIE DUPONT LECLERC", "PAUL DUPONT-LECLERC")
        self.assertFits("Sean O'Brien", "SEAN OBRIEN", "SEAN O'BRIEN", "S OBRIEN", "OBRIEN SEAN")
        self.assertNot("Sean O'Brien", "SEAN BRIEN")

    def test_look_alike_keys_compare_normalised(self):
        self.assertTrue(alike_keys(tokens("José García"), "western") & alike_keys(tokens("JUAN GARCIA"), "western"))
        self.assertFalse(alike_keys(tokens("José García"), "western") & alike_keys(tokens("Pedro García"), "western"))
        # auto reads both ways, so it calls more pairs look-alikes: stricter, never looser.
        self.assertFalse(alike_keys(tokens("Yamada Taro"), "western") & alike_keys(tokens("Yamada Tomoko"), "western"))
        self.assertTrue(alike_keys(tokens("Yamada Taro"), "auto") & alike_keys(tokens("Yamada Tomoko"), "auto"))


def loan(ref, name, due, amount, n):
    first, _, last = name.partition(" ")
    return {"loan_ref": ref, "loan_id": n, "account_no": f"{n:09d}", "client_id": n, "firstname": first, "lastname": last,
            "name_tokens": tokens(name), "instalments": [{"due": due, "amount": amount}]}


# A made-up book from several countries; amounts chosen so the cases below collide only where they mean to.
BOOK = [loan("G1", "José García López", "2026-09-05", 500.0, 1),
        loan("G2", "Jürgen Müller", "2026-09-05", 610.0, 2),
        loan("G3", "Jan van der Berg", "2026-09-05", 720.0, 3),
        loan("G4", "Yamada Taro", "2026-09-05", 830.0, 4),
        loan("G5", "Иван Сидоров", "2026-09-05", 940.0, 5),
        loan("G6", "Wanjiru Kamau", "2026-09-05", 1050.0, 6),
        loan("G7", "Wangari Kamau", "2026-09-05", 1050.0, 7),   # same surname, same initial, same amount as G6
        loan("G8", "Ana Pereira", "2026-09-05", 1160.0, 8),
        loan("G9", "Ana Pereira", "2026-09-06", 1160.0, 9),     # a namesake owing the same
        loan("G10", "Sukarno", "2026-09-05", 1270.0, 10),
        loan("G11", "Ramesh Kumar", "2026-09-05", 1380.0, 11),
        loan("G12", "Anita Nair", "2026-09-05", 1380.0, 12)]


def decision(payer, amount, order="western", book=BOOK):
    line = {"txn_id": "900000000001", "date": "2026-09-05", "amount": amount, "narration": f"NEFT-900000000001-{payer}-EMI"}
    return decide([line], book, "synthetic", name_order=order)[0]


class MatcherEndToEndTest(unittest.TestCase):
    def assertPosts(self, d, ref):
        self.assertEqual((d["outcome"], d["loan_ref"]), ("post", ref), d["reason"])

    def assertQueues(self, d, candidates):
        self.assertEqual((d["outcome"], d["loan_ref"], sorted(d["candidates"])), ("queue", "", candidates), d["reason"])

    def test_one_clear_fit_posts(self):
        self.assertPosts(decision("JAN VAN DER BERG", "720.00"), "G3")
        self.assertPosts(decision("SUKARNO", "1270.00"), "G10")
        self.assertPosts(decision("YAMADA T", "830.00", "family-first"), "G4")

    def test_accent_variants_post_when_unique(self):
        self.assertPosts(decision("JOSE GARCIA LOPEZ", "500.00"), "G1")
        self.assertPosts(decision("JURGEN MULLER", "610.00"), "G2")
        self.assertPosts(decision("ИВАН СИДОРОВ", "940.00"), "G5")

    def test_two_possible_borrowers_queue(self):
        self.assertQueues(decision("ANA PEREIRA", "1160.00"), ["G8", "G9"])
        # a joint payer whose two people both owe this amount
        self.assertQueues(decision("RAMESH KUMAR AND ANITA NAIR", "1380.00"), ["G11", "G12"])

    def test_the_look_alike_rule_holds_in_the_new_normalisation(self):
        self.assertQueues(decision("WANJIRU KAMAU", "1050.00"), ["G6", "G7"])
        self.assertQueues(decision("WANJIRU KAMAU", "1050.00", "auto"), ["G6", "G7"])

    def test_a_wrong_name_order_setting_still_queues_look_alikes(self):
        # Western names read family-first: SURESH is taken for the family name, yet Sanjay Patel owing the same
        # still makes it a person's call (family-first keeps the western look-alike check too).
        book = [loan("P1", "Suresh Patel", "2026-09-05", 700.0, 1), loan("P2", "Sanjay Patel", "2026-09-05", 700.0, 2)]
        for order in ORDERS:
            self.assertQueues(decision("SURESH PATEL", "700.00", order, book), ["P1", "P2"])

    def test_a_weak_or_cross_script_name_queues(self):
        self.assertQueues(decision("IVAN SIDOROV", "940.00"), [])  # no transliteration
        self.assertQueues(decision("JUERGEN MUELLER", "610.00"), [])
        self.assertQueues(decision("YAMADA T", "830.00", "western"), [])
        self.assertQueues(decision("YAMADA T", "830.00", "auto"), [])
        self.assertQueues(decision("SUKARN", "1270.00"), [])
        self.assertQueues(decision("GARCIA LOPEZ SA", "500.00"), [])  # a company paying

    def test_fetch_keeps_first_and_last_name_as_before_and_adds_the_words(self):
        class Fineract:
            def execute_get(self, endpoint, params=None):
                if endpoint == "loans":
                    return {"totalFilteredRecords": 1, "pageItems": [{"id": 7}]}
                return {"id": 7, "accountNo": "000000007", "externalId": "G7", "clientId": 3,
                        "clientName": "Maria das Graças Silva", "repaymentSchedule": {"periods": []}}
        got = fetch(Fineract(), lambda response, what: response)[0]
        self.assertEqual((got["firstname"], got["lastname"]), ("Maria", "das Graças Silva"))
        self.assertEqual(got["name_tokens"], ["maria", "das", "gracas", "silva"])

    def test_a_loan_without_name_tokens_is_read_from_its_first_and_last_name(self):
        old = [{k: v for k, v in g.items() if k != "name_tokens"} for g in BOOK]
        self.assertPosts(decision("JOSE GARCIA LOPEZ", "500.00", book=old), "G1")


class SuggestTest(unittest.TestCase):
    def test_accents_order_and_particles_count_in_the_shortlist(self):
        line = {"txn_id": "1", "date": "2026-09-05", "amount": "720.00", "narration": "NEFT-1-BERG JAN VAN DER-EMI"}
        self.assertEqual(suggest(line, BOOK)[0]["loan_ref"], "G3")
        line = {**line, "amount": "499.00", "narration": "NEFT-1-JOSE GARCIA LOPEZ-EMI"}
        top = suggest(line, BOOK)[0]
        self.assertEqual((top["loan_ref"], top["similarity"]), ("G1", "same name"))

    def test_a_family_first_name_with_an_initial(self):
        line = {"txn_id": "1", "date": "2026-09-05", "amount": "800.00", "narration": "NEFT-1-YAMADA T-EMI"}
        top = suggest(line, BOOK, order="family-first")[0]
        self.assertEqual((top["loan_ref"], top["similarity"]), ("G4", "same surname, first initial matches"))


class NameOrderSettingTest(unittest.TestCase):
    def test_the_setting_defaults_to_western_and_refuses_anything_else(self):
        self.assertEqual(loc.DEFAULT.name_order, "western")
        self.assertEqual(loc.load({"NAME_ORDER": "family-first"}).name_order, "family-first")
        self.assertEqual(loc.load({"COUNTRY": "Kenya"}).name_order, "western")
        with self.assertRaises(ValueError):
            loc.load({"NAME_ORDER": "eastern"})
        self.assertEqual(loc.view(loc.DEFAULT)["values"]["NAME_ORDER"], "western")
        self.assertEqual(loc.view(loc.DEFAULT)["name_orders"], ("western", "family-first", "auto"))
        for preset in loc.PRESETS.values():
            self.assertEqual(preset["name_order"], "western")


class NameOrderPageTest(Page):
    def test_name_order_is_saved_from_the_connect_tab_and_used_by_match(self):
        saved = self.post("/api/settings", {"values": {"NAME_ORDER": "auto"}})
        self.assertEqual(saved["values"]["NAME_ORDER"], "auto")
        self.assertIn("NAME_ORDER=auto", self.env.read_text())
        self.assertIn("problem", self.post("/api/settings", {"values": {"NAME_ORDER": "sideways"}}))
        # The demo's R KUMAR line fits two loans by an initial; with auto (no initials) it fits none. Queued either way.
        self.assertNotIn("problem", self.post("/api/match", {}))
        rows = {d["txn_id"]: d for d in files.read("decisions.csv")}
        self.assertEqual((rows["700000000001"]["outcome"], rows["700000000001"]["candidates"]), ("queue", ""))
        self.assertIn("no loan fits 'R KUMAR'", rows["700000000001"]["reason"])
        self.assertEqual(rows["700000000004"]["outcome"], "post")  # ANITA SHARMA in full still posts
        page = self.request("GET", "/")[1]
        self.assertIn('name="NAME_ORDER"', page)


if __name__ == "__main__":
    unittest.main()
