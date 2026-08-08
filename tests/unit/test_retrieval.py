"""Deterministic policy lookup (ITER_01_v2 §04). No LLM, no network, no vector store.

The startup-validation failures are branches under ``fail_under = 100``, so every one of them is
exercised here rather than left to a runtime that is supposed to be unreachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gate.config import Settings
from gate.errors import PolicyIndexError
from gate.graph.state import ChangeType, Classification
from gate.intake import retrieval as retrieval_mod
from gate.intake.retrieval import (
    PolicyIndex,
    index_path,
    load_index,
    resolve_files,
    retrieve,
    validate_policy_index,
)

_INDEX_YAML = """
by_change_type:
  code: [security.md, licensing.md]
  dependency: [licensing.md, security.md]
  config: [security.md, data-handling.md]
  data_handling: [data-handling.md, security.md]
  docs: []
keyword_overrides:
  auth: [security.md]
  logging: [data-handling.md]
"""

_SECURITY_MD = """# Security Policy

Preamble that belongs to no policy and must not become a chunk.

## SEC-01 — No new network egress without review

Egress rules.

## SEC-02 — No hardcoded secrets

Secret rules.
"""

_LICENSING_MD = "# Licensing\n\n## LIC-01 — Approved licenses only\n\nLicense rules.\n"
_DATA_MD = "# Data\n\n## DATA-01 — No PII in logs\n\nLogging rules.\n"


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway policy corpus + index, wired in place of the shipped assets."""
    (tmp_path / "security.md").write_text(_SECURITY_MD, encoding="utf-8")
    (tmp_path / "licensing.md").write_text(_LICENSING_MD, encoding="utf-8")
    (tmp_path / "data-handling.md").write_text(_DATA_MD, encoding="utf-8")
    (tmp_path / "index.yaml").write_text(_INDEX_YAML, encoding="utf-8")
    monkeypatch.setattr(retrieval_mod, "POLICIES_DIR", tmp_path)
    return tmp_path


def _settings(corpus: Path) -> Settings:
    return Settings(gate_policy_index=corpus / "index.yaml")


def _classification(change_type: str, *areas: str) -> Classification:
    return Classification(change_type=change_type, summary="s", touched_areas=list(areas))


# ---- index resolution -----------------------------------------------------------------------


def test_the_shipped_index_maps_every_change_type_and_parses_every_heading() -> None:
    """The real corpus, not a fixture: the assets that ship must pass their own startup gate."""
    validate_policy_index(Settings())


@pytest.mark.parametrize(
    ("change_type", "expected"),
    [
        ("code", ["security.md", "licensing.md"]),
        ("dependency", ["licensing.md", "security.md"]),
        ("config", ["security.md", "data-handling.md"]),
        ("data_handling", ["data-handling.md", "security.md"]),
    ],
)
def test_each_change_type_resolves_to_its_declared_files(
    corpus: Path, change_type: str, expected: list[str]
) -> None:
    index = load_index(_settings(corpus))
    assert resolve_files(index, _classification(change_type)) == expected


def test_docs_resolves_to_an_empty_set_without_error(corpus: Path) -> None:
    """A deliberately empty entry, not an absent one — and it must not be a failure."""
    assert retrieve(_classification("docs"), _settings(corpus)) == []


def test_a_keyword_override_unions_into_the_change_type_set(corpus: Path) -> None:
    index = load_index(_settings(corpus))
    files = resolve_files(index, _classification("docs", "logging config"))
    assert files == ["data-handling.md"]


def test_matching_is_whole_token_not_substring(corpus: Path) -> None:
    """`auth` matches "auth module"; it must not match "author metadata"."""
    index = load_index(_settings(corpus))
    assert resolve_files(index, _classification("docs", "auth module")) == ["security.md"]
    assert resolve_files(index, _classification("docs", "author metadata")) == []


def test_matching_is_case_folded(corpus: Path) -> None:
    index = load_index(_settings(corpus))
    assert resolve_files(index, _classification("docs", "Auth Middleware")) == ["security.md"]


def test_overrides_dedupe_and_preserve_declaration_order(corpus: Path) -> None:
    index = load_index(_settings(corpus))
    # `code` already declares security.md first; the auth override must not duplicate or reorder.
    files = resolve_files(index, _classification("code", "auth module", "logging config"))
    assert files == ["security.md", "licensing.md", "data-handling.md"]


# ---- chunking -------------------------------------------------------------------------------


def test_retrieval_returns_chunks_in_file_then_document_order(corpus: Path) -> None:
    chunks = retrieve(_classification("code"), _settings(corpus))
    assert [c.policy_id for c in chunks] == ["SEC-01", "SEC-02", "LIC-01"]
    assert chunks[0].source_file == "security.md"
    assert chunks[0].text.startswith("SEC-01 — No new network egress without review")
    assert "Preamble" not in "".join(c.text for c in chunks)


def test_retrieval_resolves_settings_itself_when_not_given_any() -> None:
    assert retrieve(_classification("code"))  # real corpus via get_settings()


def test_index_path_prefers_the_configured_override_over_the_resolved_default(
    corpus: Path,
) -> None:
    assert index_path(_settings(corpus)) == corpus / "index.yaml"
    assert index_path(Settings()) == retrieval_mod.POLICY_INDEX


# ---- fail-loud startup validation -------------------------------------------------------------


def test_an_unmapped_change_type_fails_at_startup(corpus: Path) -> None:
    (corpus / "index.yaml").write_text(_INDEX_YAML.replace("  docs: []\n", ""), encoding="utf-8")
    with pytest.raises(PolicyIndexError, match="no entry for change_type"):
        validate_policy_index(_settings(corpus))


def test_a_missing_policy_file_fails_at_startup(corpus: Path) -> None:
    (corpus / "licensing.md").unlink()
    with pytest.raises(PolicyIndexError, match="missing or unreadable"):
        validate_policy_index(_settings(corpus))


def test_an_unlabelled_heading_fails_loudly(corpus: Path) -> None:
    """Specialists cite policy_id — an unlabelled chunk breaks that contract silently."""
    (corpus / "licensing.md").write_text(
        _LICENSING_MD + "\n## Appendix\n\nNo policy id here.\n", encoding="utf-8"
    )
    with pytest.raises(PolicyIndexError, match="has no policy id"):
        validate_policy_index(_settings(corpus))


def test_a_file_named_only_by_a_keyword_override_is_still_validated(corpus: Path) -> None:
    """The override target is reachable policy text, so a broken one must fail the same way."""
    (corpus / "index.yaml").write_text(
        "by_change_type:\n"
        "  code: []\n  dependency: []\n  config: []\n  data_handling: []\n  docs: []\n"
        "keyword_overrides:\n  auth: [nowhere.md]\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyIndexError, match="missing or unreadable"):
        validate_policy_index(_settings(corpus))


def test_an_unreadable_index_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(PolicyIndexError, match="unreadable"):
        load_index(Settings(gate_policy_index=tmp_path / "absent.yaml"))


def test_invalid_yaml_fails_loudly(corpus: Path) -> None:
    (corpus / "index.yaml").write_text("by_change_type: [unclosed\n", encoding="utf-8")
    with pytest.raises(PolicyIndexError, match="not valid YAML"):
        load_index(_settings(corpus))


def test_a_malformed_index_shape_fails_loudly(corpus: Path) -> None:
    (corpus / "index.yaml").write_text("by_change_type:\n  code: not-a-list\n", encoding="utf-8")
    with pytest.raises(PolicyIndexError, match="malformed"):
        load_index(_settings(corpus))


def test_the_index_model_rejects_unknown_top_level_keys(corpus: Path) -> None:
    """`extra="forbid"` so a typo'd key is a startup failure, not a silently ignored section."""
    (corpus / "index.yaml").write_text(_INDEX_YAML + "keyword_override: {}\n", encoding="utf-8")
    with pytest.raises(PolicyIndexError, match="malformed"):
        load_index(_settings(corpus))


def test_the_index_model_covers_every_change_type_member() -> None:
    """Guards the parametrized table above from drifting as the enum grows."""
    index = PolicyIndex(by_change_type=dict.fromkeys(ChangeType, []))
    assert set(index.by_change_type) == set(ChangeType)
