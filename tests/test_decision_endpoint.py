"""Decision-endpoint tests with a mocked graph (ITER_04 §04).

``ainvoke`` is a *synchronous* method on the fake that records its argument at call time (the
handler builds the coroutine synchronously before handing it to the background task), so the
resumed ``Command`` can be asserted deterministically without awaiting the background run.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from langgraph.types import Command

from gate.api.app import create_app


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


def _client_with(graph: _FakeGraph) -> TestClient:
    app = create_app()
    client = TestClient(app)
    client.__enter__()  # run lifespan (sets a real graph), then override it
    app.state.graph = graph
    return client


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
