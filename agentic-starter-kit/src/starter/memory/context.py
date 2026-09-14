"""Context memory: assembles the prompt context from all three memory layers.

This is the piece most projects rewrite badly. The rules here are explicit:

1. system prompt
2. long-term records relevant to *this* query (retrieved, capped)
3. the rolling summary of everything older than the window
4. the last `short_term_max_turns` turns verbatim

When the thread grows past `summary_trigger_turns`, the oldest turns are rolled
into the summary by the LLM (or by a deterministic fallback when no provider is
configured) and dropped from the verbatim window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from starter.llm import LLMProvider
from starter.memory.base import MemoryRecord, MemoryStore, Turn
from starter.memory.stores import CosmosMemoryStore, FileMemoryStore, InMemoryStore
from starter.settings import Settings, get_settings

SUMMARY_PROMPT = (
    "Condense the conversation below into durable facts, decisions and open "
    "questions. Keep names, ids and numbers exactly. No preamble, max 200 words."
)


@dataclass
class ContextMemory:
    store: MemoryStore
    max_turns: int = 20
    summary_trigger: int = 12
    max_records: int = 5
    llm: LLMProvider | None = None
    system_prompt: str = "You are a helpful, careful assistant."
    _stats: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------- writing

    def remember_turn(self, thread_id: str, role: str, content: str, **metadata: Any) -> None:
        self.store.append_turn(thread_id, Turn(role=role, content=content, metadata=metadata))

    def remember_fact(
        self,
        owner_id: str,
        text: str,
        kind: str = "fact",
        key: str | None = None,
        **metadata: Any,
    ) -> MemoryRecord:
        record = MemoryRecord(text=text, kind=kind, key=key, metadata=metadata)
        self.store.upsert_record(owner_id, record)
        return record

    # ---------------------------------------------------------------- reading

    def recall(self, owner_id: str, query: str) -> list[MemoryRecord]:
        return self.store.search_records(owner_id, query, limit=self.max_records)

    def build_context(
        self, thread_id: str, query: str, owner_id: str | None = None
    ) -> list[dict[str, str]]:
        """Return the message list to send to the model, oldest first."""
        self.maybe_summarize(thread_id)
        messages: list[dict[str, str]] = [{"role": "system", "content": self.system_prompt}]

        records = self.recall(owner_id or thread_id, query)
        if records:
            bullets = "\n".join(f"- {r.text}" for r in records)
            messages.append(
                {"role": "system", "content": f"Relevant long-term memory:\n{bullets}"}
            )

        summary = self.store.get_summary(thread_id)
        if summary:
            messages.append({"role": "system", "content": f"Conversation so far:\n{summary}"})

        for turn in self.store.get_turns(thread_id, limit=self.max_turns):
            messages.append(turn.as_message())

        if query:
            messages.append({"role": "user", "content": query})
        return messages

    # ------------------------------------------------------------ compaction

    def maybe_summarize(self, thread_id: str) -> str | None:
        """Roll the oldest turns into the summary once the window overflows."""
        turns = self.store.get_turns(thread_id)
        if len(turns) <= max(self.summary_trigger, self.max_turns):
            return None
        overflow = turns[: len(turns) - self.max_turns]
        previous = self.store.get_summary(thread_id) or ""
        summary = self._summarize(previous, overflow)
        self.store.set_summary(thread_id, summary)
        self._stats["summarized_turns"] = self._stats.get("summarized_turns", 0) + len(overflow)
        return summary

    def _summarize(self, previous: str, turns: list[Turn]) -> str:
        transcript = "\n".join(f"{t.role}: {t.content}" for t in turns)
        if self.llm is None:
            head = f"{previous}\n" if previous else ""
            return (head + transcript)[-4000:]
        response = self.llm.complete(
            [
                {"role": "system", "content": SUMMARY_PROMPT},
                {
                    "role": "user",
                    "content": f"Existing summary:\n{previous}\n\nNew turns:\n{transcript}",
                },
            ]
        )
        return response.text.strip()


def build_store(settings: Settings | None = None) -> MemoryStore:
    s = settings or get_settings()
    if s.memory_backend == "file":
        return FileMemoryStore(s.memory_dir)
    if s.memory_backend == "cosmos":
        if not s.cosmos_endpoint:
            raise ValueError("COSMOS_ENDPOINT is required for memory_backend=cosmos")
        return CosmosMemoryStore(s.cosmos_endpoint, s.cosmos_database, s.cosmos_container)
    return InMemoryStore()


def build_memory(
    settings: Settings | None = None,
    llm: LLMProvider | None = None,
    system_prompt: str | None = None,
) -> ContextMemory:
    s = settings or get_settings()
    return ContextMemory(
        store=build_store(s),
        max_turns=s.short_term_max_turns,
        summary_trigger=s.summary_trigger_turns,
        llm=llm,
        system_prompt=system_prompt or ContextMemory.system_prompt,
    )
