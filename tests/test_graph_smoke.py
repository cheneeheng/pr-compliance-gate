"""Graph construction smoke test — the topology compiles with every node wired."""

from __future__ import annotations

from gate.graph.build import build_graph_builder

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
    assert _EXPECTED <= set(builder.nodes)
