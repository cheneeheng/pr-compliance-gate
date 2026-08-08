"""Deterministic policy retrieval.

Retrieval is a config-driven file lookup, not a similarity search: ``index.yaml`` maps each
``ChangeType`` to the policy documents that govern it, and those documents are split on their
``##`` headings into citable chunks. Retrieval still runs *after* classification (the cheap gate
never sees policy text) — it needs the ``change_type`` to key on.

Why no embeddings: RAG earns its place when, after metadata narrowing, the candidate set still
exceeds what fits in context, or when disambiguation within a category is genuinely semantic
rather than categorical. Neither holds at a three-document corpus where ``change_type`` is
already a closed enum. Revisit embedding-based retrieval when a single ``change_type`` maps to
more policy text than fits comfortably alongside the diff, or when policies within one category
overlap enough that only semantic similarity separates them. Raw policy count is not the
trigger.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from langchain_text_splitters import MarkdownHeaderTextSplitter
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gate.config import Settings, get_settings
from gate.errors import PolicyIndexError
from gate.graph.state import ChangeType, Classification, PolicyChunk
from gate.paths import POLICIES_DIR, POLICY_INDEX

# Leading token of a policy heading, e.g. "SEC-01" in "SEC-01 — No new network egress".
_POLICY_ID = re.compile(r"^([A-Z]{2,6}-\d{2,3})\b")

# Split on `##` only. The `#` document title is not a policy heading; text before the first `##`
# is preamble and yields a section-less document, which is skipped.
_SPLITTER = MarkdownHeaderTextSplitter(headers_to_split_on=[("##", "section")])

# Whole-token, case-folded matching for keyword overrides. `touched_areas` entries are short noun
# phrases, so `auth` must match "auth module" but not "author metadata"; substring matching would
# be quieter to write and would pull security.md into a docs change about authorship.
_TOKEN = re.compile(r"[a-z0-9]+")


class PolicyIndex(BaseModel):
    """Parsed ``index.yaml``. ``extra="forbid"`` so a typo'd top-level key fails loudly."""

    model_config = ConfigDict(extra="forbid")

    by_change_type: dict[ChangeType, list[str]]
    keyword_overrides: dict[str, list[str]] = Field(default_factory=dict)


def index_path(settings: Settings) -> Path:
    """The policy index to read.

    Defaults to the asset-resolved copy (bundled wheel first, repo root otherwise); a configured
    ``GATE_POLICY_INDEX`` overrides that resolved default rather than replacing the resolution.
    """
    return settings.gate_policy_index or POLICY_INDEX


def load_index(settings: Settings | None = None) -> PolicyIndex:
    """Read and validate the shape of ``index.yaml``. Raises ``PolicyIndexError``."""
    settings = settings or get_settings()
    path = index_path(settings)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyIndexError(f"policy index unreadable at {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PolicyIndexError(f"policy index at {path} is not valid YAML: {exc}") from exc
    try:
        return PolicyIndex.model_validate(raw)
    except ValidationError as exc:
        raise PolicyIndexError(f"policy index at {path} is malformed: {exc}") from exc


def _chunks_for(source_file: str) -> list[PolicyChunk]:
    """Split one policy document into citable chunks, one per ``##`` heading."""
    path = POLICIES_DIR / source_file
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyIndexError(f"policy file {source_file!r} is missing or unreadable") from exc
    chunks: list[PolicyChunk] = []
    for section in _SPLITTER.split_text(text):
        heading = str(section.metadata.get("section", "")).strip()
        if not heading:
            continue  # preamble before the first `##` — not a policy heading
        match = _POLICY_ID.match(heading)
        if match is None:
            # Specialists cite policy_id; an unlabelled chunk breaks that contract silently.
            raise PolicyIndexError(f"heading {heading!r} in {source_file} has no policy id")
        chunks.append(
            PolicyChunk(
                text=f"{heading}\n{section.page_content}".strip(),
                policy_id=match.group(1),
                source_file=source_file,
            )
        )
    return chunks


def _matched_keywords(index: PolicyIndex, touched_areas: list[str]) -> list[str]:
    tokens = {t for area in touched_areas for t in _TOKEN.findall(area.casefold())}
    return [kw for kw in index.keyword_overrides if kw.casefold() in tokens]


def resolve_files(index: PolicyIndex, classification: Classification) -> list[str]:
    """The policy files governing this change: the change_type set, plus keyword overrides.

    Deduplicated with declaration order preserved — that order is what specialists read.
    """
    files = list(index.by_change_type[classification.change_type])
    for keyword in _matched_keywords(index, classification.touched_areas):
        files.extend(index.keyword_overrides[keyword])
    return list(dict.fromkeys(files))


def retrieve(classification: Classification, settings: Settings | None = None) -> list[PolicyChunk]:
    """Return every policy chunk governing the classified change, in file-then-document order."""
    settings = settings or get_settings()
    index = load_index(settings)
    chunks: list[PolicyChunk] = []
    for source_file in resolve_files(index, classification):
        chunks.extend(_chunks_for(source_file))
    return chunks


def validate_policy_index(settings: Settings | None = None) -> None:
    """Fail the process at startup rather than a review mid-run.

    Asserts every ``ChangeType`` member is mapped, every file named in the index exists, and
    every ``##`` heading in those files yields a ``policy_id``.
    """
    index = load_index(settings)
    missing = [c.value for c in ChangeType if c not in index.by_change_type]
    if missing:
        raise PolicyIndexError(
            f"policy index has no entry for change_type(s): {', '.join(missing)}"
        )
    named = {f for files in index.by_change_type.values() for f in files}
    named |= {f for files in index.keyword_overrides.values() for f in files}
    for source_file in sorted(named):
        _chunks_for(source_file)  # raises on a missing file or an unlabelled heading
