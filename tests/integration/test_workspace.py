"""Workspace materialization (ITER_03_v2 §04).

The clone is exercised against a **local bare repo created in a fixture**, so the default suite
still makes no network call while the real ``git`` argv is what runs. CI needs ``git`` on the
runner for this file.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from gate.config import Settings
from gate.intake import workspace as workspace_mod
from gate.intake.workspace import WorkspaceError, evict_lru, materialize


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def origin(tmp_path: Path) -> tuple[str, str]:
    """A real local repository with one commit. Returns (clone_url, head_sha)."""
    work = tmp_path / "origin"
    work.mkdir()
    _git("init", "--initial-branch=main", cwd=work)
    _git("config", "user.email", "t@example.com", cwd=work)
    _git("config", "user.name", "Test", cwd=work)
    (work / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _git("add", "app.py", cwd=work)
    _git("commit", "-m", "initial", cwd=work)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout.strip()
    return work.as_uri(), head


def _settings(tmp_path: Path, **kw: object) -> Settings:
    return Settings(gate_workspace_dir=tmp_path / "workspaces", **kw)


async def test_a_workspace_is_checked_out_at_the_head_sha(
    origin: tuple[str, str], tmp_path: Path
) -> None:
    url, head = origin
    settings = _settings(tmp_path)

    path = await materialize(url, head, settings)

    assert path.name == head  # the directory name is the SHA, never a branch or repo name
    assert (path / "app.py").read_text(encoding="utf-8") == "print('hi')\n"


async def test_the_second_materialization_of_the_same_head_is_a_cache_hit(
    origin: tuple[str, str], tmp_path: Path
) -> None:
    url, head = origin
    settings = _settings(tmp_path)

    first = await materialize(url, head, settings)
    (first / "marker").write_text("kept", encoding="utf-8")
    second = await materialize(url, head, settings)

    assert second == first and (second / "marker").is_file()  # not re-cloned


async def test_a_non_sha_ref_is_refused(tmp_path: Path) -> None:
    """Branch names legitimately contain `/` and can contain `..` — using one as a path component
    is a traversal bug waiting to happen."""
    with pytest.raises(WorkspaceError, match="non-SHA ref"):
        await materialize("https://example.invalid/r.git", "../../etc", _settings(tmp_path))


async def test_a_failed_clone_leaves_no_half_materialized_directory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    head = "a" * 40

    with pytest.raises(WorkspaceError, match="git clone failed"):
        await materialize(str(tmp_path / "nonexistent"), head, settings)

    assert not (settings.gate_workspace_dir / head).exists()


async def test_an_unsupported_event_loop_is_named_not_guessed_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same trap as the Agent SDK: a bare NotImplementedError with an empty message."""
    monkeypatch.setattr(workspace_mod, "loop_supports_subprocess", lambda: False)

    with pytest.raises(WorkspaceError, match="--reload"):
        await materialize("https://example.invalid/r.git", "b" * 40, _settings(tmp_path))


def test_lru_eviction_drops_the_oldest_workspaces_until_the_cache_fits(tmp_path: Path) -> None:
    """Unbounded clone caches are how a demo repo grows to 40 GB."""
    root = tmp_path / "workspaces"
    root.mkdir()
    for index, name in enumerate(("old", "middle", "new")):
        directory = root / name
        directory.mkdir()
        (directory / "blob").write_bytes(b"x" * 4096)
        # Deterministic recency order regardless of filesystem timestamp granularity.
        import os

        os.utime(directory, (1_700_000_000 + index, 1_700_000_000 + index))

    settings = Settings(gate_workspace_dir=root, gate_workspace_max_gb=8192 / 1024**3)

    evicted = evict_lru(settings)

    assert evicted == ["old"]
    assert not (root / "old").exists() and (root / "new").exists()


def test_eviction_is_a_no_op_when_the_cache_does_not_exist(tmp_path: Path) -> None:
    assert evict_lru(_settings(tmp_path)) == []


def test_a_zero_budget_evicts_the_whole_cache(tmp_path: Path) -> None:
    """The loop must terminate having evicted everything, not stop one short of the budget."""
    root = tmp_path / "workspaces"
    for name in ("a", "b"):
        (root / name).mkdir(parents=True)
        (root / name / "blob").write_bytes(b"x" * 32)

    assert sorted(evict_lru(Settings(gate_workspace_dir=root, gate_workspace_max_gb=0))) == [
        "a",
        "b",
    ]


def test_eviction_keeps_everything_inside_budget(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    (root / "only").mkdir(parents=True)
    (root / "only" / "blob").write_bytes(b"x" * 16)

    assert evict_lru(Settings(gate_workspace_dir=root)) == []
    assert (root / "only").is_dir()


async def test_git_failures_surface_the_stderr_not_an_exit_code(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError) as excinfo:
        await workspace_mod._git("rev-parse", "--verify", "definitely-not-a-ref", cwd=tmp_path)
    assert "git rev-parse failed" in str(excinfo.value)


async def test_the_clone_runs_without_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """The "no shell" invariant: the argv is built from config and a regex-validated SHA."""
    captured: dict[str, object] = {}

    async def _fake_exec(*args: str, **kwargs: object) -> object:
        captured["argv"] = args
        captured["kwargs"] = kwargs

        class _Proc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    await workspace_mod._git("status")

    assert captured["argv"][0] == "git"
    assert "shell" not in captured["kwargs"]
