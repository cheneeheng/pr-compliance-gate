"""Checkpoint serialization.

LangGraph's msgpack deserializer reconstructs only allowlisted types. Its default allowlist is
the sentinel ``True`` — everything is allowed, with a deprecation warning per unregistered type —
and the library states unregistered types will be refused in a future version, at which point
checkpointed state comes back as bare dicts instead of models.

``BaseCheckpointSaver.with_allowlist`` cannot be used to fix that: it *merges* into the existing
allowlist, and merging into ``True`` is a no-op that silently returns the saver unchanged (see
``JsonPlusSerializer.with_msgpack_allowlist``). The allowlist has to be supplied when the
serializer is constructed instead, which is what this module does.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.base import maybe_add_typed_methods
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from gate.errors import StateVersionError
from gate.graph.state import CHECKPOINT_ALLOWLIST


def use_state_allowlist(saver: BaseCheckpointSaver[Any]) -> BaseCheckpointSaver[Any]:
    """Point ``saver`` at a serializer that declares this project's state types.

    Everything outside :data:`~gate.graph.state.CHECKPOINT_ALLOWLIST` and LangGraph's own safe
    set is refused, so the behaviour matches what ``LANGGRAPH_STRICT_MSGPACK=true`` enforces
    today and what the library will enforce by default later.
    """
    saver.serde = maybe_add_typed_methods(
        JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_ALLOWLIST)
    )
    return saver


def check_state_version(db_path: Path, version: int) -> None:
    """Refuse to start against a checkpoint database written by an incompatible state schema.

    The allowlist governs *which* types msgpack will reconstruct, not whether an old payload
    still matches the current field set — a checkpoint carrying a since-removed field is still an
    old checkpoint. There is deliberately no migration: this is local single-user demo data, so
    the recorded version is a gate and the fix is deleting the file. Stamped on first use, so an
    existing database from before this check reads as the current version exactly once.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS gate_meta (key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute("SELECT value FROM gate_meta WHERE key = 'state_version'").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO gate_meta (key, value) VALUES ('state_version', ?)", (str(version),)
            )
            return
        if int(row[0]) != version:
            raise StateVersionError(
                f"{db_path} was written at GATE_STATE_VERSION={row[0]} but this build is "
                f"version {version}. There is no migration — delete {db_path} and re-run."
            )
