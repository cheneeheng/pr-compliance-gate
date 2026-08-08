"""OpenTelemetry instrumentation, exported over OTLP/HTTP to a self-hosted Langfuse.

Instrumentation, not infrastructure: spans are written in application code with real semantic
conventions, and Langfuse is addressed purely as an OTLP endpoint. Point the same exporter at
Jaeger or Tempo and none of this changes.

Three gotchas are designed for here rather than debugged later:

1. **The graph run outlives its HTTP request.** ``POST /api/reviews`` returns as soon as the
   background task is spawned, so parenting node spans under the request span would attach a
   multi-minute run to a span that ended in milliseconds — and under a ``BatchSpanProcessor``
   that parent may already have been exported. ``gate.review.run`` is therefore opened as a
   **root** span inside the background task, carrying a **link** to the request span.
2. **Orphaned spans across the parallel fan-out.** LangGraph runs the two specialists as parallel
   branches, and OTel context lives in ``contextvars`` that do not follow into branches scheduled
   on separate tasks. Instrument naively and both specialist spans become trace roots. So the run
   span's context is captured *where the run span is opened* and stored per-thread here; the node
   decorator passes it explicitly as ``context=``. Capturing it inside the ``intake`` node
   instead is the tempting shortcut and is wrong — ``intake`` is itself a child of the run span,
   so every later node would parent to ``intake``.
3. **Spans lost on shutdown.** ``BatchSpanProcessor`` buffers; a process that exits without
   flushing drops the tail of every trace, which reads in the UI as runs that "end" mid-graph.
   :func:`shutdown_tracing` force-flushes, and the lifespan calls it in a ``finally`` *after*
   cancelling the tracked background tasks — reversed, a run still in flight writes to a
   shut-down provider and its tail spans vanish anyway.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Link, NoOpTracerProvider, Span, SpanContext

from gate.config import Settings

_TRACER_NAME = "gate"

# thread_id -> the run span's context. Populated by `review_run_span`, read by `traced_node`.
_RUN_CONTEXTS: dict[str, Context] = {}

# The provider this module installed. Held here rather than read back off the OTel global,
# because `trace.set_tracer_provider` is guarded by a process-wide `Once`: the second call — a
# second app instance, or the kill switch after an enabled run — is refused with a log warning
# and no effect. Relying on the global would make `GATE_OTEL_ENABLED=false` silently not work.
_provider: trace.TracerProvider | None = None


def _parse_headers(raw: str) -> dict[str, str]:
    """Parse the OTel `key=value,key2=value2` header convention."""
    pairs = (item.split("=", 1) for item in raw.split(",") if "=" in item)
    return {k.strip(): v.strip() for k, v in pairs}


def init_tracing(app: Any, settings: Settings) -> None:
    """Install the tracer provider. Called once from the FastAPI lifespan.

    ``GATE_OTEL_ENABLED=false`` installs a no-op provider instead: the app must run with the
    Langfuse container down, because a portfolio reviewer cloning the repo should not need Docker
    to see the gate work.

    No sampling (``ALWAYS_ON``). This is a demo with tens of runs; dropping traces to save volume
    buys nothing and makes the demo non-reproducible.
    """
    global _provider
    if not settings.gate_otel_enabled:
        _provider = NoOpTracerProvider()
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.gate_otel_service_name}),
        sampler=ALWAYS_ON,
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=settings.otel_exporter_otlp_endpoint,
                headers=_parse_headers(settings.otel_exporter_otlp_headers),
            )
        )
    )
    # Best effort for third-party instrumentation that reads the global; ours does not depend
    # on it succeeding (see the `_provider` comment).
    trace.set_tracer_provider(provider)
    # Request spans for the API surface. Deliberately NOT the parent of a graph run (see 1).
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    _provider = provider


def shutdown_tracing() -> None:
    """Force-flush and tear down the provider. Safe to call when tracing is disabled."""
    global _provider
    if isinstance(_provider, TracerProvider):
        _provider.shutdown()  # force-flushes, so the tail of every trace still reaches Langfuse
    _provider = None


def tracer() -> trace.Tracer:
    """The gate's tracer, from the provider this module installed."""
    provider = _provider
    return provider.get_tracer(_TRACER_NAME) if provider else trace.get_tracer(_TRACER_NAME)


def current_request_context() -> SpanContext | None:
    """The in-flight request span's context, captured at handler time to link the run to it."""
    span_context = trace.get_current_span().get_span_context()
    return span_context if span_context.is_valid else None


def trace_id_of(span: Span) -> str | None:
    """The 32-hex trace id the UI deep-links, or None under a no-op provider."""
    span_context = span.get_span_context()
    return format(span_context.trace_id, "032x") if span_context.is_valid else None


@contextmanager
def review_run_span(
    pr_id: str, thread_id: str, link_to: SpanContext | None = None
) -> Iterator[Span]:
    """Open the root span for one graph run and publish its context for the node decorator.

    ``context=Context()`` makes this a root even when a request span is ambient — the link, not
    parentage, is what ties the run back to the call that kicked it off.
    """
    links = [Link(link_to)] if link_to is not None else []
    with tracer().start_as_current_span(
        "gate.review.run",
        context=Context(),
        links=links,
        attributes={"gate.pr_id": pr_id, "gate.thread_id": thread_id},
    ) as span:
        _RUN_CONTEXTS[thread_id] = trace.set_span_in_context(span)
        try:
            yield span
        finally:
            _RUN_CONTEXTS.pop(thread_id, None)


def run_context(thread_id: str) -> Context | None:
    """The run span's context for ``thread_id``, or None outside a traced run."""
    return _RUN_CONTEXTS.get(thread_id)


def _flatten(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


def set_run_outcome_attributes(span: Span, values: dict[str, Any]) -> None:
    """Cost and guardrail attributes on the root span — the whole point of the instrumentation.

    The README *claims* low-risk PRs never touch Sonnet; a trace filtered on
    ``gate.risk.level=low`` with zero Sonnet spans *proves* it.
    """
    risk = values.get("risk")
    escalation = values.get("escalation")
    investigation = values.get("investigation")
    span.set_attributes(
        _flatten(
            {
                "gate.risk.level": _field(risk, "level"),
                "gate.route.taken": values.get("route"),
                "gate.loop_count.final": values.get("loop_count"),
                "gate.escalation.reason": _field(escalation, "reason"),
                "gate.cost.investigator_usd": _field(investigation, "cost_estimate_usd"),
                "gate.specialists.timed_out": _field(escalation, "reason") == "timeout",
            }
        )
    )


def _field(value: Any, name: str) -> Any:
    """Read a field from a checkpointed sub-value, which may be a dict or a Pydantic model."""
    if value is None:
        return None
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(raw) if isinstance(raw, str) else raw
