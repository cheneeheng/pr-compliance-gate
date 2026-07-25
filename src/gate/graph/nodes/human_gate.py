"""human_gate node — the HITL interrupt.

Performs a real LangGraph ``interrupt()``: the graph pauses here and the checkpoint records the
review packet the human needs. The API's ``/decision`` endpoint resumes with
``Command(resume={"action", "note"})``; that value is what ``interrupt()`` returns on resume.
"""

from __future__ import annotations

from datetime import UTC, datetime

from langgraph.types import interrupt

from gate.graph.state import HumanDecision, ReviewState
from gate.logging import get_logger

log = get_logger(__name__)


def build_interrupt_payload(state: ReviewState) -> dict[str, object]:
    """The review packet surfaced to the human (also what ``/pending`` returns)."""
    return {
        "pr_id": state.pr_id,
        "risk": state.risk.model_dump() if state.risk else None,
        "findings": [f.model_dump() for f in state.specialist_findings],
        "investigation": state.investigation.model_dump() if state.investigation else None,
        "escalation": state.escalation.model_dump() if state.escalation else None,
    }


async def human_gate(state: ReviewState) -> dict[str, object]:
    resumed = interrupt(build_interrupt_payload(state))
    # resumed is the payload passed to Command(resume=...) by the decision endpoint.
    decision = HumanDecision(
        action=resumed["action"],
        note=resumed.get("note", ""),
        decided_at=datetime.now(UTC),
    )
    log.info("human_decided", pr_id=state.pr_id, action=decision.action)
    return {"human_decision": decision}
