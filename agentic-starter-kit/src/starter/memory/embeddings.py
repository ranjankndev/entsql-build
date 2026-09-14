"""Embedding seam.

One `Embedder` interface so the vector store never learns which vendor made
the numbers. `hashing` is a dependency-free, deterministic stand-in: it is a
bag-of-words projection, so it reproduces lexical behaviour rather than real
semantics — enough to exercise, test and benchmark the vector path offline,
not enough to ship. Point `EMBEDDING_PROVIDER` at a real model before you
claim semantic retrieval.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Protocol, runtime_checkable

from starter.settings import Settings, get_settings

_WORD = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


class HashingEmbedder:
    """The hashing trick: token -> stable bucket, L2-normalised.

    Deterministic across processes and machines (it uses blake2b, not Python's
    salted `hash`), so a cached vector written last week still compares
    correctly today.
    """

    name = "hashing"

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def _bucket(self, token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in _WORD.findall(text.lower()):
                vector[self._bucket(token)] += 1.0
            norm = math.sqrt(sum(v * v for v in vector))
            vectors.append([v / norm for v in vector] if norm else vector)
        return vectors


class LangChainEmbedder:
    """Adapter over any `langchain_core` embeddings object."""

    def __init__(self, model: Any, name: str, dimensions: int) -> None:
        self._model = model
        self.name = name
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return list(self._model.embed_documents(texts))


def get_embedder(settings: Settings | None = None) -> Embedder:
    s = settings or get_settings()

    if s.embedding_provider == "hashing":
        return HashingEmbedder(s.embedding_dimensions)

    if s.embedding_provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return LangChainEmbedder(
            OpenAIEmbeddings(
                model=s.embedding_model, api_key=s.openai_api_key, base_url=s.openai_base_url
            ),
            "openai",
            s.embedding_dimensions,
        )

    if s.embedding_provider == "azure_openai":
        from langchain_openai import AzureOpenAIEmbeddings

        return LangChainEmbedder(
            AzureOpenAIEmbeddings(
                azure_endpoint=s.azure_openai_endpoint,
                azure_deployment=s.embedding_deployment or s.embedding_model,
                api_version=s.azure_openai_api_version,
                api_key=s.azure_openai_api_key,
            ),
            "azure_openai",
            s.embedding_dimensions,
        )

    raise ValueError(f"unknown embedding provider: {s.embedding_provider}")
