"""Memory contracts.

Three layers, deliberately separate:

* **working**   – the messages of the current turn, held by the graph state.
* **short term** – the recent turns of this thread, capped and rolled up.
* **long term**  – durable facts scoped to a user or thread, retrieved by query.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Turn:
    role: str  # user | assistant | tool | system
    content: str
    ts: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_message(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class MemoryRecord:
    """A durable fact. `kind` lets you keep profile, preference and episodic
    memories in one store without mixing them at retrieval time."""

    text: str
    kind: str = "fact"
    scope: str = "thread"  # thread | user | global
    key: str | None = None  # stable key makes a write an upsert
    score: float = 0.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MemoryStore(Protocol):
    """Backend seam: in-memory, file, Cosmos DB, Redis, pgvector, ..."""

    def append_turn(self, thread_id: str, turn: Turn) -> None: ...

    def get_turns(self, thread_id: str, limit: int | None = None) -> list[Turn]: ...

    def set_summary(self, thread_id: str, summary: str) -> None: ...

    def get_summary(self, thread_id: str) -> str | None: ...

    def upsert_record(self, owner_id: str, record: MemoryRecord) -> None: ...

    def search_records(self, owner_id: str, query: str, limit: int = 5) -> list[MemoryRecord]: ...

    def clear(self, thread_id: str) -> None: ...
