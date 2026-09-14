"""Tool registry with timing, error capture and guardrail hooks."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from starter.tools.base import Tool, ToolError, ToolResult


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, *tools: Tool) -> ToolRegistry:
        for t in tools:
            self.tools[t.name] = t
        return self

    def get(self, name: str) -> Tool:
        if name not in self.tools:
            raise ToolError(f"unknown tool {name!r}; available: {sorted(self.tools)}")
        return self.tools[name]

    def schemas(self) -> list[dict[str, Any]]:
        return [t.as_openai_schema() for t in self.tools.values()]

    def names(self) -> list[str]:
        return sorted(self.tools)

    def invoke(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        started = time.perf_counter()
        try:
            tool = self.get(name)
            content = tool.run(**(args or {}))
            ok = True
        except ToolError as exc:
            content, ok = f"tool error: {exc}", False
        except Exception as exc:  # a tool bug must not kill the agent loop
            content, ok = f"unhandled tool exception: {type(exc).__name__}: {exc}", False
        return ToolResult(
            tool=name,
            ok=ok,
            content=content,
            duration_ms=(time.perf_counter() - started) * 1000,
        )


def default_registry(extra: Iterable[Tool] = ()) -> ToolRegistry:
    """The placeholder toolset. Replace `search_kb` with your real retrieval."""
    from starter.tools import examples

    return ToolRegistry().add(*examples.TOOLS, *extra)
