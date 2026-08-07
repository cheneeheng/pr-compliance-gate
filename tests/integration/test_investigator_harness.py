"""The investigator harness with the SDK loop stubbed (ITER_03 §04).

LangGraph sees one node with one exit, so the contract tested here is: the harness owns the
metrics, and *nothing* it does raises into the graph — an SDK failure comes back as an
inconclusive report so the review lands on a human, never in a stack trace.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from gate.agents.investigator import _InvestigationAnswer, run_investigation
from gate.config import get_settings
from gate.graph.state import Finding, ReviewState, Severity, Specialty
from gate.llm.models import effort_for

_PR_ID = "003_auth_change"  # the one fixture with a real repo snapshot to read


def _state() -> ReviewState:
    return ReviewState(
        pr_id=_PR_ID,
        files_changed=["src/acme/auth.py"],
        specialist_findings=[
            Finding(
                specialist=Specialty.security,
                severity=Severity.warn,
                policy_id="SEC-03",
                description="is the signature check still enforced?",
                confidence=0.5,
                needs_more_context=True,
            )
        ],
    )


class ResultMessage:
    """Named to match the SDK's class: the harness identifies it by class name, not isinstance."""

    def __init__(self, cost: float) -> None:
        self.total_cost_usd = cost
        self.result = "final loop message"


class _Assistant:
    def __init__(self, text: str) -> None:
        self.result = text


def _fake_query(*messages: Any) -> Any:
    def _query(prompt: str, options: Any) -> AsyncIterator[Any]:
        async def _gen() -> AsyncIterator[Any]:
            _query.seen = (prompt, options)  # type: ignore[attr-defined]
            for message in messages:
                yield message

        return _gen()

    return _query


async def _answer(*_a: Any, **_k: Any) -> _InvestigationAnswer:
    return _InvestigationAnswer(summary="signature check intact", findings=["auth.py verifies"])


async def test_harness_owns_the_metrics_and_the_model_owns_the_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A textless message in the middle: the SDK emits control frames the transcript must skip.
    query = _fake_query(_Assistant("looked at auth"), object(), ResultMessage(0.42))
    monkeypatch.setattr("gate.agents.investigator.query", query)
    monkeypatch.setattr("gate.agents.investigator.structured_call", _answer)

    report = await run_investigation(_state(), get_settings())

    assert report.summary == "signature check intact"
    assert report.findings == ["auth.py verifies"]
    assert report.cost_estimate_usd == 0.42  # from the SDK, not from the model's output
    assert report.tool_calls_count == 0  # this loop called no tools
    prompt, options = query.seen  # type: ignore[attr-defined]
    assert "is the signature check still enforced?" in prompt
    assert options.max_turns == get_settings().gate_investigator_max_turns
    # The tool loop carries the investigator tier's own effort, resolved against its model id.
    assert options.effort == effort_for(
        get_settings().gate_model_investigator, get_settings().gate_model_investigator_effort
    )


async def test_an_sdk_failure_comes_back_as_an_inconclusive_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(prompt: str, options: Any) -> AsyncIterator[Any]:
        raise RuntimeError("sdk exploded")

    monkeypatch.setattr("gate.agents.investigator.query", _boom)

    report = await run_investigation(_state(), get_settings())

    assert report.summary == "investigation inconclusive: sdk exploded"
    assert report.files_examined == []


async def test_a_failed_synthesis_turn_still_returns_what_the_loop_saw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gate.agents.investigator.query", _fake_query(_Assistant("partial notes")))

    async def _fail(*_a: Any, **_k: Any) -> _InvestigationAnswer:
        raise RuntimeError("structured output refused")

    monkeypatch.setattr("gate.agents.investigator.structured_call", _fail)

    report = await run_investigation(_state(), get_settings())

    assert report.summary == "partial notes"
    assert report.findings == ["partial notes"]


async def test_an_empty_loop_and_a_silent_model_still_produce_a_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gate.agents.investigator.query", _fake_query())

    async def _blank(*_a: Any, **_k: Any) -> _InvestigationAnswer:
        raise RuntimeError("nothing to summarize")

    monkeypatch.setattr("gate.agents.investigator.structured_call", _blank)
    report = await run_investigation(_state(), get_settings())
    assert report.summary == "investigation inconclusive: nothing to summarize"
    assert report.findings == []

    async def _empty(*_a: Any, **_k: Any) -> _InvestigationAnswer:
        return _InvestigationAnswer(summary="")

    monkeypatch.setattr("gate.agents.investigator.structured_call", _empty)
    report = await run_investigation(_state(), get_settings())
    assert report.summary == "investigation inconclusive: no answer produced"


async def test_a_loop_without_subprocess_support_is_named_not_guessed_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows' SelectorEventLoop cannot spawn the CLI, and the SDK reports that as an empty
    'Failed to start Claude Code:'. Say what actually happened instead."""
    monkeypatch.setattr("gate.agents.investigator._loop_can_spawn_subprocess", lambda: False)

    report = await run_investigation(_state(), get_settings())

    assert report.summary.startswith("investigation inconclusive:")
    assert "--reload" in report.summary
    assert report.tool_calls_count == 0
