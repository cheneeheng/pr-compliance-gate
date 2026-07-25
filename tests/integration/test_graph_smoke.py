"""Graph topology and its edge conditions (SKELETON §04, ITER_02 §04).

The routing functions are where the cost gates live, so they are asserted directly rather than
inferred from an end-to-end run.
"""

from __future__ import annotations

from gate.config import get_settings
from gate.graph.build import (
    _route_after_gate,
    _route_after_reconcile,
    _route_after_risk,
    build_graph_builder,
)
from gate.graph.nodes.reconcile import ROUTE_HUMAN_GATE, ROUTE_VERDICT
from gate.graph.state import DecisionAction, HumanDecision, ReviewState, RiskAssessment

_EXPECTED = {
    "intake",
    "classify",
    "retrieve_policy",
    "risk_score",
    "security_specialist",
    "license_specialist",
    "reconcile",
    "investigate",
    "human_gate",
    "verdict",
}


def test_graph_has_expected_nodes() -> None:
    builder = build_graph_builder()
    assert set(builder.nodes) >= _EXPECTED


def test_cost_gate_one_keeps_low_risk_changes_away_from_the_specialists() -> None:
    threshold = get_settings().gate_risk_threshold
    below = ReviewState(pr_id="t", risk=RiskAssessment(score=threshold - 1))
    assert _route_after_risk(below) == "verdict"

    at_threshold = ReviewState(pr_id="t", risk=RiskAssessment(score=threshold))
    assert _route_after_risk(at_threshold) == ["security_specialist", "license_specialist"]


def test_a_missing_risk_score_fans_out_rather_than_auto_approving() -> None:
    """Fail safe: without a score the change is reviewed, never waved through."""
    assert _route_after_risk(ReviewState(pr_id="t")) == [
        "security_specialist",
        "license_specialist",
    ]


def test_the_reconcile_edge_just_reads_the_route_reconcile_wrote() -> None:
    assert _route_after_reconcile(ReviewState(pr_id="t", route=ROUTE_HUMAN_GATE)) == (
        ROUTE_HUMAN_GATE
    )
    assert _route_after_reconcile(ReviewState(pr_id="t")) == ROUTE_VERDICT


def test_only_request_info_sends_the_human_back_into_investigation() -> None:
    for action, expected in [
        (DecisionAction.request_info, "investigate"),
        (DecisionAction.approve, "verdict"),
        (DecisionAction.reject, "verdict"),
    ]:
        state = ReviewState(pr_id="t", human_decision=HumanDecision(action=action))
        assert _route_after_gate(state) == expected
    assert _route_after_gate(ReviewState(pr_id="t")) == "verdict"
