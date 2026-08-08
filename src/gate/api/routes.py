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
from gate.graph.state import DecisionAction, initial_state
from gate.intake.loader import list_prs, load_pr
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
    model_config = ConfigDict(extra="forbid")
    pr_id: str


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: DecisionAction
    note: str = ""


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _pending_interrupts(snapshot: Any) -> list[Any]:
    return [intr for task in snapshot.tasks for intr in task.interrupts]


def _derive_status(values: dict[str, Any], snapshot: Any) -> str:
    if values.get("verdict") is not None:
        return "done"
    if _pending_interrupts(snapshot):
        return "awaiting_human"
    return "running"


def _spawn(
    request: Request,
    invoke: Callable[[str | None], Awaitable[Any]],
    *,
    pr_id: str,
    thread_id: str,
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
            with review_run_span(pr_id, thread_id, link_to) as span:
                values = await invoke(trace_id_of(span))
                set_run_outcome_attributes(span, values or {})
        except Exception as exc:  # noqa: BLE001 — background run: log, never crash the app
            log.info("graph_run_error", pr_id=pr_id, error=str(exc))

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
    load_pr(body.pr_id)  # validate up front: FixtureError -> 422, no half-created thread
    thread_id = str(uuid4())
    request.app.state.reviews[thread_id] = body.pr_id
    graph = request.app.state.graph

    async def _invoke(trace_id: str | None) -> Any:
        state = initial_state(body.pr_id, thread_id)
        state.trace_id = trace_id  # seeded before ainvoke, so it is in the first checkpoint
        return await graph.ainvoke(state, _config(thread_id))

    _spawn(request, _invoke, pr_id=body.pr_id, thread_id=thread_id)
    return {"thread_id": thread_id}


@router.get("/api/reviews")
async def list_reviews(request: Request) -> list[dict[str, Any]]:
    graph = request.app.state.graph
    out: list[dict[str, Any]] = []
    for thread_id, pr_id in request.app.state.reviews.items():
        snapshot = await graph.aget_state(_config(thread_id))
        values = snapshot.values or {}
        risk = values.get("risk")
        # Checkpointed sub-values may come back as dicts or as Pydantic instances.
        risk_level = risk.get("level") if isinstance(risk, dict) else getattr(risk, "level", None)
        out.append(
            {
                "thread_id": thread_id,
                "pr_id": pr_id,
                "status": _derive_status(values, snapshot),
                "risk_level": risk_level,
            }
        )
    return out


@router.get("/api/reviews/{thread_id}")
async def get_review(thread_id: str, request: Request) -> dict[str, Any]:
    snapshot = await request.app.state.graph.aget_state(_config(thread_id))
    values = snapshot.values or {}
    if not values:
        # A review is registered before its background run writes the first checkpoint, and the
        # UI polls as soon as POST returns. Report a known thread as running rather than 404 --
        # the page stops polling on a 404, so the race would strand the very first review.
        pr_id = request.app.state.reviews.get(thread_id)
        if pr_id is None:
            raise ReviewNotFoundError(f"no review for thread {thread_id}")
        return {"status": "running", "thread_id": thread_id, "pr_id": pr_id, **_ui_config()}
    return {"status": _derive_status(values, snapshot), **values, **_ui_config()}


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
    pr_id = request.app.state.reviews.get(thread_id, snapshot.values.get("pr_id", ""))

    async def _invoke(_trace_id: str | None) -> Any:
        # The resumed leg gets its own root run span, but `trace_id` in state is left at the
        # first run's value: `Command(resume=...)` carries no state to seed, and the UI's
        # deep-link should keep pointing at the trace where the review started.
        return await graph.ainvoke(
            Command(resume={"action": body.action.value, "note": body.note}),
            _config(thread_id),
        )

    _spawn(request, _invoke, pr_id=pr_id, thread_id=thread_id)
    return {"status": "resumed"}
