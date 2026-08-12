"""Model-tier factory and cost estimation.

One place owns model construction and message assembly. The Anthropic API takes the system
prompt as a top-level parameter, not a ``system``-role message; ``langchain-anthropic`` maps a
``SystemMessage`` to that parameter, so :func:`structured_call` builds the message array here
and no node ever constructs raw messages itself.
"""

from __future__ import annotations

import re
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from opentelemetry.trace import Span
from pydantic import BaseModel

from gate.config import ModelEffort, Settings, get_settings
from gate.telemetry.tracing import tracer

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
    usage = UsageMetadataCallbackHandler()
    with tracer().start_as_current_span(
        f"gate.llm.{schema.__name__}",
        attributes={
            "gen_ai.system": "anthropic",
            "gen_ai.request.model": model.model,
            "gen_ai.operation.name": "chat",
            # Langfuse renders these as the prompt/completion pair rather than a bare timing bar.
            "input.value": f"{system}\n\n{user}",
            # This pairing is not optional bookkeeping: an effort setting turns on adaptive
            # thinking, the API then refuses to force a tool call, and `function_calling` loses
            # its guarantee. That is the exact combination that silently broke the specialists in
            # v1 — recorded here, the next occurrence is a filter query, not a debugging session.
            "gate.llm.effort": model.reasoning_effort or "",
            "gate.llm.structured_output_method": method,
        },
    ) as span:
        result = await structured.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)],
            config={"callbacks": [usage]},
        )
        _record_usage(span, model.model, usage)
        span.set_attribute("output.value", str(result))
    return result  # type: ignore[return-value]  # with_structured_output returns schema instance


def _record_usage(span: Span, model_id: str, handler: UsageMetadataCallbackHandler) -> None:
    """Copy token counts off the callback handler onto the LLM span.

    ``with_structured_output`` returns the parsed schema instance, not the raw ``AIMessage``, so
    usage is collected through a callback rather than read off the result — the alternative
    (``include_raw=True``) would change the return shape and move schema-validation failures from
    an exception into a field nobody checks.

    Thinking tokens get their own attribute rather than being folded into
    ``gen_ai.usage.output_tokens``, which stays exactly as the API reports it: an effort setting
    makes thinking a real cost line, and burying it inside a conventional attribute would make
    the two unreconcilable against a bill.
    """
    metadata = handler.usage_metadata.get(model_id)
    if metadata is None:
        return
    output_details = metadata.get("output_token_details") or {}
    span.set_attributes(
        {
            "gen_ai.usage.input_tokens": metadata.get("input_tokens", 0),
            "gen_ai.usage.output_tokens": metadata.get("output_tokens", 0),
            "gate.llm.thinking_tokens": output_details.get("reasoning", 0),
            "gate.llm.cost_estimate_usd": estimate_cost(
                model_id, metadata.get("input_tokens", 0), metadata.get("output_tokens", 0)
            ),
        }
    )


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


def truncate_diff_by_file(diff: str, max_chars: int) -> str:
    """Assemble a diff file-by-file until the budget is reached, rather than slicing mid-hunk.

    Real pull requests are larger and noisier than fixtures, so the budget bites more often. The
    strategy is unchanged in spirit — the same ``GATE_MAX_DIFF_CHARS`` cap — but the classifier
    and specialists now always see *whole* hunks, and the files the budget dropped are named in
    the trailing marker so a specialist can flag "I could not see X" instead of judging in
    ignorance.
    """
    if len(diff) <= max_chars:
        return diff
    sections = re.split(r"(?m)^(?=diff --git )", diff)
    kept: list[str] = []
    dropped: list[str] = []
    used = 0
    for section in sections:
        header = re.match(r"diff --git a/.+ b/(.+)", section)
        name = header.group(1) if header else "(preamble)"
        if header is not None and used + len(section) > max_chars:
            dropped.append(name)
            continue
        kept.append(section)
        used += len(section)
    body = "".join(kept)
    if not body.strip() or not dropped:
        # Either nothing survived (one file bigger than the whole budget) or nothing could be
        # dropped (no `diff --git` headers at all). An empty diff — or a still-oversized one —
        # is useless to a specialist, so fall back to the blunt head+tail cut.
        return truncate_diff(diff, max_chars)
    return f"{body}\n[... {len(dropped)} file(s) omitted for size: {', '.join(dropped)} ...]"
