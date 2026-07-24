"""The two pure-Python decision points: risk-level derivation and the verdict node (ITER_02 §04).

Both exist so tuning the gate never means re-prompting a model — the thresholds live in config
and the outcome mapping lives in code.
"""

from __future__ import annotations

from gate.config import Settings
from gate.graph.nodes.risk import derive_level
from gate.graph.nodes.verdict import verdict
from gate.graph.state import (
    DecisionAction,
    Finding,
    HumanDecision,
    Outcome,
    ReviewState,
    RiskLevel,
    Severity,
    Specialty,
)

_SETTINGS = Settings(gate_risk_threshold=40, gate_risk_high_bound=75)


def _finding(severity: Severity, policy_id: str = "SEC-01") -> Finding:
    return Finding(
        specialist=Specialty.security,
        severity=severity,
        policy_id=policy_id,
        description="d",
        confidence=0.5,
    )


def test_derive_level_splits_on_the_configured_bounds() -> None:
    assert derive_level(39, _SETTINGS) == RiskLevel.low
    assert derive_level(40, _SETTINGS) == RiskLevel.medium  # threshold is the gate boundary
    assert derive_level(74, _SETTINGS) == RiskLevel.medium
    assert derive_level(75, _SETTINGS) == RiskLevel.high


async def test_verdict_auto_approves_when_no_human_was_involved() -> None:
    update = await verdict(ReviewState(pr_id="t"))
    assert update["verdict"].outcome == Outcome.auto_approved
    assert "no specialist findings" in update["verdict"].rationale


async def test_verdict_maps_the_human_action_and_quotes_the_note() -> None:
    state = ReviewState(
        pr_id="t",
        specialist_findings=[_finding(Severity.block, "LIC-02"), _finding(Severity.block)],
        human_decision=HumanDecision(action=DecisionAction.reject, note="copyleft"),
    )
    update = await verdict(state)
    assert update["verdict"].outcome == Outcome.rejected
    rationale = update["verdict"].rationale
    assert "blocking findings on LIC-02, SEC-01" in rationale  # deduped and sorted
    assert "Reviewer note: copyleft" in rationale


async def test_verdict_approves_and_counts_non_blocking_findings() -> None:
    state = ReviewState(
        pr_id="t",
        specialist_findings=[_finding(Severity.info), _finding(Severity.warn)],
        human_decision=HumanDecision(action=DecisionAction.approve),
    )
    update = await verdict(state)
    assert update["verdict"].outcome == Outcome.approved
    assert "2 non-blocking finding(s)" in update["verdict"].rationale
    assert "Reviewer note" not in update["verdict"].rationale  # empty note is omitted
