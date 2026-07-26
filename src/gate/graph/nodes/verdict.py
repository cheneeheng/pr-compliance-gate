"""verdict node — terminal. Pure Python.

Arrives from the low-risk edge (no human decision -> auto_approved) or from the human gate
(human_decision set -> approved/rejected). ``request_info`` never reaches here; it routes back
through investigate.
"""

from __future__ import annotations

from gate.graph.state import (
    DecisionAction,
    Outcome,
    ReviewState,
    Severity,
    Verdict,
)
from gate.logging import get_logger

log = get_logger(__name__)


def _findings_summary(state: ReviewState) -> str:
    if not state.specialist_findings:
        return "no specialist findings"
    blocks = [f for f in state.specialist_findings if f.severity == Severity.block]
    if blocks:
        ids = ", ".join(sorted({f.policy_id for f in blocks}))
        return f"blocking findings on {ids}"
    return f"{len(state.specialist_findings)} non-blocking finding(s)"


async def verdict(state: ReviewState) -> dict[str, object]:
    decision = state.human_decision
    if decision is None:
        outcome = Outcome.auto_approved
        rationale = f"Auto-approved: risk score below threshold; {_findings_summary(state)}."
    else:
        outcome = (
            Outcome.approved if decision.action == DecisionAction.approve else Outcome.rejected
        )
        note = f" Reviewer note: {decision.note}" if decision.note else ""
        rationale = f"Human {decision.action.value}: {_findings_summary(state)}.{note}"

    log.info("verdict", pr_id=state.pr_id, outcome=outcome)
    return {"verdict": Verdict(outcome=outcome, rationale=rationale)}
