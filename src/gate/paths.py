"""Repo-relative filesystem anchors, resolved independently of the current working directory."""

from __future__ import annotations

from pathlib import Path

# src/gate/paths.py -> parents[2] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
SAMPLE_PRS_DIR = DATA_DIR / "sample_prs"
POLICIES_DIR = DATA_DIR / "policies"
STATIC_DIR = REPO_ROOT / "static"


def repo_snapshot_dir(pr_id: str) -> Path:
    """The sandboxed repository snapshot the investigator may read for a given PR."""
    return SAMPLE_PRS_DIR / pr_id / "repo"
