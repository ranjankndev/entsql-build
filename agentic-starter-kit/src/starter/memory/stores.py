"""Memory backends. All three satisfy `MemoryStore`.

Retrieval here is lexical (token overlap) so the kit has zero infra
dependencies out of the box. Swap `search_records` for an embedding search
(Azure AI Search, pgvector, Cosmos DB vector index) without touching callers.
"""

from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from starter.memory.base import MemoryRecord, Turn

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def lexical_score(query: str, text: str) -> float:
    """Jaccard overlap. Good enough to be useful, simple enough to unit test."""
    q, t = _tokens(query), _tokens(text)
    if not q or not t:
        return 0.0
    return len(q & t) / len(q | t)


class InMemoryStore:
    """Process-local store. Default for tests, evals and local development."""

    def __init__(self) -> None:
        self._turns: dict[str, list[Turn]] = defaultdict(list)
        self._summaries: dict[str, str] = {}
        self._records: dict[str, dict[str, MemoryRecord]] = defaultdict(dict)
        self._lock = threading.Lock()

    def append_turn(self, thread_id: str, turn: Turn) -> None:
        with self._lock:
            self._turns[thread_id].append(turn)

    def get_turns(self, thread_id: str, limit: int | None = None) -> list[Turn]:
        turns = list(self._turns.get(thread_id, []))
        return turns[-limit:] if limit else turns

    def set_summary(self, thread_id: str, summary: str) -> None:
        self._summaries[thread_id] = summary

    def get_summary(self, thread_id: str) -> str | None:
        return self._summaries.get(thread_id)

    def upsert_record(self, owner_id: str, record: MemoryRecord) -> None:
        with self._lock:
            self._records[owner_id][record.key or record.id] = record

    def search_records(self, owner_id: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        scored = [
            (lexical_score(query, r.text), r) for r in self._records.get(owner_id, {}).values()
        ]
        hits = [r for score, r in sorted(scored, key=lambda p: p[0], reverse=True) if score > 0]
        return hits[:limit]

    def clear(self, thread_id: str) -> None:
        with self._lock:
            self._turns.pop(thread_id, None)
            self._summaries.pop(thread_id, None)


class FileMemoryStore(InMemoryStore):
    """JSON-on-disk store: survives restarts, still needs no server."""

    def __init__(self, directory: str | Path) -> None:
        super().__init__()
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _path(self) -> Path:
        return self._dir / "memory.json"

    def _load(self) -> None:
        path = self._path()
        if not path.exists():
            return
        blob: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        for thread_id, turns in blob.get("turns", {}).items():
            self._turns[thread_id] = [Turn(**t) for t in turns]
        self._summaries.update(blob.get("summaries", {}))
        for owner, records in blob.get("records", {}).items():
            self._records[owner] = {k: MemoryRecord(**v) for k, v in records.items()}

    def _flush(self) -> None:
        blob = {
            "turns": {k: [asdict(t) for t in v] for k, v in self._turns.items()},
            "summaries": self._summaries,
            "records": {
                owner: {k: asdict(r) for k, r in recs.items()}
                for owner, recs in self._records.items()
            },
        }
        self._path().write_text(json.dumps(blob, indent=2), encoding="utf-8")

    def append_turn(self, thread_id: str, turn: Turn) -> None:
        super().append_turn(thread_id, turn)
        self._flush()

    def set_summary(self, thread_id: str, summary: str) -> None:
        super().set_summary(thread_id, summary)
        self._flush()

    def upsert_record(self, owner_id: str, record: MemoryRecord) -> None:
        super().upsert_record(owner_id, record)
        self._flush()

    def clear(self, thread_id: str) -> None:
        super().clear(thread_id)
        self._flush()


class CosmosMemoryStore:
    """Azure Cosmos DB (NoSQL API) store, partitioned by thread/owner id.

    Auth is Managed Identity via `DefaultAzureCredential` — no keys in config.
    Import is lazy so the kit installs and runs without the azure extra.
    """

    def __init__(self, endpoint: str, database: str, container: str) -> None:
        from azure.cosmos import CosmosClient  # type: ignore[import-not-found]
        from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]

        client = CosmosClient(endpoint, credential=DefaultAzureCredential())
        self._container = client.get_database_client(database).get_container_client(container)

    def _upsert(self, doc: dict[str, Any]) -> None:
        self._container.upsert_item(doc)

    def _query(self, sql: str, params: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(
            self._container.query_items(
                query=sql, parameters=params, enable_cross_partition_query=True
            )
        )

    def append_turn(self, thread_id: str, turn: Turn) -> None:
        self._upsert(
            {
                "id": f"{thread_id}:turn:{turn.ts}:{turn.role}",
                "pk": thread_id,
                "doc_type": "turn",
                "role": turn.role,
                "content": turn.content,
                "ts": turn.ts,
                "metadata": turn.metadata,
            }
        )

    def get_turns(self, thread_id: str, limit: int | None = None) -> list[Turn]:
        rows = self._query(
            "SELECT * FROM c WHERE c.pk=@pk AND c.doc_type='turn' ORDER BY c.ts",
            [{"name": "@pk", "value": thread_id}],
        )
        turns = [
            Turn(role=r["role"], content=r["content"], ts=r["ts"], metadata=r.get("metadata", {}))
            for r in rows
        ]
        return turns[-limit:] if limit else turns

    def set_summary(self, thread_id: str, summary: str) -> None:
        self._upsert(
            {"id": f"{thread_id}:summary", "pk": thread_id, "doc_type": "summary", "text": summary}
        )

    def get_summary(self, thread_id: str) -> str | None:
        rows = self._query(
            "SELECT * FROM c WHERE c.pk=@pk AND c.doc_type='summary'",
            [{"name": "@pk", "value": thread_id}],
        )
        return rows[0]["text"] if rows else None

    def upsert_record(self, owner_id: str, record: MemoryRecord) -> None:
        self._upsert(
            {
                "id": f"{owner_id}:rec:{record.key or record.id}",
                "pk": owner_id,
                "doc_type": "record",
                "text": record.text,
                "kind": record.kind,
                "scope": record.scope,
                "key": record.key,
                "ts": record.ts,
                "metadata": record.metadata,
            }
        )

    def search_records(self, owner_id: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        rows = self._query(
            "SELECT * FROM c WHERE c.pk=@pk AND c.doc_type='record'",
            [{"name": "@pk", "value": owner_id}],
        )
        records = [
            MemoryRecord(
                text=r["text"],
                kind=r.get("kind", "fact"),
                scope=r.get("scope", "thread"),
                key=r.get("key"),
                ts=r.get("ts", 0.0),
                metadata=r.get("metadata", {}),
            )
            for r in rows
        ]
        scored = [(lexical_score(query, r.text), r) for r in records]
        return [r for s, r in sorted(scored, key=lambda p: p[0], reverse=True) if s > 0][:limit]

    def clear(self, thread_id: str) -> None:
        for row in self._query(
            "SELECT c.id FROM c WHERE c.pk=@pk", [{"name": "@pk", "value": thread_id}]
        ):
            self._container.delete_item(row["id"], partition_key=thread_id)
