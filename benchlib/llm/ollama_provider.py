"""Local Ollama through its native /api/chat endpoint, with the JSON schema in the format field."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchlib.config import LlmProfile
from benchlib.llm.base import DEFAULT_TIMEOUT_SECONDS, LLMError, extract_json_object, json_instruction, post_json, setting


@dataclass
class OllamaProvider:
    base_url: str
    model: str
    log_dir: Path
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    name: str = "ollama"

    @classmethod
    def from_profile(cls, profile: LlmProfile, log_dir: Path, environ: Mapping[str, str]) -> OllamaProvider:
        return cls(
            base_url=setting(profile, "base_url"),
            model=setting(profile, "model"),
            log_dir=log_dir,
            timeout=float(profile.settings.get("timeout", DEFAULT_TIMEOUT_SECONDS)),
        )

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"{system}\n\n{json_instruction(schema)}"},
                {"role": "user", "content": user},
            ],
            "format": schema,
            "stream": False,
            "options": {"temperature": 0},
        }
        reply = post_json(f"{self.base_url.rstrip('/')}/api/chat", body, {}, self.timeout, self.log_dir, self.name)
        try:
            content = reply["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"unexpected reply shape: {str(reply)[:200]}") from exc
        return extract_json_object(str(content))
