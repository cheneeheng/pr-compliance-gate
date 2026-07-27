"""Fixture-loader unit tests (ITER_01 §04)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gate.errors import FixtureError
from gate.intake import loader
from gate.intake.loader import _parse_changed_files, _read_fixture_dir, list_prs, load_pr


def test_parse_changed_files_extracts_post_image_paths() -> None:
    diff = (
        "diff --git a/src/old.py b/src/new.py\n"
        "--- a/src/old.py\n+++ b/src/new.py\n"
        "diff --git a/pyproject.toml b/pyproject.toml\n"
    )
    assert _parse_changed_files(diff) == ["src/new.py", "pyproject.toml"]


def test_load_pr_reads_real_fixture() -> None:
    fixture = load_pr("002_new_dependency")
    assert fixture.pr_id == "002_new_dependency"
    assert "pyproject.toml" in fixture.files_changed
    assert fixture.meta["expected_outcome"] == "rejected"


def test_load_pr_missing_raises() -> None:
    with pytest.raises(FixtureError):
        load_pr("does_not_exist")


@pytest.mark.parametrize(
    "pr_id",
    ["../secrets", "..", "003_auth_change/repo", "/etc/passwd", "C:\\Windows", ""],
)
def test_load_pr_rejects_unsafe_ids(pr_id: str) -> None:
    """`pr_id` reaches the filesystem and becomes the investigator's sandbox root — it must be a
    plain directory name, never a path."""
    with pytest.raises(FixtureError, match="invalid sample PR id"):
        load_pr(pr_id)


def test_malformed_meta_raises(tmp_path: Path) -> None:
    (tmp_path / "meta.json").write_text("{ not valid json", encoding="utf-8")
    (tmp_path / "diff.patch").write_text("diff --git a/x b/x\n", encoding="utf-8")
    with pytest.raises(FixtureError):
        _read_fixture_dir(tmp_path)


def test_diff_without_headers_raises(tmp_path: Path) -> None:
    (tmp_path / "meta.json").write_text(json.dumps({"pr_id": "x", "title": "x"}), encoding="utf-8")
    (tmp_path / "diff.patch").write_text("no git headers here\n", encoding="utf-8")
    with pytest.raises(FixtureError):
        _read_fixture_dir(tmp_path)


def test_missing_files_raise(tmp_path: Path) -> None:
    with pytest.raises(FixtureError):
        _read_fixture_dir(tmp_path)  # neither meta.json nor diff.patch


def test_list_prs_returns_all_three() -> None:
    ids = {s.pr_id for s in list_prs()}
    assert {"001_docs_typo", "002_new_dependency", "003_auth_change"} <= ids


def test_list_prs_skips_unparseable_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One broken fixture must not take the whole picker down."""
    good = tmp_path / "001_good"
    good.mkdir()
    (good / "meta.json").write_text(json.dumps({"pr_id": "001_good", "title": "t"}), "utf-8")
    (good / "diff.patch").write_text("diff --git a/x b/x\n", "utf-8")
    (tmp_path / "002_broken").mkdir()  # no meta.json, no diff.patch
    monkeypatch.setattr(loader, "SAMPLE_PRS_DIR", tmp_path)

    assert [s.pr_id for s in list_prs()] == ["001_good"]


def test_list_prs_is_empty_when_there_is_no_fixture_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loader, "SAMPLE_PRS_DIR", tmp_path / "missing")
    assert list_prs() == []
