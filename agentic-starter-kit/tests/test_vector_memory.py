"""Embeddings and hybrid retrieval."""

import pytest

from starter.memory import ContextMemory, InMemoryStore, MemoryRecord
from starter.memory.embeddings import HashingEmbedder, cosine
from starter.memory.vector import EMBEDDING_KEY, VectorMemoryStore

# ------------------------------------------------------------- embeddings


def test_hashing_embedder_is_deterministic_and_normalised():
    embedder = HashingEmbedder(dimensions=64)
    a, b = embedder.embed(["invoice 42 refunded"] * 2)
    assert a == b
    assert len(a) == 64
    assert pytest.approx(sum(x * x for x in a), rel=1e-9) == 1.0


def test_hashing_embedder_is_stable_across_instances():
    # Not Python's salted hash: a vector cached yesterday must still compare.
    assert HashingEmbedder(32).embed(["mango"]) == HashingEmbedder(32).embed(["mango"])


def test_empty_text_embeds_to_the_zero_vector():
    assert HashingEmbedder(16).embed([""])[0] == [0.0] * 16


def test_cosine_bounds():
    embedder = HashingEmbedder(64)
    same = embedder.embed(["refund policy", "refund policy"])
    assert pytest.approx(cosine(*same), rel=1e-9) == 1.0
    disjoint = embedder.embed(["alpha beta", "gamma delta"])
    assert cosine(*disjoint) == 0.0
    assert cosine([], [1.0]) == 0.0
    assert cosine([1.0, 0.0], [1.0]) == 0.0  # length mismatch is not a crash


# ------------------------------------------------------- hybrid retrieval


def store(alpha: float = 0.7) -> VectorMemoryStore:
    return VectorMemoryStore(
        inner=InMemoryStore(), embedder=HashingEmbedder(128), alpha=alpha, min_score=0.0
    )


def test_embeds_at_write_time():
    vector_store = store()
    vector_store.upsert_record("u1", MemoryRecord(text="Invoice 42 was refunded."))
    record = vector_store._all_records("u1")[0]
    assert len(record.metadata[EMBEDDING_KEY]) == 128


def test_ranks_the_relevant_record_first():
    vector_store = store()
    for text in ("Invoice 42 was refunded on Tuesday.", "The office cat is called Mango."):
        vector_store.upsert_record("u1", MemoryRecord(text=text))
    hits = vector_store.search_records("u1", "refunded invoice", limit=2)
    assert "refunded" in hits[0].text
    assert hits[0].score > hits[1].score


def test_respects_the_limit_and_min_score():
    vector_store = VectorMemoryStore(
        inner=InMemoryStore(), embedder=HashingEmbedder(128), min_score=0.5
    )
    for i in range(5):
        vector_store.upsert_record("u1", MemoryRecord(text=f"fact number {i}"))
    assert len(vector_store.search_records("u1", "fact number 1", limit=2)) <= 2
    assert vector_store.search_records("u1", "entirely unrelated wording", limit=5) == []


def test_alpha_zero_is_pure_lexical_and_one_is_pure_vector():
    for alpha in (0.0, 1.0):
        vector_store = store(alpha)
        vector_store.upsert_record("u1", MemoryRecord(text="Invoice 42 was refunded."))
        hits = vector_store.search_records("u1", "invoice refunded", limit=1)
        assert hits and hits[0].score > 0


def test_alpha_is_validated():
    with pytest.raises(ValueError):
        VectorMemoryStore(embedder=HashingEmbedder(8), alpha=1.5)


def test_empty_store_returns_nothing():
    assert store().search_records("nobody", "anything") == []


def test_turns_and_summaries_pass_through_untouched():
    inner = InMemoryStore()
    vector_store = VectorMemoryStore(inner=inner, embedder=HashingEmbedder(32))
    memory = ContextMemory(store=vector_store)
    memory.remember_turn("t1", "user", "hello")
    vector_store.set_summary("t1", "a summary")
    assert [t.content for t in inner.get_turns("t1")] == ["hello"]
    assert vector_store.get_summary("t1") == "a summary"
    vector_store.clear("t1")
    assert inner.get_turns("t1") == []


def test_context_memory_works_unchanged_on_a_vector_store():
    memory = ContextMemory(
        store=VectorMemoryStore(inner=InMemoryStore(), embedder=HashingEmbedder(64), min_score=0.0)
    )
    memory.remember_fact("u1", "The user prefers metric units.", key="units")
    messages = memory.build_context("t1", "which units?", owner_id="u1")
    assert any("metric units" in m["content"] for m in messages)


def test_build_store_selects_the_vector_backend(settings, tmp_path):
    from starter.memory.context import build_store

    settings.memory_backend = "vector"
    settings.memory_dir = str(tmp_path)
    assert isinstance(build_store(settings), VectorMemoryStore)
