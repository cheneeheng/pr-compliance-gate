"""Policy ingestion and retrieval with the vector store faked (ITER_01 §04).

The real store would download a sentence-transformers model, so it is stubbed; what matters here
is that headings become citable ``policy_id``s and that a retrieval hit round-trips into a
``PolicyChunk`` the specialists can cite.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.documents import Document

from gate.config import Settings, get_settings
from gate.graph.state import Classification
from gate.intake import ingest as ingest_mod
from gate.intake import retrieval as retrieval_mod
from gate.intake import store as store_mod
from gate.intake.ingest import _chunk_file, ingest
from gate.intake.retrieval import _TOP_K, retrieve

_POLICY_MD = """# Security Policy

Preamble that belongs to no policy.

## SEC-01 — No new network egress without review

Egress rules.

## Appendix

Not a policy — has no id, so nothing should cite it.
"""


def test_chunking_keeps_only_headings_with_a_citable_id() -> None:
    docs = _chunk_file(_POLICY_MD, "security.md")
    assert [d.metadata["policy_id"] for d in docs] == ["SEC-01"]
    assert docs[0].metadata["source_file"] == "security.md"
    assert docs[0].page_content.startswith("SEC-01 — No new network egress without review")


class _FakeStore:
    def __init__(self, *, reset_raises: bool = False) -> None:
        self.added: list[Document] = []
        self.reset_called = False
        self.deleted = False
        self._reset_raises = reset_raises

    def reset_collection(self) -> None:
        if self._reset_raises:
            raise AttributeError("older langchain-chroma")
        self.reset_called = True

    def delete_collection(self) -> None:
        self.deleted = True

    def add_documents(self, documents: list[Document]) -> None:
        self.added.extend(documents)


def test_ingest_rebuilds_the_collection_from_the_real_corpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    monkeypatch.setattr(ingest_mod, "get_vectorstore", lambda _s: store)

    count = ingest()

    assert store.reset_called  # re-ingest is deterministic, not additive
    assert count == len(store.added) > 0
    ids = {d.metadata["policy_id"] for d in store.added}
    assert {"SEC-01", "LIC-02", "DATA-03"} <= ids


def test_ingest_falls_back_when_the_store_cannot_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stores = [_FakeStore(reset_raises=True), _FakeStore()]
    monkeypatch.setattr(ingest_mod, "get_vectorstore", lambda _s: stores.pop(0))

    ingest()

    assert stores == []  # the fallback re-opened the store after deleting the collection


def test_ingest_writes_nothing_when_the_corpus_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    store = _FakeStore()
    monkeypatch.setattr(ingest_mod, "get_vectorstore", lambda _s: store)
    monkeypatch.setattr(ingest_mod, "POLICIES_DIR", tmp_path)

    assert ingest() == 0
    assert store.added == []


class _QueryStore:
    def __init__(self) -> None:
        self.query: str = ""
        self.k: int = 0

    def similarity_search_with_score(self, query: str, k: int) -> list[tuple[Document, float]]:
        self.query, self.k = query, k
        return [
            (
                Document(
                    page_content="egress",
                    metadata={"policy_id": "SEC-01", "source_file": "security.md"},
                ),
                0.25,
            ),
            (Document(page_content="orphan"), 0.9),  # metadata-less hit must not crash retrieval
        ]


def test_retrieval_queries_on_the_classification_and_keeps_the_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _QueryStore()
    monkeypatch.setattr(retrieval_mod, "get_vectorstore", lambda _s: store)
    classification = Classification(
        change_type="code", summary="s", touched_areas=["auth middleware"]
    )

    chunks = retrieve(classification, get_settings())

    assert store.query == "code auth middleware" and store.k == _TOP_K
    assert (chunks[0].policy_id, chunks[0].distance) == ("SEC-01", 0.25)
    assert (chunks[1].policy_id, chunks[1].source_file) == ("", "")


def test_retrieval_resolves_settings_itself_when_not_given_any(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(retrieval_mod, "get_vectorstore", lambda _s: _QueryStore())
    assert retrieve(Classification(change_type="docs", summary="s"))


def test_the_store_is_built_once_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ingestion and retrieval must agree on collection, directory and embeddings."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        store_mod, "HuggingFaceEmbeddings", lambda model_name: seen.setdefault("model", model_name)
    )
    monkeypatch.setattr(store_mod, "Chroma", lambda **kw: seen.setdefault("chroma", kw))

    settings = Settings(gate_embed_model="local-embedder", policy_collection="policies")
    store_mod.get_vectorstore(settings)

    assert seen["model"] == "local-embedder"
    assert seen["chroma"]["collection_name"] == "policies"
    assert seen["chroma"]["persist_directory"] == str(settings.gate_chroma_dir)
