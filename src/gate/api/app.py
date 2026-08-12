"""FastAPI application: lifespan, middleware, exception handlers, static serving.

The UI is served by this same app (same origin) — no CORS middleware is needed, and none is
added. No auth: single-user local demo (deferred, see the plan's out-of-scope list).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from gate.api.routes import router
from gate.config import get_settings
from gate.errors import (
    FixtureError,
    GateError,
    InvalidPRRefError,
    NoPendingDecisionError,
    ReviewNotFoundError,
    SourceForbiddenError,
    SourceNotFoundError,
)
from gate.graph.build import compile_graph
from gate.graph.checkpoint import check_state_version, use_state_allowlist
from gate.intake.retrieval import validate_policy_index
from gate.intake.workspace import evict_lru
from gate.logging import configure_logging, get_logger, set_correlation_id
from gate.telemetry.tracing import init_tracing, shutdown_tracing

log = get_logger(__name__)

_STATUS_FOR_ERROR: dict[type[GateError], int] = {
    FixtureError: 422,
    InvalidPRRefError: 422,
    SourceForbiddenError: 403,
    ReviewNotFoundError: 404,
    SourceNotFoundError: 404,
    NoPendingDecisionError: 409,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    # Fail the process here rather than a review mid-run: a missing policy file or an unmapped
    # change_type is a config error, and the classifier's closed enum means it cannot be a
    # runtime condition worth surviving.
    validate_policy_index(settings)
    check_state_version(settings.gate_db_path, settings.gate_state_version)
    evict_lru(settings)  # a clone cache left oversized by a crashed run is trimmed on start
    # Session-scoped registry of created reviews (thread_id -> pr_id). Checkpoints persist on
    # disk; this ordered map just backs the list view (see DECISION_LOG entry 5).
    app.state.reviews = OrderedDict()
    # thread_id -> the message of the exception that killed its background run. Session-scoped for
    # the same reason `reviews` is: a crashed run writes no checkpoint, so the failure exists
    # nowhere else. Without it the UI polls a dead review as `running` forever.
    app.state.review_errors = {}
    app.state.bg_tasks = set()
    init_tracing(app, settings)
    try:
        async with AsyncSqliteSaver.from_conn_string(str(settings.gate_db_path)) as saver:
            app.state.graph = compile_graph(use_state_allowlist(saver))
            log.info("app_started", db=str(settings.gate_db_path))
            yield
    finally:
        # Ordering is load-bearing: drain the tracked runs *first*. Reversed, a run still in
        # flight writes to a shut-down provider and its tail spans vanish — the same symptom as
        # an unflushed BatchSpanProcessor, from a different cause.
        await _drain(app.state.bg_tasks)
        shutdown_tracing()
    log.info("app_stopped")


async def _drain(tasks: set[asyncio.Task[None]]) -> None:
    """Cancel and await every tracked graph run before the tracer provider goes away."""
    for task in list(tasks):
        task.cancel()
    if tasks:
        await asyncio.gather(*list(tasks), return_exceptions=True)


def create_app() -> FastAPI:
    app = FastAPI(title="PR Compliance Gate", lifespan=lifespan)

    @app.middleware("http")
    async def correlation_id_mw(request: Request, call_next):  # type: ignore[no-untyped-def]
        cid = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex
        set_correlation_id(cid)
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response

    @app.exception_handler(GateError)
    async def gate_error_handler(_request: Request, exc: GateError) -> JSONResponse:
        status = next(
            (code for cls, code in _STATUS_FOR_ERROR.items() if isinstance(exc, cls)), 400
        )
        log.info("gate_error", status=status, error=str(exc))
        return JSONResponse(status_code=status, content={"error": str(exc)})

    app.include_router(router)
    return app


app = create_app()
