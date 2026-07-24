"""Model-tier factory, message assembly, cost estimation, diff truncation (SKELETON §06)."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from gate.config import Settings
from gate.graph.state import Classification
from gate.llm.models import (
    _FALLBACK_PRICE,
    PRICE_TABLE,
    classifier_model,
    estimate_cost,
    investigator_model,
    specialist_model,
    structured_call,
    truncate_diff,
)

_SETTINGS = Settings(
    anthropic_api_key="test-key",
    gate_model_classifier="claude-haiku-4-5",
    gate_model_specialist="claude-sonnet-4-6",
    gate_model_investigator="claude-sonnet-4-6",
)


def test_each_tier_uses_its_configured_model() -> None:
    assert classifier_model(_SETTINGS).model == _SETTINGS.gate_model_classifier
    assert specialist_model(_SETTINGS).model == _SETTINGS.gate_model_specialist
    assert investigator_model(_SETTINGS).model == _SETTINGS.gate_model_investigator


def test_the_cheap_tier_gets_a_smaller_output_budget() -> None:
    assert classifier_model(_SETTINGS).max_tokens < specialist_model(_SETTINGS).max_tokens


def test_factories_fall_back_to_the_settings_singleton() -> None:
    # No argument: the factory resolves get_settings() itself (conftest points it at test env).
    assert classifier_model().model
    assert specialist_model().model
    assert investigator_model().model


class _Structured:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def ainvoke(self, messages: list[Any]) -> Classification:
        self.messages = messages
        return Classification(change_type="docs", summary="s")


class _Model:
    def __init__(self) -> None:
        self.structured = _Structured()
        self.schema: Any = None

    def with_structured_output(self, schema: Any) -> _Structured:
        self.schema = schema
        return self.structured


async def test_structured_call_owns_message_assembly() -> None:
    """No node builds raw messages: the system prompt goes in a SystemMessage, which
    langchain-anthropic maps onto the API's top-level system parameter."""
    model = _Model()
    result = await structured_call(model, Classification, "SYSTEM", "USER")  # type: ignore[arg-type]

    assert model.schema is Classification
    system, user = model.structured.messages
    assert isinstance(system, SystemMessage) and system.content == "SYSTEM"
    assert isinstance(user, HumanMessage) and user.content == "USER"
    assert result.change_type == "docs"


def test_estimate_cost_uses_the_price_table_then_the_fallback() -> None:
    in_price, out_price = PRICE_TABLE["claude-haiku-4-5"]
    assert estimate_cost("claude-haiku-4-5", 1_000_000, 1_000_000) == in_price + out_price
    assert estimate_cost("some-unlisted-model", 1_000_000, 0) == _FALLBACK_PRICE[0]


def test_truncate_diff_keeps_head_and_tail_and_marks_the_gap() -> None:
    diff = "H" * 100 + "M" * 100 + "T" * 100
    out = truncate_diff(diff, 100)
    assert out.startswith("H" * 50) and out.endswith("T" * 50)
    assert "[... truncated 200 chars ...]" in out


def test_truncate_diff_leaves_a_short_diff_alone() -> None:
    assert truncate_diff("short", 100) == "short"
