"""Policy chunk retrieval.

Builds a query from the classification and returns the top-k policy chunks for downstream
specialists. Retrieval runs *after* classification (the cheap gate never sees policy text).
"""

from __future__ import annotations

from gate.config import Settings, get_settings
from gate.graph.state import Classification, PolicyChunk
from gate.intake.store import get_vectorstore

_TOP_K = 6


def retrieve(classification: Classification, settings: Settings | None = None) -> list[PolicyChunk]:
    """Return the top-k policy chunks most relevant to the classified change."""
    settings = settings or get_settings()
    # change_type is a non-empty enum value, so the query is never empty.
    query = " ".join([classification.change_type.value, *classification.touched_areas])
    store = get_vectorstore(settings)
    hits = store.similarity_search_with_score(query, k=_TOP_K)
    chunks: list[PolicyChunk] = []
    for doc, distance in hits:
        chunks.append(
            PolicyChunk(
                text=doc.page_content,
                policy_id=str(doc.metadata.get("policy_id", "")),
                source_file=str(doc.metadata.get("source_file", "")),
                distance=float(distance),
            )
        )
    return chunks
