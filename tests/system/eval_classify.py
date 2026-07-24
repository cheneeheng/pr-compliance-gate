"""Behavioral eval: each fixture classifies to its expected change_type (ITER_01 §06).

Marked ``llm`` — hits the real Anthropic API, excluded from the default run.
"""

from __future__ import annotations

import pytest

from gate.graph.nodes.classify import classify
from gate.graph.state import ReviewState
from gate.intake.loader import list_prs, load_pr

pytestmark = pytest.mark.llm


@pytest.mark.parametrize("pr_id", [s.pr_id for s in list_prs()])
async def test_classifies_to_expected(pr_id: str) -> None:
    fixture = load_pr(pr_id)
    expected = fixture.meta.get("expected_classification")
    state = ReviewState(pr_id=pr_id, diff=fixture.diff, files_changed=fixture.files_changed)
    update = await classify(state)
    assert update["classification"].change_type.value == expected  # type: ignore[union-attr]
