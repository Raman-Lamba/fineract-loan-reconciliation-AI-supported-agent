"""The AI reader with OpenAI, Gemini and "other" (OpenAI-style chat completions), with fakes only.

No test here reaches any provider. Opener stands in for urlopen, FakeSend for a whole sender.
"""
import hashlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import posting.run as files  # noqa: E402
from ai import client, narration  # noqa: E402
from tests.test_ai import FakeSend, Opener, model_says  # noqa: E402
from tests.test_ui_connect import Page  # noqa: E402

OPENAI_KEY, GEMINI_KEY, OTHER_KEY = "sk-test-OPENAI-NOT-REAL-0001", "AIza-test-GEMINI-NOT-REAL-0002", "tok-test-OTHER-NOT-REAL-0003"
LINE = "BY TRANSFER-UPI/CR/507296189751/MEENA IYER/UTIB/x"
ITEMS = {"items": [{"i": 0, "payer_name": "MEENA IYER", "channel": "UPI", "reference": "", "loan_account": ""}]}


def chat_says(answer, finish="stop", **message):
    """A chat completions response whose message content is `answer` (a dict as JSON, or raw text)."""
    text = answer if isinstance(answer, str) else json.dumps(answer)
    return {"choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": text, **message}}]}


def bad_request(code=400, message="response_format is not supported"):
    return urllib.error.HTTPError("x", code, message, {}, io.BytesIO(json.dumps({"error": {"message": message}}).encode()))


class Setup(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        self.env = self.data / ".env"
        sleep = mock.patch.object(client.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def configure(self, text, consent=True):
        self.env.write_text(text)
        if consent:
            client.record_consent(self.data, client.config(client.read_env(self.env)))

    def send(self, opener, text, consent=True):
        self.configure(text, consent)
        return client.sender(self.env, self.data, opener)


class AdapterTest(Setup):
    def test_openai_request_shape_and_parsing(self):
        opener = Opener(chat_says(ITEMS))
        send = self.send(opener, f"AI_PROVIDER=openai\nOPENAI_API_KEY={OPENAI_KEY}\n")
        self.assertEqual(send(client.request("sys prompt", "user prompt", {"type": "object"}, 777)), ITEMS)
        req, = opener.requests
        body = json.loads(req.data)
        self.assertEqual(req.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(req.get_header("Authorization"), f"Bearer {OPENAI_KEY}")
        self.assertEqual((body["model"], body["max_completion_tokens"]), ("gpt-6.1-sol", 777))
        self.assertEqual(body["messages"], [{"role": "system", "content": "sys prompt"}, {"role": "user", "content": "user prompt"}])
        self.assertEqual(body["response_format"], {"type": "json_schema", "json_schema": {
            "name": "answer", "strict": True, "schema": {"type": "object"}}})
        self.assertNotIn(OPENAI_KEY, req.data.decode())
        self.assertNotIn("x-api-key", {k.lower() for k in req.headers})  # Claude's header never goes to another provider

    def test_gemini_uses_its_openai_compatible_endpoint(self):
        opener = Opener(chat_says(ITEMS))
        send = self.send(opener, f"AI_PROVIDER=gemini\nGEMINI_API_KEY={GEMINI_KEY}\nAI_MODEL=gemini-3.5-flash-lite\n")
        send(client.request("s", "p", {}))
        req, = opener.requests
        body = json.loads(req.data)
        self.assertEqual(req.full_url, "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
        self.assertEqual((body["model"], body["max_tokens"]), ("gemini-3.5-flash-lite", 16000))
        self.assertEqual(req.get_header("Authorization"), f"Bearer {GEMINI_KEY}")

    def test_other_on_this_computer_needs_no_key_but_needs_consent(self):
        text = "AI_PROVIDER=other\nAI_BASE_URL=http://localhost:11434/v1\nAI_MODEL=llama3.1\n"
        with self.assertRaises(client.AIError) as caught:
            self.send(Opener(), text, consent=False)
        self.assertIn("consent", str(caught.exception))
        opener = Opener(chat_says(ITEMS))
        self.send(opener, text)(client.request("s", "p", {}))
        req, = opener.requests
        self.assertEqual(req.full_url, "http://localhost:11434/v1/chat/completions")
        self.assertNotIn("authorization", {k.lower() for k in req.headers})
        self.assertTrue(client.available(self.env))

    def test_other_needs_an_address_a_model_and_https_unless_local(self):
        for text, word in (("AI_PROVIDER=other\nAI_MODEL=m\n", "address"),
                           ("AI_PROVIDER=other\nAI_BASE_URL=http://localhost:11434/v1\n", "model"),
                           ("AI_PROVIDER=other\nAI_BASE_URL=http://ai.example.com/v1\nAI_MODEL=m\nAI_API_KEY=k1234\n", "https"),
                           ("AI_PROVIDER=other\nAI_BASE_URL=https://ai.example.com/v1\nAI_MODEL=m\n", "no API key")):
            with self.assertRaises(client.AIError, msg=text) as caught:
                self.send(Opener(), text)
            self.assertIn(word, str(caught.exception))

    def test_a_key_for_one_provider_is_never_used_for_another(self):
        for text in (f"AI_PROVIDER=openai\nANTHROPIC_API_KEY={KEY_C}\n",
                     f"AI_PROVIDER=gemini\nOPENAI_API_KEY={OPENAI_KEY}\nAI_API_KEY=x1234\n"):
            with self.assertRaises(client.AIError) as caught:
                self.send(Opener(), text)
            self.assertIn("no API key", str(caught.exception))
        # a leftover AI_BASE_URL does not redirect OpenAI's key
        opener = Opener(chat_says(ITEMS))
        self.send(opener, f"AI_PROVIDER=openai\nOPENAI_API_KEY={OPENAI_KEY}\nAI_BASE_URL=https://evil.example/v1\n")(client.request("s", "p", {}))
        self.assertTrue(opener.requests[0].full_url.startswith("https://api.openai.com/"))

    def test_json_schema_unsupported_falls_back_to_json_mode_then_plain_and_remembers(self):
        opener = Opener(bad_request(), bad_request(), chat_says(ITEMS), chat_says(ITEMS))
        send = self.send(opener, f"AI_PROVIDER=openai\nOPENAI_API_KEY={OPENAI_KEY}\n")
        schema = {"type": "object", "properties": {"items": {}}}
        self.assertEqual(send(client.request("sys", "p", schema)), ITEMS)
        formats = [json.loads(r.data).get("response_format", {}).get("type") for r in opener.requests]
        self.assertEqual(formats, ["json_schema", "json_object", None])
        plain = json.loads(opener.requests[2].data)
        self.assertIn("JSON", plain["messages"][0]["content"])  # the schema is asked for in words
        self.assertIn('"items"', plain["messages"][0]["content"])
        send(client.request("sys", "p", schema))  # the next call starts where the last one ended
        self.assertEqual(json.loads(opener.requests[3].data).get("response_format"), None)

    def test_other_errors_are_not_mistaken_for_missing_json_support(self):
        opener = Opener(bad_request(404, "model not found"))
        send = self.send(opener, f"AI_PROVIDER=openai\nOPENAI_API_KEY={OPENAI_KEY}\n")
        with self.assertRaises(client.AIError) as caught:
            send(client.request("s", "p", {}))
        self.assertEqual((caught.exception.status, len(opener.requests)), (404, 1))

    def test_one_retry_on_busy_and_no_key_in_errors(self):
        text = f"AI_PROVIDER=openai\nOPENAI_API_KEY={OPENAI_KEY}\n"
        self.assertEqual(self.send(Opener(bad_request(529, "busy"), chat_says({"ok": 1})), text)(client.request("s", "p", {})), {"ok": 1})
        opener = Opener(bad_request(429, "slow down"), bad_request(429, "slow down"))
        with self.assertRaises(client.AIError) as caught:
            self.send(opener, text)(client.request("s", "p", {}))
        self.assertEqual(len(opener.requests), 2)
        opener = Opener(bad_request(401, f"Incorrect API key provided: {OPENAI_KEY}"))
        with self.assertRaises(client.AIError) as caught:
            self.send(opener, text)(client.request("s", "p", {}))
        self.assertEqual(len(opener.requests), 1)
        self.assertNotIn(OPENAI_KEY, str(caught.exception))
        self.assertIn("OpenAI's API answered 401", str(caught.exception))

    def test_answers_that_are_not_usable(self):
        for response, word in ((chat_says("{}", finish="length"), "cut off"), (chat_says("", refusal="I can't"), "declined"),
                               (chat_says("{}", finish="content_filter"), "declined"), (chat_says("no json here"), "not JSON"),
                               (chat_says("[1, 2]"), "not a JSON object"), ({"choices": []}, "no message"),
                               ({"choices": [{"finish_reason": "stop", "message": {"content": None}}]}, "not text")):
            with self.assertRaises(client.AIError, msg=response) as caught:
                client.chat_answer(response)
            self.assertIn(word, str(caught.exception))

    def test_a_fenced_or_chatty_answer_is_still_read(self):
        for text in ("```json\n" + json.dumps(ITEMS) + "\n```", "Sure! Here you go: " + json.dumps(ITEMS) + " Hope it helps."):
            self.assertEqual(client.chat_answer(chat_says(text)), ITEMS)

    def test_malformed_output_leaves_the_line_unread(self):
        for answer in (chat_says("I could not read these"), chat_says({"rows": []}), chat_says({"items": "MEENA"}),
                       chat_says(ITEMS, finish="length"), chat_says({"items": [{"i": "0", "payer_name": "MEENA IYER"}]})):
            send = self.send(Opener(answer), f"AI_PROVIDER=openai\nOPENAI_API_KEY={OPENAI_KEY}\n")
            out, stats = narration.extract([LINE], send, self.data / "c.json")
            self.assertEqual((out, stats["unread"]), ({}, 1))
        # a name the model invents is dropped here as with Claude
        send = self.send(Opener(chat_says({"items": [{**ITEMS["items"][0], "payer_name": "RAJESH KUMAR"}]})),
                         f"AI_PROVIDER=openai\nOPENAI_API_KEY={OPENAI_KEY}\n")
        out, _ = narration.extract([LINE], send, self.data / "c2.json")
        self.assertEqual(out[LINE]["payer_name"], "")

    def test_claude_is_unchanged(self):
        opener = Opener(model_says({"items": []}))
        send = self.send(opener, "ANTHROPIC_API_KEY=sk-ant-x1234\n")
        send(client.request("s", "p", {}))
        req, = opener.requests
        self.assertEqual(req.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(req.get_header("X-api-key"), "sk-ant-x1234")
        self.assertEqual(send.scope, "claude:claude-haiku-5-5")


KEY_C = "sk-ant-test-NOT-A-REAL-KEY-0001"


class CacheAndConsentTest(Setup):
    def test_a_cache_is_per_provider_and_model(self):
        cache = self.data / "ai_cache.json"
        first = FakeSend(ITEMS)
        first.scope = "openai:gpt-6.1-sol"
        narration.extract([LINE], first, cache)
        for scope, calls in (("openai:gpt-6.1-sol", 1), ("openai:gpt-6-luna", 2), ("gemini:gemini-3.8-flash", 3),
                             ("other:llama3.1@http://localhost:11434/v1", 4)):
            send = FakeSend(ITEMS)
            send.scope = scope
            out, stats = narration.extract([LINE], send, cache)
            self.assertEqual(stats["from_cache"], 0 if calls > 1 else 1, scope)
            self.assertEqual(out[LINE]["payer_name"], "MEENA IYER") if calls == 1 else None
        again = FakeSend()
        again.scope = "openai:gpt-6-luna"
        self.assertEqual(narration.extract([LINE], again, cache)[1]["from_cache"], 1)  # its own entry is found

    def test_an_old_unscoped_entry_is_a_miss_not_an_error(self):
        cache = self.data / "ai_cache.json"
        cache.write_text(json.dumps({narration.key(LINE): {"payer_name": "MEENA IYER", "channel": "", "reference": "", "loan_account": ""},
                                     "junk": 5}))
        send = FakeSend(ITEMS)
        send.scope = "claude:claude-haiku-5-5"
        out, stats = narration.extract([LINE], send, cache)
        self.assertEqual((stats["from_cache"], stats["sent"]), (0, 1))
        self.assertEqual(out[LINE]["payer_name"], "MEENA IYER")

    def test_consent_is_asked_again_when_the_provider_changes(self):
        cfg = lambda text: client.config(client.read_env(self._env(text)))  # noqa: E731
        claude, openai = cfg("ANTHROPIC_API_KEY=k1234\n"), cfg("AI_PROVIDER=openai\nAI_API_KEY=k1234\n")
        gemini = cfg("AI_PROVIDER=gemini\nAI_API_KEY=k1234\n")
        self.assertFalse(client.consent_given(self.data, claude))
        client.record_consent(self.data, claude)
        self.assertEqual([client.consent_given(self.data, c) for c in (claude, openai, gemini)], [True, False, False])
        client.record_consent(self.data, openai)
        self.assertEqual([client.consent_given(self.data, c) for c in (claude, openai, gemini)], [False, True, False])
        self.assertEqual(json.loads((self.data / "ai_consent.json").read_text())["provider"], "openai")
        # "other": a new address is a new recipient
        here = cfg("AI_PROVIDER=other\nAI_BASE_URL=http://localhost:11434/v1\nAI_MODEL=m\n")
        there = cfg("AI_PROVIDER=other\nAI_BASE_URL=https://ai.example.com/v1\nAI_MODEL=m\nAI_API_KEY=k1234\n")
        client.record_consent(self.data, here)
        self.assertEqual([client.consent_given(self.data, c) for c in (here, there)], [True, False])

    def test_a_consent_file_from_before_providers_still_counts_for_claude_only(self):
        (self.data / "ai_consent.json").write_text(json.dumps({"text": client.CONSENT, "at": "2026-11-17T10:00:00"}))
        self.assertTrue(client.consent_given(self.data))
        self.assertFalse(client.consent_given(self.data, client.config({"AI_PROVIDER": "openai"})))

    def _env(self, text):
        self.env.write_text(text)
        return self.env

    def test_the_consent_text_names_the_provider_and_model(self):
        text = client.consent_text(client.config({"AI_PROVIDER": "gemini", "AI_MODEL": "gemini-3.6-flash"}))
        self.assertIn("Google's Gemini API", text)
        self.assertIn("gemini-3.6-flash", text)
        text = client.consent_text(client.config({"AI_PROVIDER": "openai"}))
        self.assertIn("OpenAI", text)
        self.assertIn("gpt-6.1-sol", text)
        local = client.consent_text(client.config({"AI_PROVIDER": "other", "AI_BASE_URL": "http://localhost:11434/v1", "AI_MODEL": "llama3.1"}))
        self.assertIn("nothing leaves", local)
        self.assertNotIn("nothing leaves", client.consent_text(client.config(
            {"AI_PROVIDER": "other", "AI_BASE_URL": "https://ai.example.com/v1", "AI_MODEL": "m"})))
        self.assertEqual(client.consent_text(client.config({})), client.CONSENT)


class ProviderPageTest(Page):
    """The Connect tab's side: settings, the Test AI button, and keys that never come back."""

    def setUp(self):
        super().setUp()
        self.keys = [OPENAI_KEY, GEMINI_KEY, OTHER_KEY, KEY_C]

    def everything(self):
        text = ""
        for path in ("/api/settings", "/api/run", "/api/state", "/"):
            status, body = self.request("GET", path)
            text += body
        return text + "\n".join(self.logged) + self.out.getvalue()

    def use(self, provider, key="", model="", base=""):
        values = {"AI_PROVIDER": provider, "AI_MODEL": model, "AI_BASE_URL": base, "AI_API_KEY": key}
        result = self.post("/api/settings", {"values": values})
        self.assertNotIn("problem", result, result)
        return result

    def test_the_one_key_box_saves_under_the_providers_own_name(self):
        self.use("openai", OPENAI_KEY)
        text = self.env.read_text()
        self.assertIn(f"OPENAI_API_KEY={OPENAI_KEY}", text)
        self.assertNotIn("ANTHROPIC_API_KEY", text)
        self.use("claude", KEY_C)
        self.assertIn(f"ANTHROPIC_API_KEY={KEY_C}", self.env.read_text())
        self.assertIn(f"OPENAI_API_KEY={OPENAI_KEY}", self.env.read_text())  # the other provider's key is kept apart, not reused
        view = self.post("/api/settings", {"values": {"AI_PROVIDER": "gemini"}})["ai"]
        self.assertFalse(view["key"])  # no Gemini key yet: the OpenAI one does not count
        self.post("/api/settings", {"forget": ["AI_API_KEY"]})  # forgets the active provider's key (Gemini: none saved)
        self.assertIn(f"OPENAI_API_KEY={OPENAI_KEY}", self.env.read_text())
        self.use("openai")
        self.post("/api/settings", {"forget": ["AI_API_KEY"]})
        self.assertNotIn("OPENAI_API_KEY", self.env.read_text())

    def test_bad_provider_model_and_address_are_refused(self):
        before = self.env.read_bytes()
        for values in ({"AI_PROVIDER": "bard"}, {"AI_PROVIDER": "claude", "AI_MODEL": "gpt-6-luna"},
                       {"AI_PROVIDER": "openai", "AI_MODEL": "a b"}, {"AI_BASE_URL": "ftp://x"}, {"AI_API_KEY": "two words"}):
            self.assertIn("problem", self.post("/api/settings", {"values": values}), values)
        self.assertEqual(self.env.read_bytes(), before)

    def test_the_view_lists_providers_models_and_what_is_unverified(self):
        ai = self.get("/api/settings")["ai"]
        self.assertEqual(list(ai["providers"]), ["claude", "openai", "gemini", "other"])
        self.assertIn("gpt-6.1-sol", ai["providers"]["openai"]["models"])
        self.assertIn("UNVERIFIED", ai["providers"]["openai"]["verified"])
        self.assertEqual(ai["provider"], "claude")

    def test_test_ai_needs_consent_and_sends_no_statement_data(self):
        self.use("openai", OPENAI_KEY)
        result = self.post("/api/testai")
        self.assertFalse(result["ok"])
        self.assertIn("consent", result["detail"])
        self.assertEqual(self.opener.requests, [])  # nothing was sent without the tick
        self.assertTrue(self.post("/api/consent", {"agree": True})["ai"]["consent"])
        self.opener.answers.append(chat_says({"ok": True}))
        (self.data / "bank_export.csv").write_text("Date,Narration\n05/09/26,SECRET NARRATION OF RAMESH KUMAR 8,450.00\n")
        result = self.post("/api/testai")
        self.assertTrue(result["ok"], result)
        self.assertIn("gpt-6.1-sol", result["detail"])
        sent = self.opener.requests[0].data.decode()
        for private in ("RAMESH", "SECRET NARRATION", "8,450", "MIFOSX", OPENAI_KEY, "Date,Narration"):
            self.assertNotIn(private, sent)
        self.assertEqual(len(self.opener.requests), 1)

    def test_test_ai_explains_each_failure_with_a_fix(self):
        self.use("openai", OPENAI_KEY)
        self.post("/api/consent", {"agree": True})
        for error, word in ((bad_request(401, "bad key"), "key"), (bad_request(404, "no such model"), "model name"),
                            (urllib.error.URLError("refused"), "running"), (bad_request(429, "quota"), "billing")):
            self.opener.answers.append(error)
            if getattr(error, "code", 0) in (429,):
                self.opener.answers.append(error)
            result = self.post("/api/testai")
            self.assertFalse(result["ok"])
            self.assertIn(word, result["fix"])
        self.opener.answers.append(chat_says("no json"))
        self.assertIn("another model", self.post("/api/testai")["fix"])

    def test_test_ai_for_a_local_model_needs_no_key(self):
        self.use("other", "", "llama3.1", "http://localhost:11434/v1")
        ai = self.get("/api/settings")["ai"]
        self.assertEqual((ai["ready"], ai["local"], ai["key"]), (True, True, False))
        self.assertIn("nothing leaves", ai["consent_text"])
        self.assertIn("consent", self.post("/api/testai")["detail"])
        self.post("/api/consent", {"agree": True})
        self.opener.answers.append(chat_says({"ok": True}))
        self.assertTrue(self.post("/api/testai")["ok"])
        self.assertEqual(self.opener.requests[0].full_url, "http://localhost:11434/v1/chat/completions")

    def test_switching_provider_asks_for_consent_again(self):
        self.use("openai", OPENAI_KEY)
        self.assertTrue(self.post("/api/consent", {"agree": True})["ai"]["consent"])
        self.assertFalse(self.use("gemini", GEMINI_KEY)["ai"]["consent"])
        self.assertFalse(self.post("/api/testai")["ok"])
        self.assertTrue(self.use("openai")["ai"]["consent"])  # back to the one already agreed to

    def test_no_key_appears_in_any_answer_or_log_for_any_provider(self):
        self.opener.answers.extend([chat_says({"ok": True})] * 3)
        for provider, key, model, base in (("openai", OPENAI_KEY, "", ""), ("gemini", GEMINI_KEY, "", ""),
                                           ("other", OTHER_KEY, "m", "https://ai.example.com/v1"), ("claude", KEY_C, "", "")):
            self.use(provider, key, model, base)
            self.post("/api/consent", {"agree": True})
            self.opener.answers.append(bad_request(401, f"bad key {key}") if provider != "claude" else chat_says("x"))
            result = self.post("/api/testai")
            self.assertNotIn(key, json.dumps(result))
            self.assertNotIn(key, self.everything())
        for key in self.keys:
            self.assertNotIn(key, self.everything())
        self.assertTrue(self.get("/api/settings")["ai"]["key"])

    def test_without_a_key_the_match_is_byte_for_byte_what_it_was(self):
        sha = lambda: hashlib.sha256((self.data / "decisions.csv").read_bytes()).hexdigest()  # noqa: E731
        self.post("/api/match", {"bank": "synthetic"})  # the page's demo statement, before any AI setting exists
        expected = sha()
        (self.data / "decisions.csv").unlink()
        for provider in ("claude", "openai", "gemini", "other"):
            self.use(provider)
            self.post("/api/match", {"bank": "synthetic", "ai": True})
            self.assertEqual(sha(), expected, provider)
        self.assertEqual(self.opener.requests, [])
        self.assertFalse(client.available(self.env))


if __name__ == "__main__":
    unittest.main()
