"""LLM provider seam.

Every model call in the kit goes through `LLMProvider`. Swapping vendors is a
settings change, never a code change. `echo` is the default so the whole kit —
API, evals, tests — runs with no credentials at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from starter.settings import Settings, get_settings


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: Any = None
    usage: dict[str, int] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...


class EchoProvider:
    """Deterministic offline provider.

    Replies with a canned plan/answer derived from the last user message so the
    graph, guardrails, memory and eval harness are all exercisable in CI.
    """

    name = "echo"

    def __init__(self, scripted: list[LLMResponse] | None = None) -> None:
        self._scripted = list(scripted or [])

    def complete(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        if self._scripted:
            return self._scripted.pop(0)
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        return LLMResponse(text=f"[echo] {last_user.strip()}"[:2000], usage={"total_tokens": 0})


class LangChainProvider:
    """Adapter over any `langchain_core` chat model, including tool calling."""

    def __init__(self, model: Any, name: str) -> None:
        self._model = model
        self.name = name

    def complete(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        model = self._model.bind_tools(tools) if tools else self._model
        result = model.invoke(messages)
        calls = [
            {"name": c["name"], "args": c["args"], "id": c.get("id", "")}
            for c in (getattr(result, "tool_calls", None) or [])
        ]
        content = result.content
        text = content if isinstance(content, str) else json.dumps(content)
        usage = getattr(result, "usage_metadata", None) or {}
        return LLMResponse(text=text, tool_calls=calls, raw=result, usage=dict(usage))


def get_llm(settings: Settings | None = None) -> LLMProvider:
    """Build the provider named by `LLM_PROVIDER`. Imports are lazy on purpose."""
    s = settings or get_settings()

    if s.llm_provider == "echo":
        return EchoProvider()

    if s.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=s.llm_model,
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
            api_key=s.openai_api_key,
            base_url=s.openai_base_url,
        )
        return LangChainProvider(model, "openai")

    if s.llm_provider == "azure_openai":
        from langchain_openai import AzureChatOpenAI

        model = AzureChatOpenAI(
            azure_endpoint=s.azure_openai_endpoint,
            azure_deployment=s.azure_openai_deployment,
            api_version=s.azure_openai_api_version,
            api_key=s.azure_openai_api_key,
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
        )
        return LangChainProvider(model, "azure_openai")

    if s.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        model = ChatAnthropic(
            model=s.llm_model,
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
            api_key=s.anthropic_api_key,
        )
        return LangChainProvider(model, "anthropic")

    raise ValueError(f"unknown LLM provider: {s.llm_provider}")
