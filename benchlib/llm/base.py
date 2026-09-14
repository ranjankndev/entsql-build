"""The LLMProvider interface, the provider factory, request logging and JSON helpers."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from benchlib.config import LlmProfile

DEFAULT_TIMEOUT_SECONDS = 120.0


class LLMError(Exception):
    """The provider could not be configured or called, or its reply was unusable."""


class HTTPStatusError(LLMError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


class LLMProvider(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]: ...


def get_provider(profile: LlmProfile, log_dir: Path, environ: Mapping[str, str] | None = None) -> LLMProvider | None:
    """The provider for an [llm.*] profile, or None for provider "none". Provider modules load only when selected."""
    env = os.environ if environ is None else environ
    if profile.provider == "none":
        return None
    if profile.provider == "openai_compat":
        from benchlib.llm.openai_compat_provider import OpenAICompatProvider

        return OpenAICompatProvider.from_profile(profile, log_dir, env)
    if profile.provider == "ollama":
        from benchlib.llm.ollama_provider import OllamaProvider

        return OllamaProvider.from_profile(profile, log_dir, env)
    if profile.provider == "anthropic":
        from benchlib.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider.from_profile(profile, log_dir, env)
    raise LLMError(f"[llm.{profile.name}]: unknown provider {profile.provider!r}")


def setting(profile: LlmProfile, key: str) -> str:
    value = profile.settings.get(key)
    if value in (None, ""):
        raise LLMError(f"[llm.{profile.name}] is missing {key}")
    return str(value)


def api_key(profile: LlmProfile, environ: Mapping[str, str], required: bool) -> str | None:
    """The key from the environment variable named by api_key_env (loaded from .env by config)."""
    variable = profile.settings.get("api_key_env")
    if not variable:
        if required:
            raise LLMError(f"[llm.{profile.name}] needs api_key_env")
        return None
    key = environ.get(str(variable))
    if not key:
        raise LLMError(f"environment variable {variable} is not set; add it to .env at the repository root")
    return key


def log_exchange(log_dir: Path, provider: str, request: Any, response: Any, error: str | None, elapsed: float) -> Path:
    """Write logs/llm/<timestamp>_<provider>.json with the request and the raw response. Keys are never logged."""
    log_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.UTC)
    path = log_dir / f"{now.strftime('%Y%m%dT%H%M%S_%fZ')}_{provider}.json"
    entry = {
        "time": now.isoformat(),
        "provider": provider,
        "elapsed_seconds": round(elapsed, 3),
        "request": request,
        "response": response,
        "error": error,
    }
    path.write_text(json.dumps(entry, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return path


def post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float, log_dir: Path, provider: str) -> Any:
    """POST a JSON body with urllib, log the exchange, and return the decoded JSON reply."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    logged_request = {"url": url, "body": body}
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        log_exchange(log_dir, provider, logged_request, raw, f"HTTP {exc.code}", time.perf_counter() - started)
        raise HTTPStatusError(exc.code, raw) from exc
    except (urllib.error.URLError, OSError) as exc:
        log_exchange(log_dir, provider, logged_request, None, str(exc), time.perf_counter() - started)
        raise LLMError(f"cannot reach {url}: {exc}") from exc
    log_exchange(log_dir, provider, logged_request, raw, None, time.perf_counter() - started)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"{url} did not return JSON: {raw[:200]!r}") from exc


def extract_json_object(text: str) -> dict[str, Any]:
    """The first JSON object in a reply, tolerating prose or code fences around it."""
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise LLMError(f"no JSON object in the reply: {text[:200]!r}")


def json_instruction(schema: dict[str, Any]) -> str:
    """Prompt text for endpoints that cannot enforce a JSON schema themselves."""
    return "Reply with one JSON object only, no prose and no code fences, matching this JSON schema:\n" + json.dumps(schema)
