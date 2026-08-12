"""The read + start half of the API against a fake graph (SKELETON §02, ITER_04 §04).

Handlers hold no business logic, so what is asserted here is the shaping: status derivation from
the checkpoint, tolerance of checkpointed values arriving as dicts *or* Pydantic instances, and
the fact that starting a review returns immediately with the run in the background.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from gate.api.app import create_app
from gate.api.routes import _spawn
from gate.graph.state import RiskAssessment, RiskLevel


class _Task:
    def __init__(self, interrupts: tuple[Any, ...]) -> None:
        self.interrupts = interrupts


class _Interrupt:
    def __init__(self, value: Any) -> None:
        self.value = value


class _Snapshot:
    def __init__(self, values: dict[str, Any], *, interrupted: bool = False) -> None:
        self.values = values
        self.tasks = (_Task((_Interrupt({"pr_id": "t", "risk": None}),)),) if interrupted else ()


class _FakeGraph:
    def __init__(self, snapshot: _Snapshot) -> None:
        self._snapshot = snapshot
        self.started: list[Any] = []

    async def aget_state(self, _config: dict[str, Any]) -> _Snapshot:
        return self._snapshot

    def ainvoke(self, arg: Any, _config: dict[str, Any]) -> Any:
        self.started.append(arg)

        async def _noop() -> dict[str, Any]:
            return {}

        return _noop()


@contextmanager
def _client_with(graph: _FakeGraph) -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as client:  # lifespan builds a real graph, then we swap ours in
        app.state.graph = graph
        yield client


def test_index_serves_the_single_page_ui() -> None:
    with _client_with(_FakeGraph(_Snapshot({}))) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "PR Compliance Gate" in response.text


def test_listing_fixtures_projects_only_what_the_picker_needs() -> None:
    with _client_with(_FakeGraph(_Snapshot({}))) as client:
        body = client.get("/api/prs").json()
    assert {s["pr_id"] for s in body} >= {"001_docs_typo", "002_new_dependency"}
    assert set(body[0]) == {"pr_id", "title", "files_changed_count"}


def test_starting_a_review_returns_before_the_run_finishes() -> None:
    graph = _FakeGraph(_Snapshot({}))
    with _client_with(graph) as client:
        body = client.post("/api/reviews", json={"pr_id": "001_docs_typo"}).json()
    assert body["thread_id"]
    assert graph.started[0].pr_id == "001_docs_typo"  # initial state, not a resume Command


def test_starting_a_review_on_an_unknown_fixture_is_a_422() -> None:
    graph = _FakeGraph(_Snapshot({}))
    with _client_with(graph) as client:
        response = client.post("/api/reviews", json={"pr_id": "nope"})
    assert response.status_code == 422
    assert graph.started == []  # validated up front: no half-created thread


def test_the_list_view_reads_risk_from_either_checkpoint_shape() -> None:
    """Checkpointed sub-values come back as dicts or as models depending on the resume path."""
    for risk in (RiskAssessment(score=80, level=RiskLevel.high), {"level": "high"}):
        graph = _FakeGraph(_Snapshot({"pr_id": "001_docs_typo", "risk": risk}))
        with _client_with(graph) as client:
            client.post("/api/reviews", json={"pr_id": "001_docs_typo"})
            rows = client.get("/api/reviews").json()
        assert rows[0]["risk_level"] == "high"
        assert rows[0]["status"] == "running"


def test_status_is_derived_from_the_checkpoint_not_stored() -> None:
    done = _FakeGraph(_Snapshot({"pr_id": "t", "verdict": {"outcome": "approved"}}))
    with _client_with(done) as client:
        assert client.get("/api/reviews/abc").json()["status"] == "done"

    waiting = _FakeGraph(_Snapshot({"pr_id": "t"}, interrupted=True))
    with _client_with(waiting) as client:
        assert client.get("/api/reviews/abc").json()["status"] == "awaiting_human"


def test_an_unknown_thread_is_a_404_everywhere() -> None:
    with _client_with(_FakeGraph(_Snapshot({}))) as client:
        assert client.get("/api/reviews/ghost").status_code == 404
        assert client.get("/api/reviews/ghost/pending").status_code == 404


def test_a_started_review_reads_as_running_before_its_first_checkpoint() -> None:
    """The graph runs in the background, so the UI can poll before any checkpoint exists. A 404
    there stops the page polling and strands the review -- report it as running instead."""
    with _client_with(_FakeGraph(_Snapshot({}))) as client:
        thread_id = client.post("/api/reviews", json={"pr_id": "001_docs_typo"}).json()["thread_id"]
        body = client.get(f"/api/reviews/{thread_id}").json()

    assert body == {
        "status": "running",
        "thread_id": thread_id,
        "pr_id": "001_docs_typo",
        "source": "fixture",
        "pr_ref": None,
        "error": None,
        # Empty because the default suite runs with GATE_OTEL_ENABLED=false, which is exactly
        # what makes the UI omit the "View trace" link rather than render a dead one.
        "langfuse_ui_base": "",
    }


def test_pending_returns_the_review_packet_or_204() -> None:
    with _client_with(_FakeGraph(_Snapshot({"pr_id": "t"}, interrupted=True))) as client:
        response = client.get("/api/reviews/abc/pending")
    assert response.status_code == 200 and response.json()["pr_id"] == "t"

    with _client_with(_FakeGraph(_Snapshot({"pr_id": "t"}))) as client:
        assert client.get("/api/reviews/abc/pending").status_code == 204


async def test_a_failed_background_run_is_recorded_not_crashed() -> None:
    """The graph runs detached; an exception there must not take the app down with it -- but it
    must not vanish either, or the UI polls a dead review as `running` forever."""
    state = SimpleNamespace(bg_tasks=set(), review_errors={})
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    async def _boom(_trace_id: str | None) -> None:
        raise RuntimeError("graph exploded")

    _spawn(request, _boom, pr_id="t", thread_id="abc", source="fixture")
    while request.app.state.bg_tasks:
        await asyncio.sleep(0)
    assert state.review_errors == {"abc": "graph exploded"}


def test_a_recorded_run_failure_reads_as_failed_in_both_views() -> None:
    """`failed` outranks a pending interrupt: a run that died at the gate cannot be resumed."""
    graph = _FakeGraph(_Snapshot({"pr_id": "001_docs_typo"}, interrupted=True))
    with _client_with(graph) as client:
        thread_id = client.post("/api/reviews", json={"pr_id": "001_docs_typo"}).json()["thread_id"]
        client.app.state.review_errors[thread_id] = "no API key"  # type: ignore[attr-defined]
        detail = client.get(f"/api/reviews/{thread_id}").json()
        rows = client.get("/api/reviews").json()

    assert detail["status"] == "failed" and detail["error"] == "no API key"
    assert rows[0]["status"] == "failed"
