"""Guardrail contract.

A guard is a small, pure-ish function over text plus context. It never raises for
a policy hit — it returns a `GuardResult`. The pipeline decides what a hit means.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Stage(StrEnum):
    INPUT = "input"
    TOOL = "tool"
    OUTPUT = "output"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


@dataclass
class GuardContext:
    """Everything a guard may look at besides the text itself."""

    stage: Stage
    thread_id: str = "anonymous"
    user_id: str | None = None
    tool_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GuardResult:
    guard: str
    passed: bool
    severity: Severity = Severity.INFO
    message: str = ""
    # Redacted/rewritten text; None means "leave the text alone".
    transformed: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, guard: str, transformed: str | None = None) -> GuardResult:
        return cls(guard=guard, passed=True, transformed=transformed)

    @classmethod
    def fail(
        cls,
        guard: str,
        message: str,
        severity: Severity = Severity.BLOCK,
        **details: Any,
    ) -> GuardResult:
        return cls(
            guard=guard, passed=False, severity=severity, message=message, details=details
        )


@runtime_checkable
class Guard(Protocol):
    name: str
    stages: tuple[Stage, ...]

    def check(self, text: str, ctx: GuardContext) -> GuardResult: ...
