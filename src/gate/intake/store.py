"""Shared Chroma vector-store accessor for policy chunks.

Both ingestion and retrieval build the store the same way, so the construction lives here once.
Embeddings are local (sentence-transformers) — free, and avoids burning API spend on a tiny
policy corpus.
"""

from __future__ import annotations

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from gate.config import Settings


def get_vectorstore(settings: Settings) -> Chroma:
    embeddings = HuggingFaceEmbeddings(model_name=settings.gate_embed_model)
    return Chroma(
        collection_name=settings.policy_collection,
        embedding_function=embeddings,
        persist_directory=str(settings.gate_chroma_dir),
    )
