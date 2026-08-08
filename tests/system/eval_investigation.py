"""Behavioral eval: the investigator settles fixture 003's open question (ITER_03 §04/§06).

Marked ``llm`` — runs the real Claude Agent SDK loop over fixture 003's repo snapshot and needs
a live ``ANTHROPIC_API_KEY``. Asserts the investigation is populated, stays within the tool-call
budget, and examines at least one file matching ``expected_investigation_hits`` in meta.json.
"""

from __future__ import annotations

import pytest

from gate.agents.investigator import run_investigation
from gate.config import get_settings
from gate.graph.state import Finding, ReviewState, Severity, Specialty
from gate.intake.loader import load_pr

pytestmark = pytest.mark.llm


async def test_investigator_examines_expected_files() -> None:
    fixture = load_pr("003_auth_change")
    hits = fixture.meta.get("expected_investigation_hits", [])
    assert hits, "fixture 003 must declare expected_investigation_hits"

    # Seed the open question the security specialist would raise on the auth refactor.
    question = Finding(
        specialist=Specialty.security,
        severity=Severity.warn,
        policy_id="SEC-03",
        description="Is the session signature check still enforced after the refactor?",
        confidence=0.5,
        needs_more_context=True,
    )
    state = ReviewState(
        pr_id="003_auth_change",
        diff=fixture.diff,
        files_changed=fixture.files_changed,
        specialist_findings=[question],
    )
    settings = get_settings()

    report = await run_investigation(state, settings)

    assert report.files_examined, "investigator examined no files"
    assert report.tool_calls_count <= settings.gate_investigator_max_tool_calls
    examined = " ".join(report.files_examined)
    assert any(h in examined for h in hits), (
        f"no expected hit {hits} in files_examined={report.files_examined}"
    )


async def test_the_investigator_runs_semgrep_and_reports_a_committed_rule() -> None:
    """Behavioural smoke, not a benchmark — the same standard set in ITER_03 §06.

    Rule ids are matched by suffix: semgrep prefixes them with the config directory name, which
    depends on where the ruleset resolved to.
    """
    fixture = load_pr("003_auth_change")
    expected = fixture.meta.get("expected_semgrep_rule_ids", [])
    assert expected, "fixture 003 must declare expected_semgrep_rule_ids"

    state = ReviewState(
        pr_id="003_auth_change",
        diff=fixture.diff,
        files_changed=fixture.files_changed,
        specialist_findings=[
            Finding(
                specialist=Specialty.security,
                severity=Severity.warn,
                policy_id="SEC-01",
                description="Does the new audit module introduce network egress or log PII?",
                confidence=0.5,
                needs_more_context=True,
            )
        ],
    )
    settings = get_settings()

    report = await run_investigation(state, settings)

    assert report.static_analysis, "the investigator produced no static findings"
    assert report.semgrep_runs <= settings.gate_semgrep_max_runs
    found = {f.rule_id for f in report.static_analysis}
    assert any(actual.endswith(rule) for rule in expected for actual in found), (
        f"none of {expected} in {sorted(found)}"
    )
