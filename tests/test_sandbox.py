"""Investigator sandbox + budget unit tests (ITER_03 §04)."""

from __future__ import annotations

from pathlib import Path

from gate.agents.investigator import _Tracker, resolve_in_sandbox
from gate.config import get_settings
from gate.graph.state import ReviewState


def test_sandbox_allows_paths_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_text("hi", encoding="utf-8")
    resolved = resolve_in_sandbox(root, "sub/a.txt")
    assert resolved == (root / "sub" / "a.txt").resolve()


def test_sandbox_blocks_traversal_and_absolute(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert resolve_in_sandbox(root, "../secret") is None
    assert resolve_in_sandbox(root, "sub/../../up") is None
    assert resolve_in_sandbox(root, "/etc/passwd") is None


def test_tracker_stops_after_cap() -> None:
    tracker = _Tracker(max_tool_calls=2, max_file_chars=100)
    tracker.tool_calls = 2
    assert not tracker.over_budget()
    tracker.tool_calls = 3
    assert tracker.over_budget()


async def test_run_investigation_without_snapshot_is_inconclusive() -> None:
    from gate.agents.investigator import run_investigation

    report = await run_investigation(ReviewState(pr_id="no_such_pr"), get_settings())
    assert "inconclusive" in report.summary
    assert report.tool_calls_count == 0
