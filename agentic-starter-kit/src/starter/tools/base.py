"""Tool contract: a name, a JSON schema, a callable, and a risk level.

`risk` is what the guardrail layer keys off: anything above `read` requires an
explicit allow-list entry and (optionally) a human approval step in the graph.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Risk = Literal["read", "write", "external", "dangerous"]


class ToolError(Exception):
    """Raised by a tool for an expected failure; the agent sees the message."""


@dataclass
class ToolResult:
    tool: str
    ok: bool
    content: str
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]
    risk: Risk = "read"
    requires_approval: bool = False

    def as_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, **kwargs: Any) -> str:
        allowed = set(inspect.signature(self.fn).parameters)
        unexpected = set(kwargs) - allowed
        if unexpected:
            raise ToolError(f"unexpected arguments for {self.name}: {sorted(unexpected)}")
        return str(self.fn(**kwargs))


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    risk: Risk = "read",
    requires_approval: bool = False,
) -> Callable[[Callable[..., Any]], Tool]:
    """Decorator turning a plain function into a `Tool`."""

    def decorator(fn: Callable[..., Any]) -> Tool:
        return Tool(
            name=name,
            description=description,
            parameters=parameters,
            fn=fn,
            risk=risk,
            requires_approval=requires_approval,
        )

    return decorator
