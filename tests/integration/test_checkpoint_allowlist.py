"""The checkpoint serializer declares this project's state types (see graph/checkpoint.py)."""

from __future__ import annotations

import logging

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from gate.graph.checkpoint import use_state_allowlist
from gate.graph.state import (
    Classification,
    Finding,
    ReviewState,
    Severity,
    Specialty,
    Verdict,
    initial_state,
)


def _populated() -> ReviewState:
    state = initial_state("003_auth_change", "t1")
    state.classification = Classification(
        change_type="code", summary="auth refactor", touched_areas=["auth"]
    )
    state.specialist_findings = [
        Finding(
            specialist=Specialty.security,
            severity=Severity.block,
            policy_id="SEC-03",
            description="d",
            confidence=0.9,
        )
    ]
    state.verdict = Verdict(outcome="rejected", rationale="r")
    return state


def test_state_round_trips_as_models_without_deprecation_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    serde = use_state_allowlist(InMemorySaver()).serde
    with caplog.at_level(logging.WARNING):
        back = serde.loads_typed(serde.dumps_typed(_populated()))

    assert isinstance(back, ReviewState)
    assert back.verdict is not None and back.verdict.outcome == "rejected"
    assert back.specialist_findings[0].severity is Severity.block
    assert "unregistered" not in caplog.text
    assert "Blocked" not in caplog.text


class _Undeclared(BaseModel):
    """A model outside gate.graph.state — serializable, but not on the allowlist."""

    value: str


def test_types_outside_the_allowlist_are_refused(caplog: pytest.LogCaptureFixture) -> None:
    """The allowlist is a boundary, not decoration: anything not declared is turned away."""
    serde = use_state_allowlist(InMemorySaver()).serde
    with caplog.at_level(logging.WARNING):
        back = serde.loads_typed(serde.dumps_typed(_Undeclared(value="x")))

    assert not isinstance(back, _Undeclared)
    assert "Blocked" in caplog.text
