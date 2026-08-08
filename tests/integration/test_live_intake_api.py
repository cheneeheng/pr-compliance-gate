"""The API and graph seams live intake adds (ITER_03_v2 §02/§04).

Two things are asserted here that nothing else covers: `POST /api/reviews` accepts exactly one of
`pr_id`/`pr_url` and preflights before a thread exists, and the intake short-circuit reaches the
human gate **without a single LLM call** — "no spend" is the whole point of that edge, so it is
asserted against a model factory that raises rather than inferred from a route string.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from gate.api.app import create_app
from gate.errors import InvalidPRRefError
from gate.graph.build import _route_after_intake, compile_graph
from gate.graph.checkpoint import use_state_allowlist
from gate.graph.nodes import classify as classify_mod
from gate.graph.nodes import specialists as specialists_mod
from gate.graph.state import EscalationRecord, PRRef, ReviewState
from gate.intake.sources import github_mcp, select_source
from gate.intake.sources.fixture import FixturePRSource
from gate.intake.sources.github_mcp import GitHubMCPSource

_URL = "https://github.com/acme/widgets/pull/42"
_HEAD_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


# ---- adapter selection --------------------------------------------------------------------------


def test_exactly_one_reference_is_required() -> None:
    assert isinstance(select_source("001_docs_typo", None)[0], FixturePRSource)
    assert isinstance(select_source(None, _URL)[0], GitHubMCPSource)

    for both_or_neither in ((None, None), ("001_docs_typo", _URL)):
        with pytest.raises(InvalidPRRefError, match="exactly one"):
            select_source(*both_or_neither)


# ---- the intake short-circuit --------------------------------------------------------------------


def test_intake_routes_a_seeded_escalation_straight_to_the_human_gate() -> None:
    escalated = ReviewState(
        pr_id="acme/widgets#42",
        escalation=EscalationRecord(reason="source_unavailable", detail="clone failed"),
    )
    assert _route_after_intake(escalated) == "human_gate"
    assert _route_after_intake(ReviewState(pr_id="p")) == "classify"


async def test_a_failed_load_reaches_the_human_gate_without_any_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routing a diff-less review through classify/risk/specialists would ask Haiku to classify
    nothing and Sonnet to review nothing. The models are stubbed to raise, so any spend fails."""

    def _no_spend(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an escalated intake must not invoke a model")

    monkeypatch.setattr(classify_mod, "classifier_model", _no_spend)
    monkeypatch.setattr(specialists_mod, "specialist_model", _no_spend)

    graph = compile_graph(use_state_allowlist(InMemorySaver()))
    state = ReviewState(
        pr_id="acme/widgets#42",
        thread_id="t-esc",
        source="github",
        escalation=EscalationRecord(reason="pr_too_large", detail="412 changed files"),
    )

    await graph.ainvoke(state, {"configurable": {"thread_id": "t-esc"}})

    # The graph still runs and still interrupts at the gate — the review is checkpointed,
    # resumable and reachable through /pending like any other.
    snapshot = await graph.aget_state({"configurable": {"thread_id": "t-esc"}})
    interrupts = [i for task in snapshot.tasks for i in task.interrupts]
    assert interrupts, "the escalated review must be waiting at the human gate"
    assert snapshot.values.get("classification") is None
    assert not snapshot.values.get("specialist_findings")
    assert interrupts[0].value["escalation"]["reason"] == "pr_too_large"


# ---- POST /api/reviews with a URL ----------------------------------------------------------------


class _Snapshot:
    values: dict[str, Any] = {}
    tasks: tuple[Any, ...] = ()
    next: tuple[str, ...] = ()


class _FakeGraph:
    async def aget_state(self, _config: dict[str, Any]) -> _Snapshot:
        return _Snapshot()

    async def ainvoke(self, _state: Any, _config: dict[str, Any]) -> dict[str, Any]:
        return {}


@contextmanager
def _client(graph: Any) -> Iterator[TestClient]:
    app = create_app()
    original = app.router.lifespan_context

    @asynccontextmanager
    async def _lifespan(inner: Any) -> Any:
        async with original(inner):
            inner.state.graph = graph
            yield

    app.router.lifespan_context = _lifespan
    with TestClient(app) as client:
        yield client


@pytest.fixture
def stub_github(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _resolve(_self: Any, _ref_input: str) -> PRRef:
        return PRRef(
            owner="acme",
            repo="widgets",
            number=42,
            head_sha=_HEAD_SHA,
            html_url=_URL,
            title="Add retry backoff",
        )

    async def _load(_self: Any, _ref_input: str, ref: PRRef | None) -> Any:
        return github_mcp.PRPayload(ref=ref, pr_id="acme/widgets#42", diff="", files_changed=[])

    monkeypatch.setattr(GitHubMCPSource, "resolve", _resolve)
    monkeypatch.setattr(GitHubMCPSource, "load", _load)


def test_a_live_pr_url_starts_a_review_badged_as_github(stub_github: None) -> None:
    with _client(_FakeGraph()) as client:
        thread_id = client.post("/api/reviews", json={"pr_url": _URL}).json()["thread_id"]
        listing = client.get("/api/reviews").json()
        detail = client.get(f"/api/reviews/{thread_id}").json()

    assert listing[0]["source"] == "github"
    assert listing[0]["pr_id"] == "acme/widgets#42"
    # Registered before the first checkpoint exists, so the badge is there during the clone.
    assert detail["source"] == "github"
    assert detail["pr_ref"]["html_url"] == _URL


def test_supplying_both_or_neither_reference_is_a_422() -> None:
    with _client(_FakeGraph()) as client:
        assert client.post("/api/reviews", json={}).status_code == 422
        both = client.post("/api/reviews", json={"pr_id": "001_docs_typo", "pr_url": _URL})
        assert both.status_code == 422
        assert "exactly one" in both.json()["error"]


def test_a_preflight_failure_creates_no_review_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user who typoed a URL gets an error, not a review row that fails a minute later."""
    with _client(_FakeGraph()) as client:
        response = client.post("/api/reviews", json={"pr_url": "https://github.com/acme/x"})
        assert response.status_code == 422
        assert client.get("/api/reviews").json() == []
