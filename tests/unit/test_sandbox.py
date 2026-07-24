"""Investigator sandbox, tools and budget unit tests (ITER_03 §04).

The tools are the investigator's whole attack surface: read-only, snapshot-scoped, budgeted.
They are exercised here through their real closures, without the SDK loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gate.agents.investigator import _GREP_MAX_HITS, _build_tools, _Tracker, resolve_in_sandbox
from gate.config import get_settings
from gate.graph.state import ReviewState


def _tools(root: Path, tracker: _Tracker) -> dict[str, object]:
    return {t.name: t.handler for t in _build_tools(root, tracker)}


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_text("def verify(sig):\n    return True\n", encoding="utf-8")
    (root / "README.md").write_text("docs\n", encoding="utf-8")
    return root


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


async def test_read_file_records_each_file_once_and_truncates(snapshot: Path) -> None:
    (snapshot / "big.txt").write_text("A" * 60 + "B" * 60, encoding="utf-8")
    tracker = _Tracker(max_tool_calls=5, max_file_chars=40)
    read_file = _tools(snapshot, tracker)["read_file"]

    first = await read_file({"path": "big.txt"})
    await read_file({"path": "big.txt"})

    assert "[... truncated 80 chars ...]" in first["content"][0]["text"]
    assert tracker.files_examined == ["big.txt"]  # deduped across repeat reads


async def test_grep_repo_reports_path_and_line_number(snapshot: Path) -> None:
    tracker = _Tracker(max_tool_calls=5, max_file_chars=500)
    grep_repo = _tools(snapshot, tracker)["grep_repo"]

    hit = await grep_repo({"pattern": "def verify"})
    assert hit["content"][0]["text"] == "src/auth.py:1: def verify(sig):"
    assert (await grep_repo({"pattern": "nowhere"}))["content"][0]["text"] == "no matches"
    assert (await grep_repo({"pattern": ""}))["is_error"]


async def test_grep_repo_caps_its_own_result_size(snapshot: Path) -> None:
    """One grep must not flood the context window even when the pattern matches everything."""
    (snapshot / "many.txt").write_text("hit\n" * (_GREP_MAX_HITS * 2), encoding="utf-8")
    tracker = _Tracker(max_tool_calls=5, max_file_chars=10_000)
    grep_repo = _tools(snapshot, tracker)["grep_repo"]

    lines = (await grep_repo({"pattern": "hit"}))["content"][0]["text"].splitlines()
    assert len(lines) == _GREP_MAX_HITS


async def test_grep_repo_skips_files_it_cannot_read(
    snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable file in the snapshot must not abort the search."""
    (snapshot / "locked.bin").write_bytes(b"hit")
    real_read_text = Path.read_text

    def _read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "locked.bin":
            raise OSError("permission denied")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _read_text)
    tracker = _Tracker(max_tool_calls=5, max_file_chars=500)
    grep_repo = _tools(snapshot, tracker)["grep_repo"]

    result = await grep_repo({"pattern": "def verify"})
    assert result["content"][0]["text"] == "src/auth.py:1: def verify(sig):"


async def test_list_dir_marks_directories_and_defaults_to_the_root(snapshot: Path) -> None:
    tracker = _Tracker(max_tool_calls=5, max_file_chars=500)
    list_dir = _tools(snapshot, tracker)["list_dir"]

    assert (await list_dir({}))["content"][0]["text"] == "README.md\nsrc/"
    (snapshot / "empty").mkdir()
    assert (await list_dir({"path": "empty"}))["content"][0]["text"] == "(empty)"
    assert (await list_dir({"path": "src/auth.py"}))["is_error"]  # not a directory


async def test_every_tool_refuses_once_the_budget_is_spent(snapshot: Path) -> None:
    tracker = _Tracker(max_tool_calls=0, max_file_chars=500)
    for handler in _tools(snapshot, tracker).values():
        assert (await handler({"path": ".", "pattern": "x"}))["is_error"]
    assert tracker.tool_calls == 0


async def test_run_investigation_without_snapshot_is_inconclusive() -> None:
    from gate.agents.investigator import run_investigation

    report = await run_investigation(ReviewState(pr_id="no_such_pr"), get_settings())
    assert "inconclusive" in report.summary
    assert report.tool_calls_count == 0
