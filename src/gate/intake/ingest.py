"""Policy corpus ingestion: ``uv run python -m gate.intake.ingest``.

Chunks ``data/policies/*.md`` by heading, embeds each chunk locally, and upserts into Chroma.
Idempotent: the collection is recreated on each run (the corpus is tiny; no incremental sync).
Each chunk carries a stable ``policy_id`` (the leading token of its heading, e.g. ``SEC-01``)
so specialists can cite it.
"""

from __future__ import annotations

import re

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter

from gate.config import get_settings
from gate.intake.store import get_vectorstore
from gate.paths import POLICIES_DIR

# Leading token of a policy heading, e.g. "SEC-01" in "SEC-01 — No new network egress".
_POLICY_ID = re.compile(r"^([A-Z]{2,6}-\d{2,3})\b")

_SPLITTER = MarkdownHeaderTextSplitter(
    headers_to_split_on=[("#", "title"), ("##", "section")]
)


def _chunk_file(text: str, source_file: str) -> list[Document]:
    docs: list[Document] = []
    for section in _SPLITTER.split_text(text):
        heading = str(section.metadata.get("section", ""))
        match = _POLICY_ID.match(heading.strip())
        if not match:
            continue  # skip the document title / any heading without a policy id
        policy_id = match.group(1)
        docs.append(
            Document(
                page_content=f"{heading}\n{section.page_content}".strip(),
                metadata={"policy_id": policy_id, "source_file": source_file},
            )
        )
    return docs


def ingest() -> int:
    """Rebuild the policy collection from ``data/policies/*.md``. Returns chunk count."""
    settings = get_settings()
    store = get_vectorstore(settings)
    # Recreate: drop any prior contents so re-ingest is deterministic.
    try:
        store.reset_collection()
    except AttributeError:  # older langchain-chroma: delete then re-open
        store.delete_collection()
        store = get_vectorstore(settings)

    documents: list[Document] = []
    for md_path in sorted(POLICIES_DIR.glob("*.md")):
        documents.extend(_chunk_file(md_path.read_text(encoding="utf-8"), md_path.name))

    if documents:
        store.add_documents(documents)
    return len(documents)


if __name__ == "__main__":
    count = ingest()
    print(f"ingested {count} policy chunks into {get_settings().gate_chroma_dir}")
