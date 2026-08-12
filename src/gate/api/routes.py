"""HTTP routes. Handlers contain no business logic — they load fixtures, drive the graph, and
shape responses. The graph run itself executes as a background task so the UI can poll progress.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict

from gate.config import get_settings
from gate.errors import NoPendingDecisionError, ReviewNotFoundError
from gate.graph.state import DecisionAction, PRRef, initial_state
from gate.intake.loader import list_prs
from gate.intake.sources import select_source
from gate.logging import get_logger
from gate.paths import STATIC_DIR
from gate.telemetry.tracing import (
    current_request_context,
    review_run_span,
    set_run_outcome_attributes,
    trace_id_of,
)

log = get_logger(__name__)
router = APIRouter()


class StartReview(BaseModel):
    """Exactly one of the two. `select_source` enforces that and raises the 422."""

    model_config = ConfigDict(extra="forbid")
    pr_id: str | None = None
    pr_url: str | None = None


class Registration(BaseModel):
    """What the session-scoped registry remembers about a thread.

    Recorded at registration time — before the first checkpoint exists — so `GET /api/reviews`
    lists a live review with its badge during the clone window. The v1 first-review race fix (a
    known thread reads as `running` until its first checkpoint) covers that longer window
    unchanged.
    """

    pr_id: str
    source: str
    pr_ref: PRRef | None = None


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: DecisionAction
    note: str = ""


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _pending_interrupts(snapshot: Any) -> list[Any]:
    return [intr for task in snapshot.tasks for intr in task.interrupts]


def _derive_status(values: dict[str, Any], snapshot: Any, error: str | None) -> str:
    """`error` outranks a pending interrupt but not a verdict: a run that crashed after writing
    its verdict is done, and one that crashed while parked at the gate can no longer be resumed."""
    if values.get("verdict") is not None:
        return "done"
    if error is not None:
        return "failed"
    if _pending_interrupts(snapshot):
        return "awaiting_human"
    return "running"


def _spawn(
    request: Request,
    invoke: Callable[[str | None], Awaitable[Any]],
    *,
    pr_id: str,
    thread_id: str,
    source: str,
) -> None:
    """Run a graph invocation as a tracked background task under its own root run span.

    The run span is opened *here*, inside the task, not in the handler: the handler returns as
    soon as this is spawned, so parenting the run under the request span would attach a
    multi-minute run to a span that ended in milliseconds. The request span's context is captured
    while it is still current and carried in as a span *link* instead.
    """
    link_to = current_request_context()

    async def _runner() -> None:
        try:
            with review_run_span(pr_id, thread_id, link_to, source) as span:
                values = await invoke(trace_id_of(span))
                set_run_outcome_attributes(span, values or {})
        except Exception as exc:  # noqa: BLE001 — background run: log, never crash the app
            log.info("graph_run_error", pr_id=pr_id, error=str(exc))
            # The run is over and wrote no verdict, so nothing in the checkpoint says why it
            # stopped. Recorded here or the review polls as `running` forever and the failure —
            # a bad API key being the common one — lives only in the server log.
            request.app.state.review_errors[thread_id] = str(exc)

    task = asyncio.create_task(_runner())
    request.app.state.bg_tasks.add(task)
    task.add_done_callback(request.app.state.bg_tasks.discard)


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/prs")
async def get_prs() -> list[dict[str, Any]]:
    return [s.model_dump() for s in list_prs()]


@router.post("/api/reviews")
async def start_review(body: StartReview, request: Request) -> dict[str, str]:
    """Preflight synchronously, then hand the slow half to the background task.

    The preflight (`resolve`) is bounded — a fixture directory check, or one MCP metadata round
    trip — and it is what makes a bad URL, a missing repo or a token without scope an HTTP error
    *before* a thread exists. Everything retryable (pagination, diff fetch, clone, the graph run)
    happens after, where failures are escalations because a thread already exists to carry them.
    """
    source, ref_input = select_source(body.pr_id, body.pr_url)
    ref = await source.resolve(ref_input)
    # The display and lookup key: a fixture directory name, or `{owner}/{repo}#{number}` for a
    # live PR so existing list/detail rendering keeps working with no null handling. Never a path
    # component — the `head_sha` regex is what guards the live path.
    pr_id = f"{ref.owner}/{ref.repo}#{ref.number}" if ref else str(body.pr_id)
    thread_id = str(uuid4())
    request.app.state.reviews[thread_id] = Registration(
        pr_id=pr_id, source=source.source_name, pr_ref=ref
    )
    graph = request.app.state.graph

    async def _invoke(trace_id: str | None) -> Any:
        payload = await source.load(ref_input, ref)
        state = initial_state(payload.pr_id, thread_id)
        state.trace_id = trace_id  # seeded before ainvoke, so it is in the first checkpoint
        state.source = source.source_name
        state.pr_ref = payload.ref
        state.workspace_path = payload.workspace_path
        state.diff = payload.diff
        state.files_changed = payload.files_changed
        state.escalation = payload.escalation
        return await graph.ainvoke(state, _config(thread_id))

    _spawn(request, _invoke, pr_id=pr_id, thread_id=thread_id, source=source.source_name)
    return {"thread_id": thread_id}


@router.get("/api/reviews")
async def list_reviews(request: Request) -> list[dict[str, Any]]:
    graph = request.app.state.graph
    out: list[dict[str, Any]] = []
    for thread_id, registration in request.app.state.reviews.items():
        snapshot = await graph.aget_state(_config(thread_id))
        values = snapshot.values or {}
        risk = values.get("risk")
        # Checkpointed sub-values may come back as dicts or as Pydantic instances.
        risk_level = risk.get("level") if isinstance(risk, dict) else getattr(risk, "level", None)
        out.append(
            {
                "thread_id": thread_id,
                "pr_id": registration.pr_id,
                "source": registration.source,
                "status": _derive_status(
                    values, snapshot, request.app.state.review_errors.get(thread_id)
                ),
                "risk_level": risk_level,
            }
        )
    return out


@router.get("/api/reviews/{thread_id}")
async def get_review(thread_id: str, request: Request) -> dict[str, Any]:
    snapshot = await request.app.state.graph.aget_state(_config(thread_id))
    values = snapshot.values or {}
    error = request.app.state.review_errors.get(thread_id)
    if not values:
        # A review is registered before its background run writes the first checkpoint, and the
        # UI polls as soon as POST returns. Report a known thread as running rather than 404 --
        # the page stops polling on a 404, so the race would strand the very first review.
        registration = request.app.state.reviews.get(thread_id)
        if registration is None:
            raise ReviewNotFoundError(f"no review for thread {thread_id}")
        # `_derive_status` rather than a literal "running": a run that died before its first
        # checkpoint — a bad token, a failed clone — has no values to report but is not running.
        return {
            "status": _derive_status(values, snapshot, error),
            "thread_id": thread_id,
            "pr_id": registration.pr_id,
            "source": registration.source,
            "pr_ref": registration.pr_ref.model_dump() if registration.pr_ref else None,
            "error": error,
            **_ui_config(),
        }
    return {
        "status": _derive_status(values, snapshot, error),
        **values,
        "error": error,
        **_ui_config(),
    }


def _ui_config() -> dict[str, Any]:
    """Server config the detail view needs but that is not review state.

    `trace_id` rides along inside the snapshot; the Langfuse base URL does not, so this response
    is `{...ReviewState, langfuse_ui_base}` rather than a bare snapshot. It is the first place
    the detail response stops being exactly `ReviewState`, which is worth stating rather than
    leaving a reader to discover it in the JS. Empty when tracing is off, so the UI omits the
    link entirely rather than rendering a permanently dead button.
    """
    settings = get_settings()
    base = settings.gate_langfuse_ui_base if settings.gate_otel_enabled else ""
    return {"langfuse_ui_base": base}


@router.get("/api/reviews/{thread_id}/pending")
async def get_pending(thread_id: str, request: Request) -> Any:
    snapshot = await request.app.state.graph.aget_state(_config(thread_id))
    if not (snapshot.values or {}):
        raise ReviewNotFoundError(f"no review for thread {thread_id}")
    interrupts = _pending_interrupts(snapshot)
    if not interrupts:
        return Response(status_code=204)
    return interrupts[0].value


@router.post("/api/reviews/{thread_id}/decision")
async def submit_decision(thread_id: str, body: DecisionBody, request: Request) -> dict[str, str]:
    graph = request.app.state.graph
    snapshot = await graph.aget_state(_config(thread_id))
    if not (snapshot.values or {}):
        raise ReviewNotFoundError(f"no review for thread {thread_id}")
    if not _pending_interrupts(snapshot):
        raise NoPendingDecisionError("no pending decision")  # 409; double-submits land here too
    # Spelled as an if/else rather than two ternaries on purpose: `coverage.py` records no arc for
    # a conditional expression, so both halves of a ternary count as covered the moment the line
    # runs once. As branches, an untested half fails `fail_under = 100` loudly.
    registration = request.app.state.reviews.get(thread_id)
    if registration is not None:
        pr_id, source = registration.pr_id, registration.source
    else:
        # The registry is session-scoped, so a thread checkpointed by an earlier process is not
        # in it. The checkpoint is what remembers where that review came from.
        pr_id = snapshot.values.get("pr_id", "")
        source = snapshot.values.get("source", "fixture")

    async def _invoke(_trace_id: str | None) -> Any:
        # The resumed leg gets its own root run span, but `trace_id` in state is left at the
        # first run's value: `Command(resume=...)` carries no state to seed, and the UI's
        # deep-link should keep pointing at the trace where the review started.
        return await graph.ainvoke(
            Command(resume={"action": body.action.value, "note": body.note}),
            _config(thread_id),
        )

    _spawn(request, _invoke, pr_id=pr_id, thread_id=thread_id, source=source)
    return {"status": "resumed"}
