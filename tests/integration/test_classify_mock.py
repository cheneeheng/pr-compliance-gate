"""Classifier node with mocked structured output — default-suite test (ITER_01 §04)."""

from __future__ import annotations

import pytest

from gate.graph.nodes import classify as classify_mod
from gate.graph.state import ChangeType, Classification, ReviewState


async def test_classify_sets_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(_model: object, _schema: object, _system: str, _user: str) -> Classification:
        return Classification(
            change_type=ChangeType.docs, summary="typo fix", touched_areas=["readme"]
        )

    monkeypatch.setattr(classify_mod, "classifier_model", lambda s=None: object())
    monkeypatch.setattr(classify_mod, "structured_call", _fake)

    update = await classify_mod.classify(ReviewState(pr_id="t", diff="some diff"))
    assert update["classification"].change_type == ChangeType.docs  # type: ignore[union-attr]
