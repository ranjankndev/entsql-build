"""Vector and hybrid retrieval for long-term memory.

`VectorMemoryStore` decorates any `MemoryStore`: turns and summaries pass
straight through to the inner store, and only `search_records` changes. That
is the whole RAG upgrade path — nothing above the store knows it happened.

Retrieval is **hybrid** by default. Pure vector search misses exact tokens
(order ids, SKUs, error codes) that a user is most likely to quote verbatim;
pure lexical search misses paraphrase. `alpha` weights the two:

    score = alpha * cosine + (1 - alpha) * lexical
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from starter.memory.base import MemoryRecord, MemoryStore, Turn
from starter.memory.embeddings import Embedder, cosine, get_embedder
from starter.memory.stores import InMemoryStore, lexical_score
from starter.settings import Settings, get_settings

EMBEDDING_KEY = "embedding"


@dataclass
class VectorMemoryStore:
    """Hybrid-search decorator over any `MemoryStore`."""

    inner: MemoryStore = field(default_factory=InMemoryStore)
    embedder: Embedder | None = None
    alpha: float = 0.7
    min_score: float = 0.05
    _cache: dict[str, list[float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.embedder is None:
            self.embedder = get_embedder()
        if not 0.0 <= self.alpha <= 1.0:
            raise ValueError(f"alpha must be in [0, 1], got {self.alpha}")

    # ------------------------------------------------------ pass-through

    def append_turn(self, thread_id: str, turn: Turn) -> None:
        self.inner.append_turn(thread_id, turn)

    def get_turns(self, thread_id: str, limit: int | None = None) -> list[Turn]:
        return self.inner.get_turns(thread_id, limit)

    def set_summary(self, thread_id: str, summary: str) -> None:
        self.inner.set_summary(thread_id, summary)

    def get_summary(self, thread_id: str) -> str | None:
        return self.inner.get_summary(thread_id)

    def clear(self, thread_id: str) -> None:
        self.inner.clear(thread_id)

    # ----------------------------------------------------------- vectors

    def upsert_record(self, owner_id: str, record: MemoryRecord) -> None:
        """Embed once, at write time. Retrieval then costs one query embedding."""
        assert self.embedder is not None
        if EMBEDDING_KEY not in record.metadata:
            record.metadata[EMBEDDING_KEY] = self.embedder.embed([record.text])[0]
        self.inner.upsert_record(owner_id, record)

    def _query_vector(self, query: str) -> list[float]:
        assert self.embedder is not None
        if query not in self._cache:
            self._cache[query] = self.embedder.embed([query])[0]
        return self._cache[query]

    def search_records(self, owner_id: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        records = self._all_records(owner_id)
        if not records:
            return []
        query_vector = self._query_vector(query)
        scored: list[tuple[float, MemoryRecord]] = []
        for record in records:
            vector = record.metadata.get(EMBEDDING_KEY)
            dense = cosine(query_vector, vector) if vector else 0.0
            sparse = lexical_score(query, record.text)
            record.score = self.alpha * dense + (1 - self.alpha) * sparse
            scored.append((record.score, record))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [r for score, r in scored if score >= self.min_score][:limit]

    def _all_records(self, owner_id: str) -> list[MemoryRecord]:
        """Candidate set. The in-process stores hold everything; a server-side
        index (Azure AI Search, pgvector) does this filtering in the engine —
        see `AzureAISearchStore` below."""
        getter = getattr(self.inner, "_records", None)
        if isinstance(getter, dict):
            return list(getter.get(owner_id, {}).values())
        # Fall back to the inner store's own search, then re-rank.
        return self.inner.search_records(owner_id, query="", limit=1000)


@dataclass
class AzureAISearchStore:
    """Long-term memory in Azure AI Search, with server-side vector search.

    Turns and summaries stay in `inner` (Cosmos DB in production) — a search
    index is the wrong shape for an append-only transcript. Index fields
    expected: `id` (key), `owner_id` (filterable), `text`, `kind`, `ts`, and
    `embedding` (Collection(Edm.Single), vector-searchable).

    Auth is `DefaultAzureCredential`; imports are lazy so the kit installs
    without the azure extra.
    """

    endpoint: str
    index_name: str
    inner: MemoryStore = field(default_factory=InMemoryStore)
    embedder: Embedder | None = None
    vector_field: str = "embedding"
    _client: Any = None

    def __post_init__(self) -> None:
        if self.embedder is None:
            self.embedder = get_embedder()

    @property
    def client(self) -> Any:
        if self._client is None:
            from azure.identity import DefaultAzureCredential
            from azure.search.documents import SearchClient

            self._client = SearchClient(
                endpoint=self.endpoint,
                index_name=self.index_name,
                credential=DefaultAzureCredential(),
            )
        return self._client

    def append_turn(self, thread_id: str, turn: Turn) -> None:
        self.inner.append_turn(thread_id, turn)

    def get_turns(self, thread_id: str, limit: int | None = None) -> list[Turn]:
        return self.inner.get_turns(thread_id, limit)

    def set_summary(self, thread_id: str, summary: str) -> None:
        self.inner.set_summary(thread_id, summary)

    def get_summary(self, thread_id: str) -> str | None:
        return self.inner.get_summary(thread_id)

    def clear(self, thread_id: str) -> None:
        self.inner.clear(thread_id)

    def upsert_record(self, owner_id: str, record: MemoryRecord) -> None:
        assert self.embedder is not None
        vector = record.metadata.get(EMBEDDING_KEY) or self.embedder.embed([record.text])[0]
        self.client.merge_or_upload_documents(
            [
                {
                    "id": f"{owner_id}__{record.key or record.id}",
                    "owner_id": owner_id,
                    "text": record.text,
                    "kind": record.kind,
                    "ts": record.ts,
                    self.vector_field: vector,
                }
            ]
        )

    def search_records(self, owner_id: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        from azure.search.documents.models import VectorizedQuery

        assert self.embedder is not None
        vector = self.embedder.embed([query])[0]
        # Hybrid: the text query and the vector query are fused by the service.
        results = self.client.search(
            search_text=query or None,
            vector_queries=[
                VectorizedQuery(vector=vector, k_nearest_neighbors=limit, fields=self.vector_field)
            ],
            filter=f"owner_id eq '{owner_id}'",
            top=limit,
        )
        return [
            MemoryRecord(
                text=row["text"],
                kind=row.get("kind", "fact"),
                key=row.get("id"),
                ts=row.get("ts", 0.0),
                score=float(row.get("@search.score", 0.0)),
            )
            for row in results
        ]


def build_vector_store(settings: Settings | None = None, inner: MemoryStore | None = None):
    """`MEMORY_BACKEND=vector` wiring, used by `memory.context.build_store`."""
    s = settings or get_settings()
    base = inner if inner is not None else InMemoryStore()
    if s.search_endpoint:
        return AzureAISearchStore(
            endpoint=s.search_endpoint, index_name=s.search_index, inner=base
        )
    return VectorMemoryStore(inner=base, alpha=s.hybrid_alpha)
