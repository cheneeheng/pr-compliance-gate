"""Behavioral eval: each fixture takes its expected route through the graph (ITER_02 §06).

Marked ``llm`` — runs the real pipeline and needs a live ``ANTHROPIC_API_KEY`` plus an ingested
policy corpus (``uv run python -m gate.intake.ingest``). Asserts the route recorded as
``expected_route`` in each fixture's ``meta.json``, observed at the point the review first comes
to rest:

- ``auto_approve``   — terminal with ``verdict.outcome == "auto_approved"`` (cost gate #1 held)
- ``human_gate``     — paused at the interrupt, awaiting a human
- ``clean_verdict``  — terminal with a non-auto outcome (specialists ran and found nothing)

``eval_demo`` drives the same fixtures to their *final* outcome through the API; this eval stops
at the first resting point, which is where the routing decision itself is observable.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gate.api.app import create_app
from gate.intake.loader import list_prs, load_pr

pytestmark = pytest.mark.llm

_POLL_TIMEOUT_S = 180.0
_POLL_INTERVAL_S = 2.0


def _route_of(state: dict[str, Any]) -> str:
    if state["status"] == "awaiting_human":
        return "human_gate"
    outcome = (state.get("verdict") or {}).get("outcome")
    return "auto_approve" if outcome == "auto_approved" else "clean_verdict"


@pytest.mark.parametrize("pr_id", [s.pr_id for s in list_prs()])
def test_fixture_takes_expected_route(pr_id: str) -> None:
    expected = load_pr(pr_id).meta.get("expected_route")
    assert expected, f"fixture {pr_id} must declare expected_route"

    with TestClient(create_app()) as client:
        tid = client.post("/api/reviews", json={"pr_id": pr_id}).json()["thread_id"]
        deadline = time.time() + _POLL_TIMEOUT_S
        while time.time() < deadline:
            state = client.get(f"/api/reviews/{tid}").json()
            if state["status"] in {"awaiting_human", "done"}:
                assert _route_of(state) == expected
                return
            time.sleep(_POLL_INTERVAL_S)
    raise AssertionError(f"thread {tid} never came to rest")
