"""reconcile node — the routing brain. Pure Python, no LLM.

Computes the next route from the accumulated specialist findings and loop budget, writing it to
``state.route`` (the conditional edge just reads it). First match wins:

1. escalation already present (e.g. a specialist timeout) -> human_gate
2. any block-severity finding                             -> human_gate
3. needs_more_context and loop budget remains             -> investigate
4. needs_more_context and loop budget exhausted           -> escalate(max_cycles) -> human_gate
5. cross-specialist disagreement on the same policy        -> escalate(disagreement) -> human_gate
6. otherwise                                               -> verdict

``loop_count`` is never touched here — it is incremented only by the investigate node, so a
re-entrant checkpoint resume can't double-count.
"""

from __future__ import annotations

from gate.config import get_settings
from gate.graph.state import (
    EscalationRecord,
    Finding,
    ReviewState,
    Severity,
    Specialty,
)
from gate.logging import get_logger

log = get_logger(__name__)

ROUTE_INVESTIGATE = "investigate"
ROUTE_HUMAN_GATE = "human_gate"
ROUTE_VERDICT = "verdict"


def _has_disagreement(findings: list[Finding]) -> tuple[bool, str]:
    """True if two specialists rate the same policy across the info/warn vs block divide.

    Reachable only if no lone block finding exists (rule 2 catches those first); this catches a
    contested call where the severities genuinely span the block boundary between specialists.
    """
    by_policy: dict[str, set[tuple[Specialty, Severity]]] = {}
    for f in findings:
        by_policy.setdefault(f.policy_id, set()).add((f.specialist, f.severity))
    for policy_id, pairs in by_policy.items():
        specialists = {s for s, _ in pairs}
        severities = {sev for _, sev in pairs}
        spans_block = Severity.block in severities and bool(
            severities & {Severity.info, Severity.warn}
        )
        if len(specialists) > 1 and spans_block:
            return True, policy_id
    return False, ""


async def reconcile(state: ReviewState) -> dict[str, object]:
    settings = get_settings()
    findings = state.specialist_findings

    # Rule 1: an escalation was already recorded upstream (e.g. specialist timeout).
    if state.escalation is not None:
        return _route(state, ROUTE_HUMAN_GATE)

    # Rule 2: any hard block routes straight to the human.
    if any(f.severity == Severity.block for f in findings):
        return _route(state, ROUTE_HUMAN_GATE)

    needs_context = any(f.needs_more_context for f in findings)

    # Rule 3: unanswered questions and loop budget remaining -> investigate.
    if needs_context and state.loop_count < settings.gate_max_cycles:
        return _route(state, ROUTE_INVESTIGATE)

    # Rule 4: unanswered questions but budget exhausted -> escalate.
    if needs_context:
        detail = (
            f"loop budget of {settings.gate_max_cycles} investigation cycle(s) exhausted "
            "with open questions remaining"
        )
        return _route(
            state, ROUTE_HUMAN_GATE, EscalationRecord(reason="max_cycles", detail=detail)
        )

    # Rule 5: contested call across specialists on the same policy.
    disagree, policy_id = _has_disagreement(findings)
    if disagree:
        return _route(
            state,
            ROUTE_HUMAN_GATE,
            EscalationRecord(
                reason="disagreement",
                detail=f"specialists disagree on policy {policy_id}",
            ),
        )

    # Rule 6: clean.
    return _route(state, ROUTE_VERDICT)


def _route(
    state: ReviewState, route: str, escalation: EscalationRecord | None = None
) -> dict[str, object]:
    log.debug("reconcile_route", pr_id=state.pr_id, route=route, loop_count=state.loop_count)
    update: dict[str, object] = {"route": route}
    if escalation is not None:
        update["escalation"] = escalation
    return update
