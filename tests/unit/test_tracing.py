"""OpenTelemetry instrumentation (ITER_02_v2 §04), asserted through an in-memory exporter.

Three of these pin gotchas that were designed for rather than debugged: the parallel fan-out must
not orphan the specialist spans, the run span must be a *root* linked to the request span rather
than its child, and a disabled-tracing run must still reach a verdict. The last is a branch under
``fail_under = 100``, so it is required, not optional politeness.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from gate.api.app import _drain
from gate.config import Settings
from gate.graph.nodes import traced_node
from gate.graph.state import ReviewState
from gate.llm.models import structured_call
from gate.logging import _inject_trace_context
from gate.telemetry import tracing as tracing_mod
from gate.telemetry.tracing import (
    _parse_headers,
    current_request_context,
    init_tracing,
    review_run_span,
    run_context,
    set_run_outcome_attributes,
    shutdown_tracing,
    trace_id_of,
)


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """A real SDK provider writing to memory. SimpleSpanProcessor so spans land synchronously.

    Installed through the module's own `_provider` rather than `trace.set_tracer_provider`,
    which is a process-wide once-guard and would be refused after the first test that sets it.
    """
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    monkeypatch.setattr(tracing_mod, "_provider", provider)
    yield memory


def _named(spans: tuple[ReadableSpan, ...], name: str) -> ReadableSpan:
    return next(s for s in spans if s.name == name)


async def _node(state: ReviewState) -> dict[str, object]:
    return {"route": "verdict"}


# ---- node spans -------------------------------------------------------------------------------


async def test_every_node_gets_a_span_carrying_its_identifiers(
    exporter: InMemorySpanExporter,
) -> None:
    state = ReviewState(pr_id="001_docs_typo", thread_id="t1", loop_count=1)

    await traced_node(_node)(state)

    span = exporter.get_finished_spans()[0]
    assert span.name == "gate.node._node"
    assert span.attributes["gate.pr_id"] == "001_docs_typo"
    assert span.attributes["gate.thread_id"] == "t1"
    assert span.attributes["gate.loop_count"] == 1
    assert span.attributes["gate.node.route"] == "verdict"


async def test_a_node_that_writes_no_route_records_none(exporter: InMemorySpanExporter) -> None:
    async def _quiet(_state: ReviewState) -> dict[str, object]:
        return {}

    await traced_node(_quiet)(ReviewState(pr_id="p", thread_id="t"))

    assert "gate.node.route" not in exporter.get_finished_spans()[0].attributes


async def test_a_node_failure_is_recorded_on_its_span(exporter: InMemorySpanExporter) -> None:
    async def _boom(_state: ReviewState) -> dict[str, object]:
        raise RuntimeError("node exploded")

    with pytest.raises(RuntimeError):
        await traced_node(_boom)(ReviewState(pr_id="p", thread_id="t"))

    span = exporter.get_finished_spans()[0]
    assert span.status.status_code is trace.StatusCode.ERROR
    assert span.events  # record_exception wrote one


async def test_a_human_gate_interrupt_is_control_flow_not_an_error(
    exporter: InMemorySpanExporter,
) -> None:
    """A span marked ERROR on every HITL review would make the status useless for real failures."""
    from langgraph.errors import GraphInterrupt

    async def _pause(_state: ReviewState) -> dict[str, object]:
        raise GraphInterrupt(())

    with pytest.raises(GraphInterrupt):
        await traced_node(_pause)(ReviewState(pr_id="p", thread_id="t"))

    span = exporter.get_finished_spans()[0]
    assert span.status.status_code is not trace.StatusCode.ERROR
    assert span.attributes["gate.node.interrupted"] is True


# ---- the run span -----------------------------------------------------------------------------


async def test_the_parallel_fan_out_shares_the_run_span_as_parent(
    exporter: InMemorySpanExporter,
) -> None:
    """OTel context lives in contextvars that do not follow into LangGraph's parallel branches.
    The node decorator therefore takes the run context explicitly; without that both specialist
    spans become trace roots."""
    import asyncio

    state = ReviewState(pr_id="p", thread_id="t-fan")
    with review_run_span("p", "t-fan") as run:
        # Separate tasks: exactly the ambient-context break the explicit `context=` defends.
        await asyncio.gather(
            asyncio.create_task(traced_node(_node)(state)),
            asyncio.create_task(traced_node(_node)(state)),
        )

    node_spans = [s for s in exporter.get_finished_spans() if s.name == "gate.node._node"]
    assert len(node_spans) == 2
    assert {s.parent.span_id for s in node_spans} == {run.get_span_context().span_id}


async def test_the_run_span_is_a_root_linked_to_the_request_span(
    exporter: InMemorySpanExporter,
) -> None:
    """The handler returns before the run finishes, so parenting the run under the request span
    would attach a multi-minute run to a span that ended in milliseconds."""
    with tracing_mod.tracer().start_as_current_span("POST /api/reviews"):
        request_context = current_request_context()
        with review_run_span("p", "t-link", request_context) as run:
            pass

    assert run.parent is None  # a root, despite an ambient request span
    assert run.links[0].context.span_id == request_context.span_id


async def test_the_run_context_is_published_only_for_the_life_of_the_run(
    exporter: InMemorySpanExporter,
) -> None:
    assert run_context("t-scope") is None
    with review_run_span("p", "t-scope"):
        assert run_context("t-scope") is not None
    assert run_context("t-scope") is None


async def test_the_run_span_carries_the_cost_and_guardrail_attributes(
    exporter: InMemorySpanExporter,
) -> None:
    """The README *claims* low-risk PRs never touch Sonnet; these attributes are what prove it."""
    with review_run_span("p", "t-out") as span:
        set_run_outcome_attributes(
            span,
            {
                "risk": {"level": "low"},  # dict form: checkpointed values arrive either way
                "route": "verdict",
                "loop_count": 2,
                "escalation": None,
                "investigation": {"cost_estimate_usd": 0.02},
            },
        )

    run = _named(tuple(exporter.get_finished_spans()), "gate.review.run")
    assert run.attributes["gate.risk.level"] == "low"
    assert run.attributes["gate.route.taken"] == "verdict"
    assert run.attributes["gate.loop_count.final"] == 2
    assert run.attributes["gate.cost.investigator_usd"] == 0.02
    assert run.attributes["gate.specialists.timed_out"] is False


async def test_a_specialist_timeout_is_visible_on_the_run_span(
    exporter: InMemorySpanExporter,
) -> None:
    from gate.graph.state import EscalationRecord

    with review_run_span("p", "t-timeout") as span:
        # Pydantic form this time — the same reader must handle both.
        set_run_outcome_attributes(
            span, {"escalation": EscalationRecord(reason="timeout", detail="d")}
        )

    run = _named(tuple(exporter.get_finished_spans()), "gate.review.run")
    assert run.attributes["gate.escalation.reason"] == "timeout"
    assert run.attributes["gate.specialists.timed_out"] is True


# ---- LLM spans --------------------------------------------------------------------------------


class _Structured:
    def __init__(self, usage: dict[str, Any] | None) -> None:
        self._usage = usage

    async def ainvoke(self, _messages: list[Any], config: Any = None) -> Classification:
        if self._usage is not None:
            for handler in config["callbacks"]:
                handler.usage_metadata["claude-sonnet-5"] = self._usage
        return Classification(change_type="code", summary="s")


class _Model:
    def __init__(self, usage: dict[str, Any] | None = None) -> None:
        self.model = "claude-sonnet-5"
        self.reasoning_effort = "high"
        self._structured = _Structured(usage)

    def with_structured_output(self, _schema: Any, method: str) -> _Structured:
        self.method = method
        return self._structured


from gate.graph.state import Classification  # noqa: E402 — used by the stubs above


async def test_an_llm_call_records_tokens_effort_and_the_structured_output_method(
    exporter: InMemorySpanExporter,
) -> None:
    """effort + structured_output_method are the pairing that silently broke specialists in v1."""
    model = _Model(
        usage={
            "input_tokens": 1000,
            "output_tokens": 200,
            "output_token_details": {"reasoning": 150},
        }
    )

    await structured_call(model, Classification, "SYSTEM", "USER")  # type: ignore[arg-type]

    span = _named(tuple(exporter.get_finished_spans()), "gate.llm.Classification")
    assert span.attributes["gen_ai.system"] == "anthropic"
    assert span.attributes["gen_ai.request.model"] == "claude-sonnet-5"
    assert span.attributes["gen_ai.usage.input_tokens"] == 1000
    assert span.attributes["gen_ai.usage.output_tokens"] == 200
    # Thinking is a real cost line under an effort setting; folding it into output_tokens would
    # make the two unreconcilable against a bill.
    assert span.attributes["gate.llm.thinking_tokens"] == 150
    assert span.attributes["gate.llm.effort"] == "high"
    assert span.attributes["gate.llm.structured_output_method"] == "json_schema"


async def test_an_llm_call_without_usage_metadata_still_produces_a_span(
    exporter: InMemorySpanExporter,
) -> None:
    await structured_call(_Model(), Classification, "S", "U")  # type: ignore[arg-type]

    span = _named(tuple(exporter.get_finished_spans()), "gate.llm.Classification")
    assert "gen_ai.usage.input_tokens" not in span.attributes


# ---- provider wiring --------------------------------------------------------------------------


def test_the_kill_switch_installs_a_no_op_provider() -> None:
    """The app must run with the Langfuse container down — a reviewer cloning the repo should not
    need Docker to see the gate work."""
    init_tracing(object(), Settings(gate_otel_enabled=False))
    try:
        with review_run_span("p", "t-off") as span:
            assert trace_id_of(span) is None  # nothing to deep-link, so the UI omits the link
    finally:
        shutdown_tracing()
    assert tracing_mod._provider is None  # noqa: SLF001


def test_enabling_tracing_builds_an_exporting_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    instrumented: list[Any] = []
    exporter_args: dict[str, Any] = {}
    monkeypatch.setattr(
        tracing_mod.FastAPIInstrumentor,
        "instrument_app",
        staticmethod(lambda app, tracer_provider: instrumented.append(app)),
    )
    # Stub the exporter: a real one makes shutdown's force-flush spend its full retry budget
    # trying to reach a Langfuse that is not running.
    monkeypatch.setattr(
        tracing_mod,
        "OTLPSpanExporter",
        lambda **kw: exporter_args.update(kw) or InMemorySpanExporter(),
    )
    app = object()

    init_tracing(
        app,
        Settings(
            gate_otel_enabled=True,
            otel_exporter_otlp_endpoint="http://localhost:3000/api/public/otel",
            otel_exporter_otlp_headers="Authorization=Basic abc",
        ),
    )
    assert exporter_args["endpoint"] == "http://localhost:3000/api/public/otel"
    assert exporter_args["headers"] == {"Authorization": "Basic abc"}
    try:
        assert instrumented == [app]
        assert tracing_mod._provider is not None  # noqa: SLF001
    finally:
        shutdown_tracing()
    assert tracing_mod._provider is None  # noqa: SLF001


def test_shutdown_is_safe_when_tracing_was_never_started() -> None:
    shutdown_tracing()
    shutdown_tracing()


def test_otel_headers_follow_the_sdk_comma_separated_convention() -> None:
    assert _parse_headers("a=1, b=2") == {"a": "1", "b": "2"}
    assert _parse_headers("") == {}


def test_no_ambient_span_means_no_request_link() -> None:
    assert current_request_context() is None


# ---- log correlation --------------------------------------------------------------------------


def test_log_lines_carry_the_active_trace_and_span_ids(exporter: InMemorySpanExporter) -> None:
    """A Langfuse trace and a stdout line for the same review have to be joinable — the cheap
    half of observability, and it would be strange to add tracing without it."""
    with tracing_mod.tracer().start_as_current_span("s") as span:
        event = _inject_trace_context(None, "info", {"event": "verdict"})

    assert event["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert event["span_id"] == format(span.get_span_context().span_id, "016x")


def test_log_lines_outside_a_span_carry_no_trace_ids() -> None:
    assert _inject_trace_context(None, "info", {"event": "app_started"}) == {"event": "app_started"}


# ---- shutdown ordering ------------------------------------------------------------------------


async def test_the_lifespan_drains_tracked_runs_before_the_provider_goes_away() -> None:
    """Reversed, a run still in flight writes to a shut-down provider and its tail spans vanish."""
    import asyncio

    started = asyncio.Event()

    async def _long_run() -> None:
        started.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(_long_run())
    await started.wait()

    await _drain({task})

    assert task.cancelled()


async def test_draining_an_empty_task_set_is_a_no_op() -> None:
    await _drain(set())
