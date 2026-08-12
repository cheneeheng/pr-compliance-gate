"""Graph wiring: nodes, conditional edges, fan-out/fan-in, checkpointer.

Flow:
    START -> intake
    intake --(escalation seeded by a failed load)--> human_gate   (intake short-circuit)
    intake --(else)--> classify -> retrieve_policy -> risk_score
    risk_score --(score < threshold)--> verdict            (cost gate #1: no Sonnet spend)
    risk_score --(else)--> {security_specialist, license_specialist}  (parallel fan-out)
    {specialists} -> reconcile                              (fan-in via the merge reducer)
    reconcile --> investigate | human_gate | verdict        (reconcile writes state.route)
    investigate -> {security_specialist, license_specialist}  (loop back, loop_count += 1)
    human_gate --(approve|reject)--> verdict
    human_gate --(request_info)--> investigate
    verdict -> END
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

from gate.config import get_settings

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph
from gate.graph.nodes import traced_node
from gate.graph.nodes.classify import classify
from gate.graph.nodes.human_gate import human_gate
from gate.graph.nodes.intake import intake
from gate.graph.nodes.investigate import investigate
from gate.graph.nodes.reconcile import (
    ROUTE_HUMAN_GATE,
    ROUTE_INVESTIGATE,
    ROUTE_VERDICT,
    reconcile,
)
from gate.graph.nodes.retrieve_policy import retrieve_policy
from gate.graph.nodes.risk import risk_score
from gate.graph.nodes.specialists import license_specialist, security_specialist
from gate.graph.nodes.verdict import verdict
from gate.graph.state import DecisionAction, ReviewState

_SPECIALISTS = ["security_specialist", "license_specialist"]


def _route_after_intake(state: ReviewState) -> str:
    """The only edge the v2 family adds.

    An escalation raised by ``load`` (``pr_too_large`` / ``source_unavailable``) exists before the
    graph has a diff, and reconcile's rule 1 only fires *after* classify, risk and the specialists
    have run. Routing a diff-less review down that path means asking Haiku to classify nothing and
    Sonnet to review nothing: spend and noise on a review whose outcome is already decided. Rule 1
    is unmodified and still catches escalations raised mid-graph — it is just not early enough to
    be free.
    """
    return "human_gate" if state.escalation is not None else "classify"


def _route_after_risk(state: ReviewState) -> list[str] | str:
    settings = get_settings()
    if state.risk is not None and state.risk.score < settings.gate_risk_threshold:
        return "verdict"
    return _SPECIALISTS  # parallel fan-out


def _route_after_reconcile(state: ReviewState) -> str:
    return state.route or ROUTE_VERDICT


def _route_after_gate(state: ReviewState) -> str:
    if state.human_decision and state.human_decision.action == DecisionAction.request_info:
        return "investigate"
    return "verdict"


def build_graph_builder() -> StateGraph[ReviewState]:
    """Construct the uncompiled graph topology (checkpointer added at compile time)."""
    builder: StateGraph[ReviewState] = StateGraph(ReviewState)

    # traced_node is applied here, uniformly, so no node can be silently untraced.
    for name, fn in (
        ("intake", intake),
        ("classify", classify),
        ("retrieve_policy", retrieve_policy),
        ("risk_score", risk_score),
        ("security_specialist", security_specialist),
        ("license_specialist", license_specialist),
        ("reconcile", reconcile),
        ("investigate", investigate),
        ("human_gate", human_gate),
        ("verdict", verdict),
    ):
        builder.add_node(name, traced_node(fn))

    builder.add_edge(START, "intake")
    builder.add_conditional_edges(
        "intake", _route_after_intake, {"classify": "classify", "human_gate": "human_gate"}
    )
    builder.add_edge("classify", "retrieve_policy")
    builder.add_edge("retrieve_policy", "risk_score")

    builder.add_conditional_edges(
        "risk_score", _route_after_risk, ["security_specialist", "license_specialist", "verdict"]
    )
    for specialist in _SPECIALISTS:
        builder.add_edge(specialist, "reconcile")

    builder.add_conditional_edges(
        "reconcile",
        _route_after_reconcile,
        {
            ROUTE_INVESTIGATE: "investigate",
            ROUTE_HUMAN_GATE: "human_gate",
            ROUTE_VERDICT: "verdict",
        },
    )
    # investigate loops back to the specialists for a fresh pass.
    builder.add_edge("investigate", "security_specialist")
    builder.add_edge("investigate", "license_specialist")

    builder.add_conditional_edges(
        "human_gate", _route_after_gate, {"investigate": "investigate", "verdict": "verdict"}
    )
    builder.add_edge("verdict", END)
    return builder


# Any saver: `use_state_allowlist()` returns a BaseCheckpointSaver clone, not the concrete class.
def compile_graph(checkpointer: BaseCheckpointSaver[Any]) -> CompiledStateGraph[ReviewState]:
    return build_graph_builder().compile(checkpointer=checkpointer)
