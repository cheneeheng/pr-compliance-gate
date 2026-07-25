"""HTTP routes. Handlers contain no business logic — they load fixtures, drive the graph, and
shape responses. The graph run itself executes as a background task so the UI can poll progress.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict

from gate.errors import NoPendingDecisionError, ReviewNotFoundError
from gate.graph.state import DecisionAction, initial_state
from gate.intake.loader import list_prs, load_pr
from gate.logging import get_logger
from gate.paths import STATIC_DIR

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


def _spawn(request: Request, coro: Any, *, pr_id: str) -> None:
    async def _runner() -> None:
        try:
            await coro
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
    _spawn(
        request,
        graph.ainvoke(initial_state(body.pr_id, thread_id), _config(thread_id)),
        pr_id=body.pr_id,
    )
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
        raise ReviewNotFoundError(f"no review for thread {thread_id}")
    return {"status": _derive_status(values, snapshot), **values}


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
    _spawn(
        request,
        graph.ainvoke(
            Command(resume={"action": body.action.value, "note": body.note}),
            _config(thread_id),
        ),
        pr_id=pr_id,
    )
    return {"status": "resumed"}
