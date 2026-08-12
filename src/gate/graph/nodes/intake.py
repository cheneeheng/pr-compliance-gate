"""intake node — the graph's entry point for a PR that has already been loaded.

The adapter's ``load`` runs in the background task, *before* ``ainvoke``, and seeds the diff,
file list, workspace path and (on failure) the escalation into the initial state. So this node
does not fetch anything: it records what arrived and is the anchor the short-circuit edge routes
from.

The graph still runs for a failed load. It is not skipped to a terminal state out-of-band, which
is what keeps a failed review checkpointed, resumable, traced under the same run span, and
reachable through the same ``/pending`` and ``/decision`` endpoints as every other review. A
short-circuit inside the graph costs one edge; a second, non-graph failure path would cost a
parallel implementation of the entire review lifecycle.
"""

from __future__ import annotations

from gate.graph.state import ReviewState
from gate.logging import get_logger

log = get_logger(__name__)


async def intake(state: ReviewState) -> dict[str, object]:
    log.debug(
        "intake_loaded",
        pr_id=state.pr_id,
        source=state.source,
        files=len(state.files_changed),
        escalated=state.escalation is not None,
    )
    return {}
