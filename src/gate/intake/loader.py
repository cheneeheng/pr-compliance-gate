"""Sample-PR fixture loader.

Reads ``meta.json`` + ``diff.patch`` from a fixture directory and parses the changed file
paths out of the patch headers. A malformed fixture fails loud (``FixtureError`` -> HTTP 422),
never a silently empty review.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from gate.errors import FixtureError
from gate.paths import SAMPLE_PRS_DIR

# `diff --git a/old/path b/new/path` — capture the post-image path.
_DIFF_GIT = re.compile(r"^diff --git a/.+ b/(.+)$", re.MULTILINE)

# A fixture id is a single directory name, never a path. `pr_id` arrives from the HTTP body and
# is joined onto SAMPLE_PRS_DIR here *and* becomes the investigator's sandbox root
# (`paths.repo_snapshot_dir`), so a `..` or absolute segment would relocate the sandbox itself.
# No dot is allowed, so `..` cannot be expressed at all.
_SAFE_PR_ID = re.compile(r"[A-Za-z0-9_-]+")


class PRSummary(BaseModel):
    """List-view projection of a fixture."""

    pr_id: str
    title: str
    files_changed_count: int


class PRFixture(BaseModel):
    """A fully loaded sample PR."""

    pr_id: str
    title: str
    diff: str
    files_changed: list[str] = Field(default_factory=list)
    meta: dict[str, object] = Field(default_factory=dict)


def _parse_changed_files(diff: str) -> list[str]:
    return _DIFF_GIT.findall(diff)


def _read_fixture_dir(pr_dir: Path) -> PRFixture:
    meta_path = pr_dir / "meta.json"
    diff_path = pr_dir / "diff.patch"
    if not meta_path.is_file() or not diff_path.is_file():
        raise FixtureError(f"fixture {pr_dir.name} missing meta.json or diff.patch")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FixtureError(f"fixture {pr_dir.name} has invalid meta.json: {exc}") from exc
    diff = diff_path.read_text(encoding="utf-8")
    files_changed = _parse_changed_files(diff)
    if not files_changed:
        raise FixtureError(f"fixture {pr_dir.name} diff.patch has no parseable file headers")
    return PRFixture(
        pr_id=meta.get("pr_id", pr_dir.name),
        title=meta.get("title", pr_dir.name),
        diff=diff,
        files_changed=files_changed,
        meta=meta,
    )


def load_pr(pr_id: str) -> PRFixture:
    """Load a single fixture by id. Raises ``FixtureError`` if the id is unsafe or the fixture is
    missing/malformed."""
    if not _SAFE_PR_ID.fullmatch(pr_id):
        raise FixtureError(f"invalid sample PR id {pr_id!r}")
    pr_dir = SAMPLE_PRS_DIR / pr_id
    if not pr_dir.is_dir():
        raise FixtureError(f"no sample PR fixture named {pr_id!r}")
    return _read_fixture_dir(pr_dir)


def list_prs() -> list[PRSummary]:
    """List all fixtures for the picker. Skips directories that fail to parse."""
    if not SAMPLE_PRS_DIR.is_dir():
        return []
    summaries: list[PRSummary] = []
    for pr_dir in sorted(p for p in SAMPLE_PRS_DIR.iterdir() if p.is_dir()):
        try:
            fixture = _read_fixture_dir(pr_dir)
        except FixtureError:
            continue
        summaries.append(
            PRSummary(
                pr_id=fixture.pr_id,
                title=fixture.title,
                files_changed_count=len(fixture.files_changed),
            )
        )
    return summaries
