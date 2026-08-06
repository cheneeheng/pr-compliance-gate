"""Model-tier factory and cost estimation.

One place owns model construction and message assembly. The Anthropic API takes the system
prompt as a top-level parameter, not a ``system``-role message; ``langchain-anthropic`` maps a
``SystemMessage`` to that parameter, so :func:`structured_call` builds the message array here
and no node ever constructs raw messages itself.
"""

from __future__ import annotations

from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from gate.config import ModelEffort, Settings, get_settings

# Static per-model price table, USD per 1M tokens (input, output). An ESTIMATE for the demo's
# cost display — not billing truth. Keyed by the env-configured model id; unknown ids fall back
# to the specialist tier so a cost is always shown.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
}
_FALLBACK_PRICE: tuple[float, float] = (3.00, 15.00)


def effort_for(model_id: str, effort: ModelEffort) -> ModelEffort | None:
    """The effort to send alongside ``model_id``, or ``None`` where the tier takes none.

    Sonnet/Opus/Fable accept a reasoning-effort setting; Haiku has no effort control and the API
    rejects the parameter, so the configured id is matched rather than the tier assumed — a tier
    pointed at Haiku is covered wherever it appears. Shared with the Agent SDK loop, which passes
    the same value as ``ClaudeAgentOptions.effort``.
    """
    return None if "haiku" in model_id.lower() else effort


def _chat(
    model_id: str, settings: Settings, *, max_tokens: int, effort: ModelEffort
) -> ChatAnthropic:
    resolved = effort_for(model_id, effort)
    return ChatAnthropic(
        model=model_id,  # langchain-anthropic aliases model->model_name
        api_key=settings.anthropic_api_key or None,
        # An effort setting turns on adaptive thinking, which the API pairs with its own default
        # temperature; pinning temperature=0 is only safe on the tiers that take no effort.
        temperature=None if resolved else 0.0,
        max_tokens=max_tokens,
        reasoning_effort=resolved,
        timeout=None,
        stop=None,
    )


def classifier_model(settings: Settings | None = None) -> ChatAnthropic:
    settings = settings or get_settings()
    return _chat(
        settings.gate_model_classifier,
        settings,
        max_tokens=1024,
        effort=settings.gate_model_classifier_effort,
    )


def specialist_model(settings: Settings | None = None) -> ChatAnthropic:
    settings = settings or get_settings()
    return _chat(
        settings.gate_model_specialist,
        settings,
        max_tokens=2048,
        effort=settings.gate_model_specialist_effort,
    )


def investigator_model(settings: Settings | None = None) -> ChatAnthropic:
    settings = settings or get_settings()
    return _chat(
        settings.gate_model_investigator,
        settings,
        max_tokens=2048,
        effort=settings.gate_model_investigator_effort,
    )


async def structured_call[T: BaseModel](
    model: ChatAnthropic, schema: type[T], system: str, user: str
) -> T:
    """Invoke ``model`` for a single structured-output turn. Owns message construction.

    The structured-output method follows the effort setting. An effort turns on adaptive
    thinking, and the API refuses to force a tool call while thinking is on — so the default
    ``function_calling`` method silently loses its guarantee there and the model is free to
    answer in prose, which surfaces as a validation error on the schema. Claude's dedicated
    structured output carries no such restriction. Tiers without an effort keep
    ``function_calling``, which needs no support beyond tool use.
    """
    method: Literal["function_calling", "json_schema"] = (
        "json_schema" if model.reasoning_effort else "function_calling"
    )
    structured = model.with_structured_output(schema, method=method)
    result = await structured.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
    return result  # type: ignore[return-value]  # with_structured_output returns schema instance


def estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Rough USD estimate from token counts and the static price table."""
    in_price, out_price = PRICE_TABLE.get(model_id, _FALLBACK_PRICE)
    return round(input_tokens / 1_000_000 * in_price + output_tokens / 1_000_000 * out_price, 6)


def truncate_diff(diff: str, max_chars: int) -> str:
    """Head+tail truncation with a marker so oversized diffs can't blow the context window."""
    if len(diff) <= max_chars:
        return diff
    half = max_chars // 2
    dropped = len(diff) - 2 * half
    return f"{diff[:half]}\n[... truncated {dropped} chars ...]\n{diff[-half:]}"
