"""Name -> guard-factory registry, so policy YAML can name guards as strings."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from starter.guardrails.base import Guard

_REGISTRY: dict[str, Callable[..., Guard]] = {}


def register_guard(name: str) -> Callable[[Callable[..., Guard]], Callable[..., Guard]]:
    def decorator(factory: Callable[..., Guard]) -> Callable[..., Guard]:
        if name in _REGISTRY:
            raise ValueError(f"guard already registered: {name}")
        _REGISTRY[name] = factory
        return factory

    return decorator


def registered_guards() -> dict[str, Callable[..., Guard]]:
    return dict(_REGISTRY)


def build_guard(name: str, options: dict[str, Any] | None = None) -> Guard:
    try:
        factory = _REGISTRY[name]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "<none>"
        raise KeyError(f"unknown guard {name!r}; registered: {known}") from exc
    return factory(**(options or {}))
