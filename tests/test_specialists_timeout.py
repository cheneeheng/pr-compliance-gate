"""Specialist timeout -> escalation, without hanging the graph (ITER_02 §04)."""

from __future__ import annotations

import asyncio

import pytest

from gate.config import Settings
from gate.graph.nodes import specialists
from gate.graph.state import ReviewState


async def test_specialist_timeout_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(anthropic_api_key="x", gate_specialist_timeout_s=0.01)
    monkeypatch.setattr(specialists, "get_settings", lambda: settings)
    monkeypatch.setattr(specialists, "specialist_model", lambda s=None: object())

    async def _slow(*_a: object, **_k: object) -> specialists._FindingList:
        await asyncio.sleep(0.2)
        return specialists._FindingList(findings=[])

    monkeypatch.setattr(specialists, "structured_call", _slow)

    update = await specialists.security_specialist(ReviewState(pr_id="t", diff="x"))
    assert update["escalation"].reason == "timeout"  # type: ignore[union-attr]
    assert "specialist_findings" not in update
