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

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.base import maybe_add_typed_methods
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

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
