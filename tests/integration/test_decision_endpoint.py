"""Decision-endpoint tests with a mocked graph (ITER_04 §04).

``ainvoke`` is a *synchronous* method on the fake that records its argument at call time (the
handler builds the coroutine synchronously before handing it to the background task), so the
resumed ``Command`` can be asserted deterministically without awaiting the background run.

The last test covers the resume *routing* the endpoint hands off to (ITER_04 §04 step 3) with
the real graph functions rather than the fake: request_info -> investigate -> reconcile, and at
an exhausted loop budget the gate re-interrupts with ``escalation.reason="max_cycles"``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.types import Command

from gate.api.app import create_app
from gate.config import get_settings
from gate.graph.build import _route_after_gate
from gate.graph.nodes.investigate import investigate
from gate.graph.nodes.reconcile import ROUTE_HUMAN_GATE, reconcile
from gate.graph.state import (
    DecisionAction,
    Finding,
    HumanDecision,
    InvestigationReport,
    ReviewState,
    Severity,
    Specialty,
)


class _Task:
    def __init__(self, interrupts: tuple[Any, ...]) -> None:
        self.interrupts = interrupts


class _Interrupt:
    def __init__(self, value: Any) -> None:
        self.value = value


class _Snapshot:
    def __init__(self, values: dict[str, Any], *, interrupted: bool) -> None:
        self.values = values
        self.tasks = (_Task((_Interrupt({"pr_id": "t"}),)),) if interrupted else ()


class _FakeGraph:
    def __init__(self, snapshot: _Snapshot) -> None:
        self._snapshot = snapshot
        self.invoked_with: Any = None

    async def aget_state(self, _config: dict[str, Any]) -> _Snapshot:
        return self._snapshot

    def ainvoke(self, arg: Any, _config: dict[str, Any]) -> Any:
        self.invoked_with = arg  # recorded synchronously at call time

        async def _noop() -> dict[str, Any]:
            return {}

        return _noop()


@contextmanager
def _client_with(graph: _FakeGraph) -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as client:  # run lifespan once (sets a real graph), then override it
        app.state.graph = graph
        yield client


def test_decision_409_when_not_interrupted() -> None:
    graph = _FakeGraph(_Snapshot({"pr_id": "t"}, interrupted=False))
    with _client_with(graph) as client:
        r = client.post("/api/reviews/abc/decision", json={"action": "approve", "note": ""})
    assert r.status_code == 409
    assert graph.invoked_with is None  # no resume attempted


def test_decision_404_when_unknown_thread() -> None:
    graph = _FakeGraph(_Snapshot({}, interrupted=False))
    with _client_with(graph) as client:
        r = client.post("/api/reviews/ghost/decision", json={"action": "approve", "note": ""})
    assert r.status_code == 404


def test_decision_resumes_with_command() -> None:
    graph = _FakeGraph(_Snapshot({"pr_id": "t"}, interrupted=True))
    with _client_with(graph) as client:
        r = client.post(
            "/api/reviews/abc/decision", json={"action": "request_info", "note": "look at auth"}
        )
    assert r.status_code == 200
    assert r.json() == {"status": "resumed"}
    assert isinstance(graph.invoked_with, Command)
    assert graph.invoked_with.resume == {"action": "request_info", "note": "look at auth"}


async def test_request_info_at_exhausted_budget_reescalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(
        "gate.graph.nodes.investigate.run_investigation",
        lambda _state, _settings: _inconclusive(),
    )
    state = ReviewState(
        pr_id="t",
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
        human_decision=HumanDecision(action=DecisionAction.request_info, note="check auth"),
        loop_count=settings.gate_max_cycles - 1,  # this investigation spends the last cycle
    )

    assert _route_after_gate(state) == "investigate"

    update = await investigate(state)
    resumed = state.model_copy(update=update)
    assert resumed.loop_count == settings.gate_max_cycles  # sole increment, budget now spent

    routed = await reconcile(resumed)
    assert routed["route"] == ROUTE_HUMAN_GATE
    assert routed["escalation"].reason == "max_cycles"  # type: ignore[union-attr]


async def _inconclusive() -> InvestigationReport:
    return InvestigationReport(summary="investigation inconclusive: nothing new")
