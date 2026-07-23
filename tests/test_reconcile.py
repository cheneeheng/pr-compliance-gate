"""reconcile routing-brain unit tests — all six routes (ITER_02 §04).

Rule 5 (disagreement) is shadowed by rule 2 (any block) in the full reconcile — a contested call
necessarily contains a block, which rule 2 catches first. That is the plan's explicit ordering
("ordered after rule 2 by design"), so rule 5 is exercised here against the `_has_disagreement`
predicate directly rather than through the shadowed public path.
"""

from __future__ import annotations

from gate.graph.nodes.reconcile import (
    ROUTE_HUMAN_GATE,
    ROUTE_INVESTIGATE,
    ROUTE_VERDICT,
    _has_disagreement,
    reconcile,
)
from gate.graph.state import (
    EscalationRecord,
    Finding,
    ReviewState,
    Severity,
    Specialty,
)


def _finding(**kw: object) -> Finding:
    base = dict(
        specialist=Specialty.security,
        severity=Severity.info,
        policy_id="SEC-01",
        description="d",
        confidence=0.9,
        needs_more_context=False,
    )
    base.update(kw)
    return Finding(**base)  # type: ignore[arg-type]


def _state(**kw: object) -> ReviewState:
    return ReviewState(pr_id="t", **kw)  # type: ignore[arg-type]


async def test_rule1_existing_escalation_routes_human() -> None:
    state = _state(escalation=EscalationRecord(reason="timeout", detail="x"))
    assert (await reconcile(state))["route"] == ROUTE_HUMAN_GATE


async def test_rule2_block_routes_human() -> None:
    state = _state(specialist_findings=[_finding(severity=Severity.block)])
    assert (await reconcile(state))["route"] == ROUTE_HUMAN_GATE


async def test_rule3_needs_context_within_budget_investigates() -> None:
    state = _state(specialist_findings=[_finding(needs_more_context=True)], loop_count=0)
    assert (await reconcile(state))["route"] == ROUTE_INVESTIGATE


async def test_rule4_needs_context_budget_exhausted_escalates() -> None:
    state = _state(specialist_findings=[_finding(needs_more_context=True)], loop_count=99)
    update = await reconcile(state)
    assert update["route"] == ROUTE_HUMAN_GATE
    assert update["escalation"].reason == "max_cycles"  # type: ignore[union-attr]


async def test_rule6_clean_routes_verdict() -> None:
    state = _state(specialist_findings=[_finding(severity=Severity.info)])
    assert (await reconcile(state))["route"] == ROUTE_VERDICT


def test_disagreement_predicate_detects_contested_policy() -> None:
    findings = [
        _finding(specialist=Specialty.security, severity=Severity.block, policy_id="SEC-01"),
        _finding(specialist=Specialty.license, severity=Severity.info, policy_id="SEC-01"),
    ]
    detected, policy_id = _has_disagreement(findings)
    assert detected and policy_id == "SEC-01"


def test_disagreement_predicate_ignores_single_specialist() -> None:
    findings = [
        _finding(specialist=Specialty.security, severity=Severity.block, policy_id="SEC-01"),
        _finding(specialist=Specialty.security, severity=Severity.info, policy_id="SEC-01"),
    ]
    assert _has_disagreement(findings) == (False, "")
