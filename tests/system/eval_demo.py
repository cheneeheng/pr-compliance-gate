"""Behavioral eval: the full three-fixture demo arc, driven through the API (ITER_04 §06).

Marked ``llm`` — spins the real graph, hits the real Anthropic API, and needs a policy corpus
that passes ``validate_policy_index``. Asserts each fixture's terminal ``verdict.outcome`` matches
``expected_outcome`` in its ``meta.json``. ``eval_routes`` covers the complementary half: the
route each fixture takes to its first resting point.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gate.api.app import create_app

pytestmark = pytest.mark.llm

_POLL_TIMEOUT_S = 180.0
_POLL_INTERVAL_S = 2.0


def _wait(
    client: TestClient,
    tid: str,
    statuses: set[str],
    also: Callable[[dict[str, Any]], bool] = lambda _s: True,
) -> dict[str, Any]:
    """Poll until the review reaches one of ``statuses`` and satisfies ``also``.

    ``also`` guards against reading the *pre-resume* checkpoint: the decision endpoint spawns the
    resume as a background task, so a poll issued immediately after it returns can still observe
    the interrupt that was just answered.
    """
    deadline = time.time() + _POLL_TIMEOUT_S
    while time.time() < deadline:
        state = client.get(f"/api/reviews/{tid}").json()
        if state["status"] in statuses and also(state):
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
        gated = _wait(client, tid, {"awaiting_human"})
        cycles = gated.get("loop_count", 0)
        _decide(client, tid, "request_info", "confirm the signature check is enforced")
        # Re-investigation runs (spending a loop cycle), then the gate re-interrupts.
        _wait(client, tid, {"awaiting_human"}, lambda s: s.get("loop_count", 0) > cycles)
        _decide(client, tid, "approve")
        state = _wait(client, tid, {"done"})
        assert state["verdict"]["outcome"] == "approved"
