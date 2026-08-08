"""Graph nodes against their real collaborators, with only the LLM boundary stubbed.

Covers the nodes whose behavior is wiring rather than logic: intake reads a real fixture,
retrieve_policy delegates to retrieval, risk_score derives its level server-side, and human_gate
turns a resume payload into a HumanDecision.
"""

from __future__ import annotations

from typing import Any

import pytest

from gate.config import Settings
from gate.graph.nodes import human_gate as human_gate_mod
from gate.graph.nodes import retrieve_policy as retrieve_mod
from gate.graph.nodes import risk as risk_mod
from gate.graph.nodes.human_gate import build_interrupt_payload, human_gate
from gate.graph.nodes.intake import intake
from gate.graph.nodes.retrieve_policy import retrieve_policy
from gate.graph.nodes.risk import risk_score
from gate.graph.state import (
    Classification,
    EscalationRecord,
    Finding,
    InvestigationReport,
    PolicyChunk,
    ReviewState,
    RiskAssessment,
    RiskLevel,
    Severity,
    Specialty,
)


async def test_intake_loads_the_diff_and_changed_files() -> None:
    update = await intake(ReviewState(pr_id="002_new_dependency"))
    assert "diff --git" in update["diff"]
    assert "pyproject.toml" in update["files_changed"]


async def test_retrieve_policy_delegates_to_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    chunk = PolicyChunk(text="t", policy_id="SEC-01", source_file="security.md")
    monkeypatch.setattr(retrieve_mod, "retrieve", lambda _c: [chunk])
    state = ReviewState(pr_id="t", classification=Classification(change_type="code", summary="s"))
    assert (await retrieve_policy(state))["policy_context"] == [chunk]


async def test_retrieve_policy_skips_the_query_when_classification_failed() -> None:
    """Nothing to build a query from — retrieve nothing rather than query on junk."""
    assert (await retrieve_policy(ReviewState(pr_id="t")))["policy_context"] == []


async def test_risk_score_derives_the_level_and_records_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(gate_model_classifier="claude-haiku-4-5", gate_risk_threshold=40)
    captured: dict[str, Any] = {}

    async def _fake(_model: object, _schema: object, _system: str, user: str) -> RiskAssessment:
        captured["user"] = user
        return RiskAssessment(score=80, reasons=["cites SEC-01"])  # level defaults to low

    monkeypatch.setattr(risk_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(risk_mod, "classifier_model", lambda s=None: object())
    monkeypatch.setattr(risk_mod, "structured_call", _fake)

    state = ReviewState(
        pr_id="t",
        diff="some diff",
        classification=Classification(change_type="code", summary="s"),
        policy_context=[PolicyChunk(text="egress rules", policy_id="SEC-01", source_file="s.md")],
    )
    update = await risk_score(state)

    assert update["risk"].level == RiskLevel.high  # server-derived, not model-chosen
    assert update["risk"].model_used == "claude-haiku-4-5"
    assert "[SEC-01] egress rules" in captured["user"]


async def test_risk_score_survives_missing_upstream_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake(_model: object, _schema: object, _system: str, user: str) -> RiskAssessment:
        captured["user"] = user
        return RiskAssessment(score=10)

    monkeypatch.setattr(risk_mod, "classifier_model", lambda s=None: object())
    monkeypatch.setattr(risk_mod, "structured_call", _fake)

    update = await risk_score(ReviewState(pr_id="t"))
    assert update["risk"].level == RiskLevel.low
    assert "(none retrieved)" in captured["user"] and "{}" in captured["user"]


def test_the_interrupt_payload_is_the_packet_the_human_needs() -> None:
    state = ReviewState(
        pr_id="t",
        risk=RiskAssessment(score=80, level=RiskLevel.high),
        specialist_findings=[
            Finding(
                specialist=Specialty.license,
                severity=Severity.block,
                policy_id="LIC-02",
                description="GPL-3.0 runtime dependency",
                confidence=0.9,
            )
        ],
        investigation=InvestigationReport(summary="checked the manifest"),
        escalation=EscalationRecord(reason="disagreement", detail="contested LIC-02"),
    )
    payload = build_interrupt_payload(state)
    assert payload["risk"]["level"] == RiskLevel.high
    assert payload["findings"][0]["policy_id"] == "LIC-02"
    assert payload["investigation"]["summary"] == "checked the manifest"
    assert payload["escalation"]["reason"] == "disagreement"


def test_the_interrupt_payload_renders_an_empty_review_as_nulls() -> None:
    payload = build_interrupt_payload(ReviewState(pr_id="t"))
    assert payload["risk"] is None
    assert payload["findings"] == []
    assert payload["investigation"] is None and payload["escalation"] is None


async def test_human_gate_turns_the_resume_payload_into_a_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(human_gate_mod, "interrupt", lambda _payload: {"action": "reject"})
    update = await human_gate(ReviewState(pr_id="t"))
    decision = update["human_decision"]
    assert decision.action == "reject"
    assert decision.note == ""  # note is optional on the wire
    assert decision.decided_at is not None  # stamped server-side, never by the client
