"""Specialist fan-out: policy slicing, investigation context, and the timeout escape hatch
(ITER_02 §04).

Each specialist sees only its own policy slice — small prompts, checkable citations — and a slow
model must escalate rather than hang the graph.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gate.config import Settings
from gate.graph.nodes import specialists
from gate.graph.state import (
    Finding,
    InvestigationReport,
    PolicyChunk,
    ReviewState,
    Severity,
    Specialty,
    StaticFinding,
)


async def test_specialist_timeout_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(anthropic_api_key="x", gate_specialist_timeout_s=0.01)
    monkeypatch.setattr(specialists, "get_settings", lambda: settings)
    monkeypatch.setattr(specialists, "specialist_model", lambda s=None: object())

    async def _slow(*_a: object, **_k: object) -> specialists._FindingList:
        await asyncio.sleep(0.2)
        return specialists._FindingList(findings=[])

    monkeypatch.setattr(specialists, "structured_call", _slow)

    update = await specialists.security_specialist(ReviewState(pr_id="t", diff="x"))
    assert update["escalation"].reason == "timeout"  # type: ignore[union-attr]
    assert "specialist_findings" not in update


def _chunk(policy_id: str) -> PolicyChunk:
    return PolicyChunk(text=f"{policy_id} body", policy_id=policy_id, source_file="p.md")


async def test_each_specialist_sees_only_its_own_policy_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _capture(_model: object, _schema: object, _system: str, user: str) -> Any:
        captured["user"] = user
        return specialists._FindingList(
            findings=[
                Finding(
                    specialist=Specialty.security,  # deliberately wrong: the node must override
                    severity=Severity.warn,
                    policy_id="LIC-02",
                    description="copyleft",
                    confidence=0.8,
                )
            ]
        )

    monkeypatch.setattr(specialists, "specialist_model", lambda s=None: object())
    monkeypatch.setattr(specialists, "structured_call", _capture)

    state = ReviewState(
        pr_id="t",
        diff="x",
        policy_context=[_chunk("SEC-01"), _chunk("DATA-03"), _chunk("LIC-02")],
        investigation=InvestigationReport(summary="manifest pins GPL-3.0"),
    )
    update = await specialists.license_specialist(state)

    assert "LIC-02" in captured["user"]
    assert "SEC-01" not in captured["user"] and "DATA-03" not in captured["user"]
    assert "manifest pins GPL-3.0" in captured["user"]  # investigation feeds the re-run
    # Trust the routing key, not the model: the finding is relabelled to this node's specialty.
    assert update["specialist_findings"][0].specialist == Specialty.license


async def test_static_analysis_hits_reach_the_specialist_as_citable_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So a specialist can raise severity to `block` with rule/path/line behind it rather than
    with the investigator's prose alone."""
    captured: dict[str, Any] = {}

    async def _capture(_m: object, _s: object, _sys: str, user: str) -> Any:
        captured["user"] = user
        return specialists._FindingList()

    monkeypatch.setattr(specialists, "specialist_model", lambda s=None: object())
    monkeypatch.setattr(specialists, "structured_call", _capture)

    state = ReviewState(
        pr_id="t",
        diff="x",
        policy_context=[_chunk("SEC-01")],
        investigation=InvestigationReport(
            summary="the audit module posts to an external collector",
            semgrep_runs=1,
            static_analysis=[
                StaticFinding(
                    rule_id="rules.gate-sec-01-new-network-egress",
                    severity=Severity.warn,
                    path="src/acme/audit.py",
                    line=23,
                    message="SEC-01: new outbound network call",
                )
            ],
        ),
    )
    await specialists.security_specialist(state)

    assert "Static analysis hits:" in captured["user"]
    assert "rules.gate-sec-01-new-network-egress src/acme/audit.py:23 (warn)" in captured["user"]


async def test_an_investigation_without_static_hits_adds_no_evidence_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _capture(_m: object, _s: object, _sys: str, user: str) -> Any:
        captured["user"] = user
        return specialists._FindingList()

    monkeypatch.setattr(specialists, "specialist_model", lambda s=None: object())
    monkeypatch.setattr(specialists, "structured_call", _capture)

    state = ReviewState(
        pr_id="t",
        diff="x",
        policy_context=[_chunk("SEC-01")],
        investigation=InvestigationReport(summary="nothing found", semgrep_runs=1),
    )
    await specialists.security_specialist(state)

    assert "Static analysis hits:" not in captured["user"]


async def test_the_security_specialist_also_owns_data_handling_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three policy areas, two specialists: DATA-* belongs to security (DECISION_LOG entry 4)."""
    captured: dict[str, Any] = {}

    async def _capture(_model: object, _schema: object, _system: str, user: str) -> Any:
        captured["user"] = user
        return specialists._FindingList()

    monkeypatch.setattr(specialists, "specialist_model", lambda s=None: object())
    monkeypatch.setattr(specialists, "structured_call", _capture)

    state = ReviewState(pr_id="t", diff="x", policy_context=[_chunk("DATA-03"), _chunk("LIC-02")])
    assert await specialists.security_specialist(state) == {"specialist_findings": []}
    assert "DATA-03" in captured["user"] and "LIC-02" not in captured["user"]


async def test_a_specialist_with_no_matching_policy_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _capture(_model: object, _schema: object, _system: str, user: str) -> Any:
        captured["user"] = user
        return specialists._FindingList()

    monkeypatch.setattr(specialists, "specialist_model", lambda s=None: object())
    monkeypatch.setattr(specialists, "structured_call", _capture)

    await specialists.license_specialist(ReviewState(pr_id="t", policy_context=[_chunk("SEC-01")]))
    assert "(no policy chunks matched this specialty)" in captured["user"]
