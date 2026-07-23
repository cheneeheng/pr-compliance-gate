"""FastAPI application: lifespan, middleware, exception handlers, static serving.

The UI is served by this same app (same origin) — no CORS middleware is needed, and none is
added. No auth: single-user local demo (deferred, see the plan's out-of-scope list).
"""

from __future__ import annotations

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
    NoPendingDecisionError,
    ReviewNotFoundError,
)
from gate.graph.build import compile_graph
from gate.logging import configure_logging, get_logger, set_correlation_id

log = get_logger(__name__)

_STATUS_FOR_ERROR: dict[type[GateError], int] = {
    FixtureError: 422,
    ReviewNotFoundError: 404,
    NoPendingDecisionError: 409,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    # Session-scoped registry of created reviews (thread_id -> pr_id). Checkpoints persist on
    # disk; this ordered map just backs the list view (see DECISION_LOG entry 5).
    app.state.reviews = OrderedDict()
    app.state.bg_tasks = set()
    async with AsyncSqliteSaver.from_conn_string(str(settings.gate_db_path)) as checkpointer:
        app.state.graph = compile_graph(checkpointer)
        log.info("app_started", db=str(settings.gate_db_path))
        yield
    log.info("app_stopped")


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
