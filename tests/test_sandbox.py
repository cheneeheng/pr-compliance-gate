"""Investigator sandbox + budget unit tests (ITER_03 §04)."""

from __future__ import annotations

from pathlib import Path

from gate.agents.investigator import _build_tools, _Tracker, resolve_in_sandbox
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
    tracker.tool_calls = 1
    assert not tracker.budget_exhausted()
    tracker.tool_calls = 2
    assert tracker.budget_exhausted()


async def test_tools_stop_at_the_cap_and_never_overcount(tmp_path: Path) -> None:
    """The harness — not the model — enforces the tool-call budget, and a refused call is not
    billed to the counter, so ``tool_calls_count`` can never exceed the cap."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8")
    tracker = _Tracker(max_tool_calls=2, max_file_chars=100)
    read_file = next(t for t in _build_tools(root, tracker) if t.name == "read_file")

    for _ in range(2):
        assert not (await read_file.handler({"path": "a.txt"}))["is_error"]

    refused = await read_file.handler({"path": "a.txt"})
    assert refused["is_error"] and "budget exhausted" in refused["content"][0]["text"]
    assert tracker.tool_calls == 2


async def test_tools_refuse_paths_outside_the_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    tracker = _Tracker(max_tool_calls=5, max_file_chars=100)
    read_file = next(t for t in _build_tools(root, tracker) if t.name == "read_file")

    result = await read_file.handler({"path": "../secret.txt"})
    assert result["is_error"]
    assert tracker.files_examined == []


async def test_run_investigation_without_snapshot_is_inconclusive() -> None:
    from gate.agents.investigator import run_investigation

    report = await run_investigation(ReviewState(pr_id="no_such_pr"), get_settings())
    assert "inconclusive" in report.summary
    assert report.tool_calls_count == 0
