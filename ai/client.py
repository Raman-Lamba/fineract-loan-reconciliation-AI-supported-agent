"""One call to Claude's Messages API over plain HTTPS (urllib, no SDK), for the AI statement reader.

The reader only reads: its answers fill fields that the matcher's fixed rules then use, and are checked
before that (ai.layout, ai.narration). Nothing here can choose a loan or post.

sender() refuses to make a sender without an API key in .env or without the person's recorded consent,
so no narration leaves this machine before both exist. Tests pass a fake `opener` instead of urlopen.
"""
from __future__ import annotations

import json
import time
import urllib.error
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
CONSENT = ("I understand narration text, which can include people's names, is sent to Anthropic's API")
SECRETS = ("MIFOSX_PASSWORD", "ANTHROPIC_API_KEY")  # .env values never shown, logged or sent to the page


class AIError(Exception):
    """The reader could not answer. The caller falls back to the rules and the queue."""


def read_env(path: Path) -> dict:
    """KEY=value lines of a .env file. Comments and other lines are skipped. A missing file is {}."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and not key.lstrip().startswith("#"):
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def consent_given(data: Path) -> bool:
    try:
        return json.loads((data / "ai_consent.json").read_text()).get("text") == CONSENT
    except (OSError, ValueError, AttributeError):
        return False


def record_consent(data: Path):
    (data / "ai_consent.json").write_text(json.dumps({"text": CONSENT, "at": datetime.now().isoformat(timespec="seconds")}))


def available(env_file: Path) -> bool:
    return bool(read_env(env_file).get("ANTHROPIC_API_KEY"))


def sender(env_file: Path, data: Path, opener=None):
    """send(body) -> the model's JSON answer (a dict). Raises AIError without a key or consent."""
    settings = read_env(env_file)
    key = settings.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise AIError("no Claude API key is saved (Connect tab), so the AI reader is off")
    if not consent_given(data):
        raise AIError("the AI reader needs your consent first: tick the box on the Statement tab")
    model = settings.get("AI_MODEL") or DEFAULT_MODEL
    opener = opener or urllib.request.urlopen

    def send(body: dict) -> dict:
        body = {"model": model, **body}
        headers = {"content-type": "application/json", "x-api-key": key, "anthropic-version": API_VERSION}
        if model in FALLBACK_MODELS:
            body["fallbacks"] = "default"
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        data_out = json.dumps(body).encode("utf-8")
        for attempt in (1, 2):
            try:
                with opener(urllib.request.Request(API_URL, data_out, headers), timeout=TIMEOUT) as response:
                    return answer(json.loads(response.read().decode("utf-8")))
            except urllib.error.HTTPError as error:
                detail = _explain(error)
                error.close()
                if attempt == 1 and (error.code == 429 or error.code >= 500):
                    wait = (error.headers or {}).get("retry-after") or "2"
                    time.sleep(min(float(wait), 10) if wait.replace(".", "", 1).isdigit() else 2)
                    continue
                raise AIError(detail) from None
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                raise AIError(f"could not reach Anthropic's API ({getattr(error, 'reason', error)})") from None
            except ValueError:
                raise AIError("Anthropic's API sent something that is not JSON") from None

    return send


def _explain(error) -> str:
    try:
        message = json.loads(error.read().decode("utf-8"))["error"]["message"]
    except Exception:  # noqa: BLE001  the plain status is enough
        message = error.reason
    hint = {401: " Check the API key on the Connect tab.", 429: " Too many requests: try again in a minute.",
            529: " Anthropic's API is busy: try again later."}.get(error.code, "")
    return f"Anthropic's API answered {error.code}: {message}.{hint}"


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
