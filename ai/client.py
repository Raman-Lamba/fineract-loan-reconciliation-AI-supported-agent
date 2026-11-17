"""One call to an AI service over plain HTTPS (urllib, no SDK), for the AI statement reader.

Two adapters carry every provider: Claude's Messages API, and the OpenAI-style chat completions API that
OpenAI, Gemini (its OpenAI-compatible endpoint) and "other" (any such service, a local Ollama included) all
speak. Only the transport differs: callers build one request (request()) and get back the model's JSON
answer, and every check on that answer (ai.layout, ai.narration) is the same whoever wrote it.

The reader only reads: its answers fill fields that the matcher's fixed rules then use, and are checked
before that. Nothing here can choose a loan or post.

sender() refuses to make a sender without a key (a local "other" service needs none) or without the
person's recorded consent for this provider, so no narration leaves this machine before both exist. Tests
pass a fake `opener` instead of urlopen.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"
# The claude-api skill's default model (Nov 2026). Thinking is always on for it; effort "low" keeps an
# extraction cheap. AI_MODEL in .env picks another (claude-haiku-5-5 costs about a fortieth as much).
DEFAULT_MODEL = "claude-opus-5-5"
# Models that accept server-side refusal fallbacks; for others the parameter is left out.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5-2"}
TIMEOUT = 120  # seconds per attempt
CONSENT = ("I understand narration text, which can include people's names, is sent to Anthropic's API")  # Claude's text, as before
SECRETS = ("MIFOSX_PASSWORD", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "AI_API_KEY")  # .env values never shown, logged or sent to the page

# Presets. "verified" says what was read in the provider's own docs on 2026-11-17 (a page fetch, not a live
# call); anything else is marked unverified in the UI and README too. Claude's ids come from the claude-api
# skill used for the first build and were not re-checked.
PROVIDERS = {
    "claude": {"name": "Anthropic (Claude)", "label": "Anthropic's API", "model": DEFAULT_MODEL,
               "models": [DEFAULT_MODEL, "claude-sonnet-5-5", "claude-haiku-5-5"], "key": "ANTHROPIC_API_KEY",
               "verified": "Model ids and request shape carried over from the first build (claude-api skill); not re-checked."},
    "openai": {"name": "OpenAI (ChatGPT)", "label": "OpenAI's API", "base": "https://api.openai.com/v1",
               "model": "gpt-6.1-sol", "models": ["gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna"],
               "key": "OPENAI_API_KEY", "tokens": "max_completion_tokens",
               "verified": "Model ids read on OpenAI's models page; Chat Completions structured outputs confirmed there, but its "
                           "exact request fields, and max_completion_tokens, are UNVERIFIED (the fallbacks cover a mismatch)."},
    "gemini": {"name": "Google (Gemini)", "label": "Google's Gemini API",
               "base": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "model": "gemini-3.8-flash",
               "models": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-4-hydrogen"],
               "key": "GEMINI_API_KEY",
               "verified": "Base URL and model ids read in Google's docs; the docs show structured output through the OpenAI "
                           "library but not the raw json_schema field, so that is UNVERIFIED (the fallbacks cover it)."},
    "other": {"name": "Other (OpenAI-compatible, e.g. Ollama)", "label": "the AI service", "base": "",
              "model": "", "models": ["llama3.1", "qwen2.5", "mistral"], "key": "AI_API_KEY",
              "verified": "Any service that speaks OpenAI chat completions. The model names listed are examples (UNVERIFIED): use "
                          "the exact name your service shows."},
}
JSON_FORMATS = ("json_schema", "json_object", "plain")  # strictest first; a 400/422 steps down


class AIError(Exception):
    """The reader could not answer. The caller falls back to the rules and the queue."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status  # HTTP status, 0 if the service was not reached, None if not an HTTP failure


def read_env(path: Path) -> dict:
    """KEY=value lines of a .env file. Comments and other lines are skipped. A missing file is {}."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and not key.lstrip().startswith("#"):
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_local(base: str) -> bool:
    return (urllib.parse.urlparse(base).hostname or "") in ("localhost", "127.0.0.1", "::1")


def config(settings: dict) -> dict:
    """The provider setup in a .env's values: provider, model, base URL, key, and what to call it. Raises AIError."""
    provider = settings.get("AI_PROVIDER") or "claude"
    if provider not in PROVIDERS:
        raise AIError(f"AI_PROVIDER '{provider}' is not one of {', '.join(PROVIDERS)}")
    preset = PROVIDERS[provider]
    # AI_BASE_URL only ever applies to "other": a leftover address must never receive an OpenAI or Gemini key.
    base = (settings.get("AI_BASE_URL", "") if provider == "other" else preset.get("base", "")).strip()
    local = provider == "other" and is_local(base)
    host = urllib.parse.urlparse(base).netloc if provider == "other" else provider
    label = f"the AI service at {host}" if provider == "other" and host else preset["label"]
    return {"provider": provider, "name": preset["name"], "label": label, "model": settings.get("AI_MODEL") or preset["model"],
            "base": base, "key": settings.get(preset["key"], ""), "local": local, "dest": host, "preset": preset}


def consent_text(cfg: dict) -> str:
    """What the person agrees to, naming the provider and model (and saying so when nothing leaves this computer)."""
    if cfg["provider"] == "claude":
        return CONSENT
    if cfg["local"]:
        return (f"I understand narration text, which can include people's names, is sent to the model {cfg['model'] or '(none chosen)'} "
                f"at {cfg['base']}, which runs on this computer: nothing leaves it")
    return (f"I understand narration text, which can include people's names, is sent to {cfg['label']} "
            f"(model {cfg['model'] or 'none chosen'})")


def consent_given(data: Path, cfg: dict | None = None) -> bool:
    """True if the person agreed to this provider (for "other", this address). A change of either asks again."""
    cfg = cfg or config({})
    try:
        saved = json.loads((data / "ai_consent.json").read_text())
        provider = saved.get("provider") or ("claude" if saved.get("text") == CONSENT else None)  # a file from before providers
        return provider == cfg["provider"] and (provider != "other" or saved.get("dest") == cfg["dest"])
    except (OSError, ValueError, AttributeError):
        return False


def record_consent(data: Path, cfg: dict | None = None):
    cfg = cfg or config({})
    (data / "ai_consent.json").write_text(json.dumps({"text": consent_text(cfg), "provider": cfg["provider"], "dest": cfg["dest"],
                                                      "model": cfg["model"], "at": datetime.now().isoformat(timespec="seconds")}))


def available(env_file: Path) -> bool:
    """True if the reader could run once consent is given: a key, or a local "other" service that needs none."""
    try:
        return available_for(config(read_env(env_file)))
    except AIError:
        return False


def available_for(cfg: dict) -> bool:
    return bool(cfg["key"]) or (cfg["local"] and bool(cfg["model"]))


def sender(env_file: Path, data: Path, opener=None):
    """send(body) -> the model's JSON answer (a dict). Raises AIError without a key or consent.

    send.scope names provider + model (+ address): the caches key on it, so another model never reuses an answer.
    """
    cfg = config(read_env(env_file))
    opener = opener or urllib.request.urlopen
    if cfg["provider"] != "claude":
        if not cfg["base"]:
            raise AIError("no address is saved for the other AI service (Connect tab), so the AI reader is off")
        if not cfg["model"]:
            raise AIError("no model name is saved (Connect tab), so the AI reader is off")
        if not cfg["base"].startswith(("https://", "http://")) or (not cfg["local"] and not cfg["base"].startswith("https://")):
            raise AIError("the AI service address must start with https:// (http:// only for a service on this computer)")
    if not cfg["key"] and not cfg["local"]:
        raise AIError("no Claude API key is saved (Connect tab), so the AI reader is off" if cfg["provider"] == "claude"
                      else f"no API key is saved for {cfg['name']} (Connect tab), so the AI reader is off")
    if not consent_given(data, cfg):
        raise AIError("the AI reader needs your consent first: tick the box on the Statement tab")
    send = _claude(cfg, opener) if cfg["provider"] == "claude" else _chat(cfg, opener)
    send.scope = f"{cfg['provider']}:{cfg['model']}" + (f"@{cfg['base']}" if cfg["provider"] == "other" else "")
    return send


def _post(opener, url: str, headers: dict, payload: dict, label: str, secret: str = "") -> dict:
    """POST JSON, return the parsed answer. One retry on 429/5xx; every failure is an AIError saying what to do."""
    data_out = json.dumps(payload).encode("utf-8")
    for attempt in (1, 2):
        try:
            with opener(urllib.request.Request(url, data_out, headers), timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = _explain(error, label, secret)
            code, wait = error.code, (error.headers or {}).get("retry-after") or "2"
            error.close()
            if attempt == 1 and (code == 429 or code >= 500):
                time.sleep(min(float(wait), 10) if wait.replace(".", "", 1).isdigit() else 2)
                continue
            raise AIError(detail, code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise AIError(f"could not reach {label} ({getattr(error, 'reason', error)})", 0) from None
        except ValueError:
            raise AIError(f"{label} sent something that is not JSON") from None


def _claude(cfg: dict, opener):
    model, key = cfg["model"], cfg["key"]

    def send(body: dict) -> dict:
        body = {"model": model, **body}
        headers = {"content-type": "application/json", "x-api-key": key, "anthropic-version": API_VERSION}
        if model in FALLBACK_MODELS:
            body["fallbacks"] = "default"
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        return answer(_post(opener, API_URL, headers, body, cfg["label"], key))

    return send


def _chat(cfg: dict, opener):
    """OpenAI-style chat completions. Asks for a json_schema, then JSON mode, then plain "reply with JSON only"."""
    url = cfg["base"].rstrip("/") + "/chat/completions"
    headers = {"content-type": "application/json", **({"authorization": f"Bearer {cfg['key']}"} if cfg["key"] else {})}
    tokens = cfg["preset"].get("tokens", "max_tokens")
    step = [0]  # shortcut: the step-down is remembered for this sender only; a service that gains json_schema is noticed next run

    def send(body: dict) -> dict:
        schema = body.get("output_config", {}).get("format", {}).get("schema", {})
        while True:
            fmt = JSON_FORMATS[step[0]]
            system = body.get("system", "")
            if fmt != "json_schema":  # the schema is not enforced by the service, so say it in words
                system += "\nReply with one JSON object and nothing else, matching this JSON schema: " + json.dumps(schema)
            payload = {"model": cfg["model"], tokens: body.get("max_tokens", 4000),
                       "messages": [{"role": "system", "content": system}, *body["messages"]]}
            if fmt == "json_schema":
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}}
            elif fmt == "json_object":
                payload["response_format"] = {"type": "json_object"}
            try:
                return chat_answer(_post(opener, url, headers, payload, cfg["label"], cfg["key"]))
            except AIError as problem:
                if problem.status in (400, 422) and step[0] < len(JSON_FORMATS) - 1:
                    step[0] += 1
                    continue
                raise

    return send


def _explain(error, label: str = "Anthropic's API", secret: str = "") -> str:
    try:
        message = json.loads(error.read().decode("utf-8"))["error"]["message"]
    except Exception:  # noqa: BLE001  the plain status is enough
        message = error.reason
    message = str(message).replace(secret, "***") if secret else str(message)
    hint = {401: " Check the API key on the Connect tab.", 429: " Too many requests: try again in a minute.",
            529: f" {label[0].upper()}{label[1:]} is busy: try again later."}.get(error.code, "")
    return f"{label} answered {error.code}: {message}.{hint}"


def answer(response: dict) -> dict:
    """The JSON object in a Messages API response's text, or AIError. Never trusts a cut-off answer."""
    stop = response.get("stop_reason")
    if stop == "refusal":
        raise AIError("the model declined to read this text")
    if stop != "end_turn":
        raise AIError(f"the model's answer was cut off ({stop})")
    text = "".join(block.get("text", "") for block in response.get("content", []) if block.get("type") == "text")
    try:
        parsed = json.loads(text)
    except ValueError:
        raise AIError("the model's answer was not JSON") from None
    if not isinstance(parsed, dict):
        raise AIError("the model's answer was not a JSON object")
    return parsed


def request(system: str, prompt: str, schema: dict, max_tokens: int = 16000) -> dict:
    """A Messages API body (without the model) asking for JSON in `schema`."""
    return {"max_tokens": max_tokens, "system": system,
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": prompt}]}


def chat_answer(response) -> dict:
    """The JSON object in a chat completions response, or AIError. Never trusts a cut-off or refused answer."""
    try:
        choice = response["choices"][0]
        message, finish = choice["message"], choice.get("finish_reason")
    except (KeyError, IndexError, TypeError):
        raise AIError("the AI service's answer had no message") from None
    if message.get("refusal") or finish == "content_filter":
        raise AIError("the model declined to read this text")
    if finish != "stop":
        raise AIError(f"the model's answer was cut off ({finish})")
    text = message.get("content")
    if not isinstance(text, str):
        raise AIError("the model's answer was not text")
    # Weaker models wrap the JSON in a code fence or a sentence; take the outermost braces. The result is
    # checked exactly like any other answer, so leniency here cannot put a wrong value anywhere.
    text = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip()).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        try:
            parsed = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except ValueError:
            raise AIError("the model's answer was not JSON") from None
    if not isinstance(parsed, dict):
        raise AIError("the model's answer was not a JSON object")
    return parsed


TEST_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}


def try_it(env_file: Path, data: Path, opener=None) -> dict:
    """One tiny real request with a fixed prompt (no statement data) -> {"ok", "detail", "fix"}, in plain English."""
    try:
        cfg = config(read_env(env_file))
        send = sender(env_file, data, opener)
        send(request("You check that a connection works.", 'Reply with {"ok": true}.', TEST_SCHEMA, 2000))
    except AIError as problem:
        return {"ok": False, "detail": str(problem), "fix": _fix(problem, locals().get("cfg"))}
    return {"ok": True, "detail": f"{cfg['name']} answered with model {cfg['model']}.", "fix": ""}


def _fix(problem: AIError, cfg) -> str:
    if cfg is None:
        return "Choose a provider from the list and Save."
    if problem.status in (401, 403):
        return f"The key was refused. Paste the {cfg['name']} key again on the Connect tab and Save."
    if problem.status == 404:
        return ("The address or the model name is wrong. Check the model name; for Other, the address usually ends in /v1 "
                "(Ollama: http://localhost:11434/v1, and the model must be pulled first).")
    if problem.status == 0:
        return "Nothing answered. Check the address and your internet connection; for a local model, check it is running."
    if problem.status == 429:
        return "The service is rate-limiting you or the account has no credit. Wait a minute, or check the account's billing."
    if problem.status is None and ("not JSON" in str(problem) or "no message" in str(problem)):
        return "This model does not follow the JSON instructions well enough. Choose another model."
    return "Read the message above, fix that on the Connect tab and try again."
