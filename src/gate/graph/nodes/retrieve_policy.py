"""retrieve_policy node — thin wrapper over intake.retrieval.

Sits between classify and risk_score (the corrected ordering from ITER_01 §04): retrieval needs
the classification to build its query.
"""

from __future__ import annotations

from gate.graph.state import ReviewState
from gate.intake.retrieval import retrieve
from gate.logging import get_logger

log = get_logger(__name__)


async def retrieve_policy(state: ReviewState) -> dict[str, object]:
    if state.classification is None:
        return {"policy_context": []}
    chunks = retrieve(state.classification)
    log.debug("retrieved_policy", pr_id=state.pr_id, chunks=len(chunks))
    return {"policy_context": chunks}
