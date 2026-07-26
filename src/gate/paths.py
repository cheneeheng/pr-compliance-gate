"""Filesystem anchors for the shipped assets, resolved independently of the working directory."""

from __future__ import annotations

from pathlib import Path

# In a source checkout the assets sit at the repo root (src/gate/paths.py -> parents[2]). An
# installed wheel has no checkout, so hatch copies them next to the package as `_bundled/`
# (see pyproject `force-include`); that copy wins whenever it exists.
_BUNDLED = Path(__file__).resolve().parent / "_bundled"
ASSET_ROOT = _BUNDLED if _BUNDLED.is_dir() else Path(__file__).resolve().parents[2]
DATA_DIR = ASSET_ROOT / "data"
SAMPLE_PRS_DIR = DATA_DIR / "sample_prs"
POLICIES_DIR = DATA_DIR / "policies"
STATIC_DIR = ASSET_ROOT / "static"


def repo_snapshot_dir(pr_id: str) -> Path:
    """The sandboxed repository snapshot the investigator may read for a given PR."""
    return SAMPLE_PRS_DIR / pr_id / "repo"
