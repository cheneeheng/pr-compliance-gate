"""Model-tier factory and cost estimation.

One place owns model construction and message assembly. The Anthropic API takes the system
prompt as a top-level parameter, not a ``system``-role message; ``langchain-anthropic`` maps a
``SystemMessage`` to that parameter, so :func:`structured_call` builds the message array here
and no node ever constructs raw messages itself.
"""

from __future__ import annotations

from typing import TypeVar

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from gate.config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)

# Static per-model price table, USD per 1M tokens (input, output). An ESTIMATE for the demo's
# cost display — not billing truth. Keyed by the env-configured model id; unknown ids fall back
# to the specialist tier so a cost is always shown.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
}
_FALLBACK_PRICE: tuple[float, float] = (3.00, 15.00)


def _chat(model_id: str, settings: Settings, *, max_tokens: int) -> ChatAnthropic:
    return ChatAnthropic(
        model=model_id,  # type: ignore[call-arg]  # langchain-anthropic aliases model->model_name
        api_key=settings.anthropic_api_key or None,  # type: ignore[arg-type]
        temperature=0.0,
        max_tokens=max_tokens,
        timeout=None,
        stop=None,
    )


def classifier_model(settings: Settings | None = None) -> ChatAnthropic:
    settings = settings or get_settings()
    return _chat(settings.gate_model_classifier, settings, max_tokens=1024)


def specialist_model(settings: Settings | None = None) -> ChatAnthropic:
    settings = settings or get_settings()
    return _chat(settings.gate_model_specialist, settings, max_tokens=2048)


def investigator_model(settings: Settings | None = None) -> ChatAnthropic:
    settings = settings or get_settings()
    return _chat(settings.gate_model_investigator, settings, max_tokens=2048)


async def structured_call(
    model: ChatAnthropic, schema: type[T], system: str, user: str
) -> T:
    """Invoke ``model`` for a single structured-output turn. Owns message construction."""
    structured = model.with_structured_output(schema)
    result = await structured.ainvoke(
        [SystemMessage(content=system), HumanMessage(content=user)]
    )
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
