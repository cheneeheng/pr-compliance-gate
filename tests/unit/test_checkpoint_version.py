"""GATE_STATE_VERSION gate on the checkpoint database (ITER_01_v2 §04).

Removing ``PolicyChunk.distance`` makes pre-v2 checkpoints undeserializable on resume. There is
deliberately no migration — local single-user demo data — so the recorded version is the gate and
the documented fix is deleting the file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gate.errors import StateVersionError
from gate.graph.checkpoint import check_state_version


def test_a_fresh_database_is_stamped_with_the_current_version(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "checkpoints.db"  # parent does not exist yet

    check_state_version(db, 2)

    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT value FROM gate_meta WHERE key = 'state_version'").fetchone()
    assert row[0] == "2"


def test_a_matching_version_starts_cleanly(tmp_path: Path) -> None:
    db = tmp_path / "checkpoints.db"
    check_state_version(db, 2)
    check_state_version(db, 2)  # idempotent: the stamp is written once, then read


def test_a_mismatched_version_refuses_to_start(tmp_path: Path) -> None:
    db = tmp_path / "checkpoints.db"
    check_state_version(db, 1)

    with pytest.raises(StateVersionError, match="delete"):
        check_state_version(db, 2)
