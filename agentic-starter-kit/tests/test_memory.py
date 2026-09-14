from starter.memory import ContextMemory, InMemoryStore
from starter.memory.stores import FileMemoryStore, lexical_score


def test_turns_round_trip():
    memory = ContextMemory(store=InMemoryStore())
    memory.remember_turn("t1", "user", "hello")
    memory.remember_turn("t1", "assistant", "hi")
    assert [t.role for t in memory.store.get_turns("t1")] == ["user", "assistant"]


def test_context_order_is_system_memory_summary_turns():
    memory = ContextMemory(store=InMemoryStore(), max_turns=2, summary_trigger=2)
    memory.remember_fact("t1", "The user prefers metric units.")
    memory.remember_turn("t1", "user", "earlier question")
    messages = memory.build_context("t1", "prefers units?")
    assert messages[0]["role"] == "system"
    assert "metric units" in messages[1]["content"]
    assert messages[-1] == {"role": "user", "content": "prefers units?"}


def test_summarization_trims_the_window():
    memory = ContextMemory(store=InMemoryStore(), max_turns=2, summary_trigger=2)
    for i in range(6):
        memory.remember_turn("t1", "user", f"message {i}")
    summary = memory.maybe_summarize("t1")
    assert summary is not None
    assert "message 0" in summary
    messages = memory.build_context("t1", "next")
    assert sum(1 for m in messages if m["role"] == "user") <= 3


def test_recall_ranks_by_overlap():
    memory = ContextMemory(store=InMemoryStore())
    memory.remember_fact("u1", "Invoice 42 was refunded on Tuesday.")
    memory.remember_fact("u1", "The office cat is called Mango.")
    hits = memory.recall("u1", "refunded invoice")
    assert hits and "refunded" in hits[0].text


def test_file_store_persists(tmp_path):
    store = FileMemoryStore(tmp_path)
    store.append_turn("t1", __import__("starter.memory.base", fromlist=["Turn"]).Turn("user", "hi"))
    reopened = FileMemoryStore(tmp_path)
    assert [t.content for t in reopened.get_turns("t1")] == ["hi"]


def test_lexical_score_bounds():
    assert lexical_score("a b", "a b") == 1.0
    assert lexical_score("a", "z") == 0.0
