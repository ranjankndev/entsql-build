"""Built-in guards.

Deliberately dependency-free and deterministic: regex, length and allow/deny
lists. They are the floor, not the ceiling — add an LLM-judge guard or a vendor
moderation guard by registering another factory (see `docs/guardrails.md`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from starter.guardrails.base import GuardContext, GuardResult, Severity, Stage
from starter.guardrails.registry import register_guard

# --------------------------------------------------------------------------- PII

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone": re.compile(r"\b(?:\+\d{1,3}[ -]?)?(?:\d[ -]?){9,13}\d\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,16}\d\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "ipv4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}


@register_guard("pii")
@dataclass
class PIIGuard:
    """Redacts (default) or blocks recognised personal data."""

    action: str = "redact"  # redact | block
    kinds: tuple[str, ...] = tuple(PII_PATTERNS)
    name: str = "pii"
    stages: tuple[Stage, ...] = (Stage.INPUT, Stage.OUTPUT, Stage.TOOL)

    def check(self, text: str, ctx: GuardContext) -> GuardResult:
        found: dict[str, int] = {}
        redacted = text
        for kind in self.kinds:
            pattern = PII_PATTERNS.get(kind)
            if pattern is None:
                continue
            hits = pattern.findall(text)
            if hits:
                found[kind] = len(hits)
                redacted = pattern.sub(f"[REDACTED_{kind.upper()}]", redacted)
        if not found:
            return GuardResult.ok(self.name)
        if self.action == "block":
            return GuardResult.fail(
                self.name, f"personal data detected: {sorted(found)}", Severity.BLOCK, found=found
            )
        return GuardResult(
            guard=self.name,
            passed=True,
            severity=Severity.WARN,
            message=f"redacted {sorted(found)}",
            transformed=redacted,
            details={"found": found},
        )


# ------------------------------------------------------------------ prompt injection

INJECTION_PATTERNS: tuple[str, ...] = (
    r"ignore (?:all |any )?(?:previous|prior|above) instructions",
    r"disregard (?:the )?(?:system|previous) (?:prompt|instructions)",
    r"reveal (?:your )?(?:system prompt|instructions|hidden rules)",
    r"you are now (?:in )?(?:developer|dan|god) mode",
    r"print (?:your )?(?:api[_ ]?key|secret|credentials)",
    r"</?(?:system|instructions)>",
)


@register_guard("prompt_injection")
@dataclass
class PromptInjectionGuard:
    """Heuristic jailbreak/injection detector for user text and tool output."""

    extra_patterns: tuple[str, ...] = ()
    severity: Severity = Severity.BLOCK
    name: str = "prompt_injection"
    stages: tuple[Stage, ...] = (Stage.INPUT, Stage.TOOL)

    def __post_init__(self) -> None:
        self._compiled = [
            re.compile(p, re.IGNORECASE) for p in (*INJECTION_PATTERNS, *self.extra_patterns)
        ]

    def check(self, text: str, ctx: GuardContext) -> GuardResult:
        for pattern in self._compiled:
            if pattern.search(text):
                return GuardResult.fail(
                    self.name,
                    "possible prompt-injection attempt",
                    self.severity,
                    pattern=pattern.pattern,
                )
        return GuardResult.ok(self.name)


# ------------------------------------------------------------------------ topics


@register_guard("denied_topics")
@dataclass
class DeniedTopicsGuard:
    """Keyword deny-list. Cheap first pass before any model-based classifier."""

    topics: dict[str, list[str]] = field(default_factory=dict)
    name: str = "denied_topics"
    stages: tuple[Stage, ...] = (Stage.INPUT, Stage.OUTPUT)

    def check(self, text: str, ctx: GuardContext) -> GuardResult:
        lowered = text.lower()
        for topic, keywords in self.topics.items():
            for keyword in keywords:
                if keyword.lower() in lowered:
                    return GuardResult.fail(
                        self.name, f"denied topic: {topic}", Severity.BLOCK, topic=topic
                    )
        return GuardResult.ok(self.name)


# ------------------------------------------------------------------------ limits


@register_guard("max_length")
@dataclass
class MaxLengthGuard:
    max_chars: int = 16_000
    name: str = "max_length"
    stages: tuple[Stage, ...] = (Stage.INPUT, Stage.TOOL, Stage.OUTPUT)

    def check(self, text: str, ctx: GuardContext) -> GuardResult:
        if len(text) > self.max_chars:
            return GuardResult.fail(
                self.name,
                f"text of {len(text)} chars exceeds limit {self.max_chars}",
                Severity.BLOCK,
                length=len(text),
            )
        return GuardResult.ok(self.name)


@register_guard("secret_leak")
@dataclass
class SecretLeakGuard:
    """Stops credentials the agent may have picked up from ever leaving it."""

    patterns: tuple[str, ...] = (
        r"sk-[A-Za-z0-9]{20,}",
        r"AKIA[0-9A-Z]{16}",
        r"ghp_[A-Za-z0-9]{36}",
        r"(?i)(api[_-]?key|secret|password)\s*[:=]\s*\S{8,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    )
    name: str = "secret_leak"
    stages: tuple[Stage, ...] = (Stage.OUTPUT, Stage.TOOL)

    def __post_init__(self) -> None:
        self._compiled = [re.compile(p) for p in self.patterns]

    def check(self, text: str, ctx: GuardContext) -> GuardResult:
        for pattern in self._compiled:
            if pattern.search(text):
                return GuardResult.fail(
                    self.name, "possible secret in output", Severity.BLOCK, pattern=pattern.pattern
                )
        return GuardResult.ok(self.name)


@register_guard("tool_allowlist")
@dataclass
class ToolAllowlistGuard:
    """Only the named tools may be invoked, whatever the model asks for."""

    allowed: tuple[str, ...] = ()
    name: str = "tool_allowlist"
    stages: tuple[Stage, ...] = (Stage.TOOL,)

    def check(self, text: str, ctx: GuardContext) -> GuardResult:
        tool = ctx.tool_name
        if tool is None or not self.allowed or tool in self.allowed:
            return GuardResult.ok(self.name)
        return GuardResult.fail(
            self.name, f"tool {tool!r} is not allow-listed", Severity.BLOCK, tool=tool
        )


@register_guard("required_disclaimer")
@dataclass
class RequiredDisclaimerGuard:
    """Appends a mandatory notice (regulated domains) if it is missing."""

    text_to_append: str = ""
    name: str = "required_disclaimer"
    stages: tuple[Stage, ...] = (Stage.OUTPUT,)

    def check(self, text: str, ctx: GuardContext) -> GuardResult:
        if not self.text_to_append or self.text_to_append in text:
            return GuardResult.ok(self.name)
        return GuardResult(
            guard=self.name,
            passed=True,
            severity=Severity.INFO,
            message="disclaimer appended",
            transformed=f"{text.rstrip()}\n\n{self.text_to_append}",
        )


def all_builtin_names() -> Iterable[str]:
    return (
        "pii",
        "prompt_injection",
        "denied_topics",
        "max_length",
        "secret_leak",
        "tool_allowlist",
        "required_disclaimer",
    )
