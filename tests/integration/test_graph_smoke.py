"""Graph topology and its edge conditions (SKELETON §04, ITER_02 §04).

The routing functions are where the cost gates live, so they are asserted directly rather than
inferred from an end-to-end run. The last test is the exception: the tracing kill switch
(ITER_02_v2 §04) is a claim about a *run*, not about a route, so it drives the compiled graph.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from gate.config import Settings, get_settings
from gate.graph.build import (
    _route_after_gate,
    _route_after_reconcile,
    _route_after_risk,
    build_graph_builder,
    compile_graph,
)
from gate.graph.checkpoint import use_state_allowlist
from gate.graph.nodes import classify as classify_mod
from gate.graph.nodes import risk as risk_mod
from gate.graph.nodes.reconcile import ROUTE_HUMAN_GATE, ROUTE_VERDICT
from gate.graph.state import (
    Classification,
    DecisionAction,
    HumanDecision,
    Outcome,
    ReviewState,
    RiskAssessment,
)
from gate.telemetry import tracing as tracing_mod
from gate.telemetry.tracing import init_tracing, shutdown_tracing

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


async def test_a_disabled_tracing_run_emits_no_spans_and_still_reaches_a_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app must run with the Langfuse container down — a portfolio reviewer cloning the repo
    should not need Docker to see the gate work.

    An exporting provider is installed *first*, then the kill switch overwrites it. So a run that
    produces zero spans here proves the switch won, not that nothing was listening.
    """
    memory = InMemorySpanExporter()
    exporting = TracerProvider()
    exporting.add_span_processor(SimpleSpanProcessor(memory))
    monkeypatch.setattr(tracing_mod, "_provider", exporting)

    async def _classified(*_args: object) -> Classification:
        return Classification(change_type="docs", summary="typo fix")

    async def _scored(*_args: object) -> RiskAssessment:
        return RiskAssessment(score=get_settings().gate_risk_threshold - 1)

    monkeypatch.setattr(classify_mod, "classifier_model", lambda s=None: object())
    monkeypatch.setattr(classify_mod, "structured_call", _classified)
    monkeypatch.setattr(risk_mod, "classifier_model", lambda s=None: object())
    monkeypatch.setattr(risk_mod, "structured_call", _scored)

    init_tracing(object(), Settings(gate_otel_enabled=False))
    try:
        graph = compile_graph(use_state_allowlist(InMemorySaver()))
        values = await graph.ainvoke(
            ReviewState(pr_id="001_docs_typo", thread_id="t-off", diff="d"),
            {"configurable": {"thread_id": "t-off"}},
        )
    finally:
        shutdown_tracing()

    assert values["verdict"].outcome == Outcome.auto_approved
    assert memory.get_finished_spans() == ()
