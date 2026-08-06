"""State reducers and the initial-state factory (SKELETON §02, ITER_02 §04)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from gate.graph import state
from gate.graph.state import (
    EscalationRecord,
    Finding,
    Severity,
    Specialty,
    coalesce_escalation,
    initial_state,
    merge_findings,
)


def _finding(specialist: Specialty, policy_id: str) -> Finding:
    return Finding(
        specialist=specialist,
        severity=Severity.info,
        policy_id=policy_id,
        description="d",
        confidence=0.5,
    )


def test_merge_findings_keeps_the_other_specialty_in_a_parallel_write() -> None:
    existing = [_finding(Specialty.security, "SEC-01")]
    merged = merge_findings(existing, [_finding(Specialty.license, "LIC-01")])
    assert [f.policy_id for f in merged] == ["SEC-01", "LIC-01"]


def test_merge_findings_supersedes_a_specialty_on_a_fresh_batch() -> None:
    """A second investigation round must replace round one, not stack on top of it — otherwise
    a resolved ``needs_more_context`` finding would linger and burn the whole loop budget."""
    existing = [_finding(Specialty.security, "SEC-01"), _finding(Specialty.license, "LIC-01")]
    merged = merge_findings(existing, [_finding(Specialty.security, "SEC-99")])
    assert [(f.specialist, f.policy_id) for f in merged] == [
        (Specialty.license, "LIC-01"),
        (Specialty.security, "SEC-99"),
    ]


def test_coalesce_escalation_is_first_write_wins() -> None:
    first = EscalationRecord(reason="timeout", detail="first")
    second = EscalationRecord(reason="max_cycles", detail="second")
    assert coalesce_escalation(first, second) is first
    assert coalesce_escalation(None, second) is second
    assert coalesce_escalation(None, None) is None


def test_initial_state_carries_ids_and_nothing_else() -> None:
    state = initial_state("001_docs_typo", "thread-1")
    assert (state.pr_id, state.thread_id) == ("001_docs_typo", "thread-1")
    assert state.loop_count == 0
    assert state.verdict is None and state.route is None


def test_the_checkpoint_allowlist_covers_every_type_in_this_module() -> None:
    """A domain type missing from the allowlist deserializes with a warning today and will be
    refused outright by a future LangGraph. Fail here instead, when the type is added."""
    defined = {
        name
        for name, obj in vars(state).items()
        if isinstance(obj, type)
        and obj.__module__ == state.__name__
        and issubclass(obj, BaseModel | StrEnum)
    }
    assert {name for _module, name in state.CHECKPOINT_ALLOWLIST} == defined
    assert {module for module, _name in state.CHECKPOINT_ALLOWLIST} == {state.__name__}
