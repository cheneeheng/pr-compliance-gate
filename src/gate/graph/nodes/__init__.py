"""Node package. Holds the tracing decorator every node is wrapped with.

``traced_node`` is applied at wiring time in ``graph/build.py`` rather than as a per-node
decorator: the wiring is the enforcement point, so no node can be silently untraced by someone
forgetting an ``@``.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Coroutine
from typing import Any, cast

from langgraph.errors import GraphInterrupt
from opentelemetry.trace import Status, StatusCode

from gate.graph.state import ReviewState
from gate.telemetry.tracing import run_context, tracer

# Coroutine, not Awaitable: LangGraph's `add_node` overloads accept the narrower form, and the
# wrapper must stay assignable to whatever the undecorated node was.
NodeFn = Callable[[ReviewState], Coroutine[Any, Any, dict[str, object]]]


def traced_node[F: NodeFn](fn: F) -> F:
    """Wrap a node in a ``gate.node.<name>`` span parented to the run span.

    The parent context is passed explicitly rather than taken from ambient ``contextvars``: the
    two specialists run as parallel LangGraph branches on separate tasks, which ambient context
    does not follow (see ``telemetry.tracing``).
    """

    @functools.wraps(fn)
    async def wrapper(state: ReviewState) -> dict[str, object]:
        with tracer().start_as_current_span(
            f"gate.node.{fn.__name__}",
            context=run_context(state.thread_id),
            attributes={
                "gate.pr_id": state.pr_id,
                "gate.thread_id": state.thread_id,
                "gate.loop_count": state.loop_count,
            },
            # Errors are recorded below instead: the human gate pausing raises GraphInterrupt,
            # which is control flow, and a span marked ERROR on every HITL review would make the
            # status attribute useless for finding real failures.
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                update = await fn(state)
            except GraphInterrupt:
                span.set_attribute("gate.node.interrupted", True)
                raise
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            route = update.get("route")
            if route is not None:
                span.set_attribute("gate.node.route", str(route))
            return update

    # The wrapper is behaviourally the node it wraps; returning it as `F` keeps each node's exact
    # declared type, which is what LangGraph's `add_node` overloads resolve against.
    return cast("F", wrapper)
