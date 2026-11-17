"""The Claude provider's Quality setting (which model does which task), with fakes only: no live call."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai import client, layout, narration, rank  # noqa: E402
from tests.test_ai import KEY, Opener, model_says  # noqa: E402
from tests.test_suggest import SHORTLIST  # noqa: E402
from tests.test_ui_connect import Page  # noqa: E402

OPUS, SONNET, HAIKU = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"
EXPECTED = {"balanced": {"layout": OPUS, "narration": SONNET, "rank": OPUS},
            "best": {"layout": OPUS, "narration": OPUS, "rank": OPUS},
            "cheapest": {"layout": HAIKU, "narration": HAIKU, "rank": HAIKU}}
LINE = "BY TRANSFER-UPI/CR/507296189751/MEENA IYER/UTIB/x"


class MappingTest(unittest.TestCase):
    def test_each_quality_maps_each_task_to_its_model(self):
        for quality, tasks in EXPECTED.items():
            for task, model in tasks.items():
                cfg = client.config({"AI_QUALITY": quality})
                self.assertEqual(client.task_config(cfg, task)["model"], model, (quality, task))

    def test_balanced_is_the_default_and_a_bad_value_is_balanced(self):
        for settings in ({}, {"AI_QUALITY": ""}, {"AI_QUALITY": "luxury"}):
            self.assertEqual(client.config(settings)["models"], EXPECTED["balanced"], settings)
        self.assertEqual(client.config({})["model"], SONNET)  # what the reader and Test AI use: the narration model

    def test_an_explicit_ai_model_overrides_every_task(self):
        for quality in EXPECTED:
            cfg = client.config({"AI_QUALITY": quality, "AI_MODEL": "claude-fable-5-2"})
            self.assertEqual(set(cfg["models"].values()), {"claude-fable-5-2"})
            self.assertTrue(cfg["override"])
        self.assertFalse(client.config({"AI_QUALITY": "best"})["override"])

    def test_other_providers_ignore_quality(self):
        for settings in ({"AI_PROVIDER": "openai"}, {"AI_PROVIDER": "gemini", "AI_MODEL": "gemini-3.6-flash"},
                         {"AI_PROVIDER": "other", "AI_MODEL": "llama3.1", "AI_BASE_URL": "http://localhost:11434/v1"}):
            for quality in EXPECTED:
                cfg = client.config({**settings, "AI_QUALITY": quality})
                self.assertEqual(len(set(cfg["models"].values())), 1, settings)
                self.assertEqual(cfg["models"]["rank"], cfg["model"])
        self.assertEqual(client.config({"AI_PROVIDER": "openai"})["model"], "gpt-6.1-sol")


class RequestTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        self.env = self.data / ".env"

    def send(self, quality, task, *answers, extra="", kind="read"):
        self.env.write_text(f"ANTHROPIC_API_KEY={KEY}\nAI_QUALITY={quality}\n{extra}")
        cfg = client.config(client.read_env(self.env))
        client.record_consent(self.data, cfg)
        client.record_consent(self.data, cfg, "suggest")
        opener = Opener(*answers)
        return client.sender(self.env, self.data, opener, kind=kind, task=task), opener

    def body(self, opener):
        return json.loads(opener.requests[0].data)

    def test_layout_narration_and_rank_calls_carry_the_right_model(self):
        rows = [["Date", "Narration", "Amount"], ["05/09/2026", "x", "1.00"]]
        items = {"items": [{"i": 0, "payer_name": "MEENA IYER", "channel": "UPI", "reference": "", "loan_account": ""}]}
        ranked = {"ranked": [{"loan_ref": SHORTLIST[0]["loan_ref"], "reason": "name"}]}
        for quality, tasks in EXPECTED.items():
            send, opener = self.send(quality, "layout", model_says({}))
            layout.ask(rows, send)
            self.assertEqual(self.body(opener)["model"], tasks["layout"], quality)
            send, opener = self.send(quality, "narration", model_says(items))
            narration.extract([LINE], send, self.data / f"c-{quality}.json")
            self.assertEqual(self.body(opener)["model"], tasks["narration"], quality)
            send, opener = self.send(quality, "rank", model_says(ranked), kind="suggest")
            rank.rank("UPI/1/GEETA KUMA", SHORTLIST, send, self.data / f"r-{quality}.json")
            self.assertEqual(self.body(opener)["model"], tasks["rank"], quality)

    def test_the_default_task_is_narration_and_an_unknown_task_is_refused(self):
        send, _ = self.send("balanced", "narration")
        self.assertEqual(send.scope, f"claude:{SONNET}")
        with self.assertRaises(client.AIError):
            self.send("balanced", "poetry")

    def test_ai_model_in_env_overrides_the_quality_in_every_request(self):
        for task in ("layout", "narration", "rank"):
            send, opener = self.send("best", task, model_says({}), extra=f"AI_MODEL={HAIKU}\n")
            send(client.request("s", "p", {}))
            self.assertEqual(self.body(opener)["model"], HAIKU, task)

    def test_the_refusal_fallback_follows_the_request_model(self):
        for quality, task, supported in (("balanced", "layout", True), ("balanced", "narration", True),
                                         ("best", "rank", True), ("cheapest", "narration", False)):
            send, opener = self.send(quality, task, model_says({}))
            send(client.request("s", "p", {}))
            body = self.body(opener)
            self.assertEqual("fallbacks" in body, supported, (quality, task))
            self.assertEqual("anthropic-beta" in {k.lower() for k in opener.requests[0].headers}, supported)

    def test_caches_never_cross_models(self):
        scopes = {q: {t: self.send(q, t)[0].scope for t in ("layout", "narration", "rank")} for q in EXPECTED}
        self.assertEqual(scopes["balanced"]["narration"], f"claude:{SONNET}")
        self.assertNotEqual(scopes["balanced"]["narration"], scopes["best"]["narration"])
        self.assertNotEqual(scopes["best"]["narration"], scopes["cheapest"]["narration"])
        # a narration answer made by Sonnet is not used for Opus
        cache = self.data / "ai_cache.json"
        first = Opener(model_says({"items": [{"i": 0, "payer_name": "MEENA IYER", "channel": "", "reference": "", "loan_account": ""}]}))
        self.send("balanced", "narration")
        narration.extract([LINE], client.sender(self.env, self.data, first, task="narration"), cache)
        other, other_opener = self.send("best", "narration", model_says({"items": []}))
        _, stats = narration.extract([LINE], other, cache)
        self.assertEqual((stats["from_cache"], stats["sent"]), (0, 1))
        self.assertEqual(len(other_opener.requests), 1)
        # and a ranking made by one model is not found by another
        rank_cache = self.data / "rank.json"
        ranked = {"ranked": [{"loan_ref": SHORTLIST[0]["loan_ref"], "reason": "r"}]}
        s1, _ = self.send("best", "rank", model_says(ranked), kind="suggest")
        rank.rank("UPI/1/GEETA KUMA", SHORTLIST, s1, rank_cache)
        self.assertIsNotNone(rank.cached("UPI/1/GEETA KUMA", SHORTLIST, s1.scope, rank_cache))
        self.assertIsNone(rank.cached("UPI/1/GEETA KUMA", SHORTLIST, f"claude:{HAIKU}", rank_cache))

    def test_changing_the_quality_does_not_ask_for_consent_again(self):
        self.send("balanced", "narration")
        self.env.write_text(f"ANTHROPIC_API_KEY={KEY}\nAI_QUALITY=cheapest\n")
        client.sender(self.env, self.data, Opener(), task="narration")  # raises AIError if consent were asked again
        client.sender(self.env, self.data, Opener(), kind="suggest", task="rank")


class QualityPageTest(Page):
    def test_the_view_lists_the_qualities_and_what_each_does(self):
        ai = self.get("/api/settings")["ai"]
        self.assertEqual((ai["quality"], ai["override"]), ("balanced", False))
        self.assertEqual(ai["models"], EXPECTED["balanced"])
        self.assertEqual([q["id"] for q in ai["qualities"]], ["balanced", "best", "cheapest"])
        for q in ai["qualities"]:
            self.assertTrue(q["label"] and q["explain"])
            self.assertNotIn("claude-", q["label"] + q["explain"])  # plain English, no model ids

    def test_the_form_writes_ai_quality_and_never_echoes_a_secret(self):
        result = self.post("/api/settings", {"values": {"AI_QUALITY": "best", "AI_API_KEY": KEY}})
        self.assertNotIn("problem", result)
        self.assertIn("AI_QUALITY=best", self.env.read_text())
        self.assertEqual(result["ai"]["quality"], "best")
        self.assertEqual(result["ai"]["models"], EXPECTED["best"])
        self.assertNotIn(KEY, json.dumps(result))
        self.assertNotIn("claude-opus-5-5\n", self.env.read_text())  # only the quality was written, no model line

    def test_a_bad_quality_is_refused_and_nothing_is_written(self):
        before = self.env.read_bytes()
        self.assertIn("problem", self.post("/api/settings", {"values": {"AI_QUALITY": "luxury"}}))
        self.assertEqual(self.env.read_bytes(), before)

    def test_ai_model_in_env_is_shown_as_overriding_and_can_be_cleared(self):
        self.post("/api/settings", {"values": {"AI_QUALITY": "cheapest", "AI_MODEL": OPUS}})
        ai = self.get("/api/settings")["ai"]
        self.assertEqual((ai["override"], ai["models"]["layout"]), (True, OPUS))
        self.post("/api/settings", {"values": {"AI_MODEL": ""}})
        ai = self.get("/api/settings")["ai"]
        self.assertEqual((ai["override"], ai["models"]["layout"]), (False, HAIKU))

    def test_test_ai_says_which_model_it_used_and_a_quality_change_keeps_consent(self):
        self.post("/api/settings", {"values": {"AI_API_KEY": KEY}})
        self.assertTrue(self.post("/api/consent", {"agree": True})["ai"]["consent"])
        self.opener.answers.append(model_says({"ok": True}))
        result = self.post("/api/testai")
        self.assertTrue(result["ok"], result)
        self.assertIn(SONNET, result["detail"])
        self.assertIn("narration", result["detail"])
        self.assertEqual(json.loads(self.opener.requests[0].data)["model"], SONNET)
        self.assertTrue(self.post("/api/settings", {"values": {"AI_QUALITY": "cheapest"}})["ai"]["consent"])
        self.opener.answers.append(model_says({"ok": True}))
        self.assertIn(HAIKU, self.post("/api/testai")["detail"])


if __name__ == "__main__":
    unittest.main()
