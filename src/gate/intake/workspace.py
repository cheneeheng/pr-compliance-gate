"""Local materialization of a live PR's repository tree.

MCP supplies the structured surface — PR metadata, the changed-file list, the diff. The repo
*tree* is materialized with a shallow clone, because pulling hundreds of files one at a time
through MCP tool calls to satisfy the investigator's ``read_file``/``grep_repo`` would be absurd,
and semgrep needs files on disk regardless.

The directory name is the ``head_sha`` and nothing else. Branch names legitimately contain ``/``
and can contain ``..``; using one as a path component is a traversal bug waiting to happen. A
40-char hex SHA validated by regex cannot escape.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from gate.config import Settings
from gate.logging import get_logger
from gate.runtime import UNSUPPORTED_LOOP_REASON, loop_supports_subprocess

log = get_logger(__name__)

_SHA = re.compile(r"[0-9a-f]{40}")
_BYTES_PER_GB = 1024**3


class WorkspaceError(Exception):
    """Materialization failed. Caught by the adapter and turned into an escalation, never HTTP."""


async def _git(*args: str, cwd: Path | None = None) -> None:
    """Run git with a fixed argv. Never ``shell=True`` — the argv is built from config and a
    regex-validated SHA, never from model output."""
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise WorkspaceError(f"git {args[0]} failed: {stderr.decode(errors='replace').strip()}")


async def materialize(clone_url: str, head_sha: str, settings: Settings) -> Path:
    """Clone ``clone_url`` at ``head_sha`` into the cache and return the checkout path.

    Cached by SHA, so re-reviewing the same head is free.
    """
    if not _SHA.fullmatch(head_sha):
        raise WorkspaceError(f"refusing to materialize a non-SHA ref: {head_sha!r}")
    if not loop_supports_subprocess():
        # Same trap as the Agent SDK: a bare NotImplementedError with an empty message.
        raise WorkspaceError(f"cannot clone: {UNSUPPORTED_LOOP_REASON}")

    root = settings.gate_workspace_dir
    target = root / head_sha
    if (target / ".git").is_dir():
        log.debug("workspace_cache_hit", head_sha=head_sha)
        return target

    root.mkdir(parents=True, exist_ok=True)
    # A partial clone left by an interrupted run would read as a cache miss forever.
    shutil.rmtree(target, ignore_errors=True)
    try:
        await _git(
            "clone", "--depth", "1", "--filter=blob:none", "--no-checkout", clone_url, str(target)
        )
        await _git("fetch", "--depth", "1", "origin", head_sha, cwd=target)
        await _git("checkout", "--force", head_sha, cwd=target)
    except WorkspaceError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    evict_lru(settings)
    return target


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def evict_lru(settings: Settings) -> list[str]:
    """Drop least-recently-used workspaces until the cache fits its budget.

    Unbounded clone caches are how a demo repo grows to 40 GB. Runs at startup and after each
    materialization; returns the SHAs evicted so the caller can log them.
    """
    root = settings.gate_workspace_dir
    if not root.is_dir():
        return []
    budget = int(settings.gate_workspace_max_gb * _BYTES_PER_GB)
    entries = sorted(
        ((p, _dir_size(p)) for p in root.iterdir() if p.is_dir()),
        key=lambda item: item[0].stat().st_mtime,
    )
    total = sum(size for _, size in entries)
    evicted: list[str] = []
    for path, size in entries:
        if total <= budget:
            break
        shutil.rmtree(path, ignore_errors=True)
        total -= size
        evicted.append(path.name)
    if evicted:
        log.info("workspace_evicted", count=len(evicted), shas=evicted)
    return evicted
