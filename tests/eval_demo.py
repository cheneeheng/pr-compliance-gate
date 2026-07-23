"""Behavioral eval: the full three-fixture demo arc, driven through the API (ITER_04 §06).

Marked ``llm`` — spins the real graph, hits the real Anthropic API, and needs the policy corpus
ingested first (``uv run python -m gate.intake.ingest``). Asserts each fixture's terminal
``verdict.outcome`` matches ``expected_outcome`` in its ``meta.json``. This subsumes the route
assertions of ``eval_routes`` (each terminal route is implied by the outcome it produces).
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gate.api.app import create_app

pytestmark = pytest.mark.llm

_POLL_TIMEOUT_S = 180.0
_POLL_INTERVAL_S = 2.0


def _wait(client: TestClient, tid: str, statuses: set[str]) -> dict[str, Any]:
    deadline = time.time() + _POLL_TIMEOUT_S
    while time.time() < deadline:
        state = client.get(f"/api/reviews/{tid}").json()
        if state["status"] in statuses:
            return state
        time.sleep(_POLL_INTERVAL_S)
    raise AssertionError(f"thread {tid} never reached {statuses}")


def _start(client: TestClient, pr_id: str) -> str:
    return client.post("/api/reviews", json={"pr_id": pr_id}).json()["thread_id"]


def _decide(client: TestClient, tid: str, action: str, note: str = "") -> None:
    r = client.post(f"/api/reviews/{tid}/decision", json={"action": action, "note": note})
    assert r.status_code == 200


def test_001_auto_approved() -> None:
    with TestClient(create_app()) as client:
        tid = _start(client, "001_docs_typo")
        state = _wait(client, tid, {"done"})
        assert state["verdict"]["outcome"] == "auto_approved"


def test_002_reject() -> None:
    with TestClient(create_app()) as client:
        tid = _start(client, "002_new_dependency")
        _wait(client, tid, {"awaiting_human"})
        _decide(client, tid, "reject")
        state = _wait(client, tid, {"done"})
        assert state["verdict"]["outcome"] == "rejected"


def test_003_request_info_then_approve() -> None:
    with TestClient(create_app()) as client:
        tid = _start(client, "003_auth_change")
        _wait(client, tid, {"awaiting_human"})
        _decide(client, tid, "request_info", "confirm the signature check is enforced")
        # Re-investigation runs, then the gate re-interrupts.
        _wait(client, tid, {"awaiting_human"})
        _decide(client, tid, "approve")
        state = _wait(client, tid, {"done"})
        assert state["verdict"]["outcome"] == "approved"
