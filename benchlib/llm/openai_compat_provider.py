"""Any endpoint that speaks the OpenAI chat completions API (OpenRouter, DeepSeek, Groq, vLLM, llama.cpp, ...)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchlib.config import LlmProfile
from benchlib.llm.base import (
    DEFAULT_TIMEOUT_SECONDS,
    HTTPStatusError,
    LLMError,
    api_key,
    extract_json_object,
    json_instruction,
    post_json,
    setting,
)

# Status codes with which endpoints reject an unsupported response_format; we then ask for JSON in the prompt.
SCHEMA_REJECTED_STATUSES = (400, 422)


@dataclass
class OpenAICompatProvider:
    base_url: str
    model: str
    api_key: str | None
    log_dir: Path
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    use_json_schema: bool = True
    name: str = "openai_compat"

    @classmethod
    def from_profile(cls, profile: LlmProfile, log_dir: Path, environ: Mapping[str, str]) -> OpenAICompatProvider:
        return cls(
            base_url=setting(profile, "base_url"),
            model=setting(profile, "model"),
            api_key=api_key(profile, environ, required=False),
            log_dir=log_dir,
            timeout=float(profile.settings.get("timeout", DEFAULT_TIMEOUT_SECONDS)),
            use_json_schema=bool(profile.settings.get("json_schema", True)),
        )

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self.use_json_schema:
            try:
                return self.chat(system, user, response_format(schema))
            except HTTPStatusError as exc:
                if exc.status not in SCHEMA_REJECTED_STATUSES:
                    raise
        return self.chat(f"{system}\n\n{json_instruction(schema)}", user, None)

    def chat(self, system: str, user: str, format_: dict[str, Any] | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if format_ is not None:
            body["response_format"] = format_
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        reply = post_json(f"{self.base_url.rstrip('/')}/chat/completions", body, headers, self.timeout, self.log_dir, self.name)
        try:
            content = reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected reply shape: {str(reply)[:200]}") from exc
        if not isinstance(content, str):
            raise LLMError("the reply has no text content")
        return extract_json_object(content)


def response_format(schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}}
