"""Guardrail pipeline: ordered guards per stage, driven by a YAML policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from starter.guardrails import builtin as _builtin  # noqa: F401  (registers built-ins)
from starter.guardrails import judge as _judge  # noqa: F401  (registers model-based guards)
from starter.guardrails.base import Guard, GuardContext, GuardResult, Severity, Stage
from starter.guardrails.registry import build_guard
from starter.settings import Settings, get_settings


class GuardrailViolation(Exception):
    """Raised when a blocking guard fires and fail-mode is `block`."""

    def __init__(self, result: GuardResult, stage: Stage) -> None:
        super().__init__(f"[{stage.value}] {result.guard}: {result.message}")
        self.result = result
        self.stage = stage


@dataclass
class GuardrailOutcome:
    text: str
    results: list[GuardResult] = field(default_factory=list)
    blocked: bool = False
    blocking_result: GuardResult | None = None

    @property
    def violations(self) -> list[GuardResult]:
        return [r for r in self.results if not r.passed]


@dataclass
class GuardrailPipeline:
    guards: list[Guard]
    fail_mode: str = "block"
    block_message: str = "I can't help with that request."

    def for_stage(self, stage: Stage) -> list[Guard]:
        return [g for g in self.guards if stage in g.stages]

    def run(self, text: str, ctx: GuardContext) -> GuardrailOutcome:
        """Run every guard for `ctx.stage` in order, threading transformations."""
        outcome = GuardrailOutcome(text=text)
        current = text
        for guard in self.for_stage(ctx.stage):
            result = guard.check(current, ctx)
            outcome.results.append(result)
            if result.transformed is not None:
                current = result.transformed
            if not result.passed and result.severity is Severity.BLOCK:
                outcome.blocked = True
                outcome.blocking_result = result
                if self.fail_mode == "block":
                    outcome.text = self.block_message
                    raise GuardrailViolation(result, ctx.stage)
                break
        outcome.text = current
        return outcome

    def run_safe(self, text: str, ctx: GuardContext) -> GuardrailOutcome:
        """Like `run` but never raises; returns a blocked outcome instead."""
        try:
            return self.run(text, ctx)
        except GuardrailViolation as exc:
            return GuardrailOutcome(
                text=self.block_message,
                results=[exc.result],
                blocked=True,
                blocking_result=exc.result,
            )


DEFAULT_POLICY: dict[str, Any] = {
    "fail_mode": "block",
    "block_message": "I can't help with that request.",
    "guards": [
        {"name": "max_length", "options": {"max_chars": 16000}},
        {"name": "prompt_injection", "options": {}},
        {"name": "pii", "options": {"action": "redact"}},
        {"name": "secret_leak", "options": {}},
    ],
}


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        return dict(DEFAULT_POLICY)
    policy_path = Path(path)
    if not policy_path.exists():
        return dict(DEFAULT_POLICY)
    loaded = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"guardrail policy must be a mapping: {policy_path}")
    return loaded


def build_pipeline(
    policy_path: str | Path | None = None, settings: Settings | None = None
) -> GuardrailPipeline:
    s = settings or get_settings()
    policy = load_policy(policy_path if policy_path is not None else s.guardrails_policy)
    guards = [
        build_guard(entry["name"], entry.get("options"))
        for entry in policy.get("guards", [])
        if entry.get("enabled", True)
    ]
    return GuardrailPipeline(
        guards=guards,
        fail_mode=policy.get("fail_mode", s.guardrails_fail_mode),
        block_message=policy.get("block_message", DEFAULT_POLICY["block_message"]),
    )
