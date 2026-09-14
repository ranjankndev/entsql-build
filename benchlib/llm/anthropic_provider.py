"""Anthropic Messages API with structured outputs. The optional anthropic package is imported only for this profile."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchlib.config import LlmProfile
from benchlib.llm.base import DEFAULT_TIMEOUT_SECONDS, LLMError, api_key, extract_json_object, log_exchange, setting

DEFAULT_MAX_TOKENS = 16000
# Server-side refusal fallback: fallbacks = "default" in the profile re-runs a declined request on another model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class AnthropicProvider:
    client: Any
    model: str
    log_dir: Path
    max_tokens: int = DEFAULT_MAX_TOKENS
    effort: str | None = None
    fallbacks: str | None = None
    error_types: tuple[type[BaseException], ...] = field(default=(Exception,))
    name: str = "anthropic"

    @classmethod
    def from_profile(cls, profile: LlmProfile, log_dir: Path, environ: Mapping[str, str]) -> AnthropicProvider:
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("the anthropic package is not installed: .venv/bin/pip install anthropic") from exc
        client = anthropic.Anthropic(
            api_key=api_key(profile, environ, required=True),
            timeout=float(profile.settings.get("timeout", DEFAULT_TIMEOUT_SECONDS)),
        )
        return cls(
            client=client,
            model=setting(profile, "model"),
            log_dir=log_dir,
            max_tokens=int(profile.settings.get("max_tokens", DEFAULT_MAX_TOKENS)),
            effort=profile.settings.get("effort"),
            fallbacks=profile.settings.get("fallbacks"),
            error_types=(anthropic.APIError,),
        )

    def request(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        if self.fallbacks:
            request["betas"] = [FALLBACK_BETA]
            request["fallbacks"] = self.fallbacks
        return request

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        request = self.request(system, user, schema)
        create = self.client.beta.messages.create if self.fallbacks else self.client.messages.create
        started = time.perf_counter()
        try:
            response = create(**request)
        except self.error_types as exc:
            log_exchange(self.log_dir, self.name, request, None, str(exc), time.perf_counter() - started)
            raise LLMError(f"Anthropic API call failed: {exc}") from exc
        raw = response.to_dict() if hasattr(response, "to_dict") else response
        log_exchange(self.log_dir, self.name, request, raw, None, time.perf_counter() - started)
        return reply_json(response)


def reply_json(response: Any) -> dict[str, Any]:
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        raise LLMError(f"the model declined the request (category: {getattr(details, 'category', None)})")
    if response.stop_reason == "max_tokens":
        raise LLMError("the reply was cut off at max_tokens; raise max_tokens in the profile or ask for fewer rows")
    text = next((block.text for block in response.content if block.type == "text"), None)
    if text is None:
        raise LLMError("the reply has no text block")
    return extract_json_object(text)
