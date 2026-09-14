"""SQL dialects. Phase 1 has PostgreSQL only."""

from __future__ import annotations

from benchlib.dialects.base import Dialect
from benchlib.dialects.postgres import PostgresDialect


def get_dialect(name: str) -> Dialect:
    if name == "postgres":
        return PostgresDialect()
    raise ValueError(f"unknown dialect {name!r}; phase 1 supports postgres only")
