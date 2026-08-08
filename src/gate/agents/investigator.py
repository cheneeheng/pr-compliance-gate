"""Autonomous investigator sub-agent (Claude Agent SDK).

LangGraph sees one node (``investigate``) with one entry and one exit; the Agent SDK owns the
inner tool loop. The agent gets three read-only, sandboxed tools over the change's repo snapshot
and hard budgets on turns, tool calls, and per-file bytes. Budget exhaustion or SDK error never
raises into the graph — it returns an inconclusive report, so failure lands on a human at the
gate, never in a stack trace.

SDK-surface note: this targets the ``claude-agent-sdk`` in-process MCP tool API
(``tool`` / ``create_sdk_mcp_server`` / ``ClaudeAgentOptions`` / ``query``). The SDK is young
and its option surface moves — verify names against the installed version. Message/usage
extraction below is duck-typed on purpose to tolerate minor surface drift.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    McpSdkServerConfig,
    SdkMcpTool,
    create_sdk_mcp_server,
    query,
    tool,
)
from opentelemetry import trace
from opentelemetry.context import Context
from pydantic import BaseModel, ConfigDict, Field

from gate.config import Settings
from gate.graph.state import DecisionAction, InvestigationReport, ReviewState
from gate.llm.models import effort_for, estimate_cost, investigator_model, structured_call
from gate.llm.prompts import investigator_system
from gate.logging import get_logger
from gate.paths import repo_snapshot_dir
from gate.telemetry.tracing import run_context, tracer

log = get_logger(__name__)

_SERVER_NAME = "gate"
_ALLOWED_TOOLS = [
    f"mcp__{_SERVER_NAME}__read_file",
    f"mcp__{_SERVER_NAME}__grep_repo",
    f"mcp__{_SERVER_NAME}__list_dir",
]
_GREP_MAX_HITS = 40


@dataclass
class _Tracker:
    """Per-run mutable state shared with the tool closures."""

    max_tool_calls: int
    max_file_chars: int
    tool_calls: int = 0
    files_examined: list[str] = field(default_factory=list)
    # The investigation span's context, captured before the loop starts. Tool closures run inside
    # the SDK's own tasks, which ambient OTel context does not follow, so it is passed explicitly.
    span_context: Context | None = None

    def budget_exhausted(self) -> bool:
        """True once the cap is spent. Checked *before* incrementing, so a refused call is not
        counted and ``tool_calls`` can never exceed ``max_tool_calls`` (ITER_03 §04 budget)."""
        return self.tool_calls >= self.max_tool_calls


def _tool_text(text: str, *, is_error: bool = False) -> dict[str, object]:
    """Shape a tool result for the SDK. Errors are returned, never raised."""
    return {"content": [{"type": "text", "text": text}], "is_error": is_error}


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n[... truncated {len(text) - 2 * half} chars ...]\n{text[-half:]}"


def resolve_in_sandbox(root: Path, path_str: str) -> Path | None:
    """Resolve ``path_str`` under ``root``, or None if it escapes the sandbox.

    Blocks ``..`` traversal and absolute paths pointing outside the snapshot. Pure and
    module-level so the sandbox can be unit-tested without the SDK.
    """
    candidate = (root / path_str).resolve()
    return candidate if candidate.is_relative_to(root) else None


@contextmanager
def tool_span(tracker: _Tracker, name: str, target: str) -> Iterator[None]:
    """One child span per tool call, carrying the tool name and its sandboxed argument.

    Parented explicitly to the investigation span: the SDK runs tool handlers on its own tasks,
    which ambient OTel context does not follow. This is what makes cost gate #2 *demonstrable*
    rather than asserted — the trace shows the harness stopping at its budget.
    """
    with tracer().start_as_current_span(
        f"gate.investigator.tool.{name}",
        context=tracker.span_context,
        attributes={"tool.name": name, "tool.target": target},
    ):
        yield


def _build_tools(root: Path, tracker: _Tracker) -> list[SdkMcpTool[object]]:
    """Build the three sandboxed tool closures over ``root``. Separate from server construction
    so the sandbox and budget behavior can be unit-tested without running the SDK loop."""

    def _resolve(path_str: str) -> Path | None:
        return resolve_in_sandbox(root, path_str)

    @tool("read_file", "Read a UTF-8 text file inside the change's repo snapshot.", {"path": str})
    async def read_file(args: dict[str, object]) -> dict[str, object]:
        with tool_span(tracker, "read_file", str(args.get("path", ""))):
            if tracker.budget_exhausted():
                return _tool_text("tool-call budget exhausted; stop and report.", is_error=True)
            tracker.tool_calls += 1
            path = _resolve(str(args.get("path", "")))
            if path is None or not path.is_file():
                return _tool_text(f"no such file in snapshot: {args.get('path')!r}", is_error=True)
            rel = path.relative_to(root).as_posix()
            if rel not in tracker.files_examined:
                tracker.files_examined.append(rel)
            return _tool_text(
                _truncate(
                    path.read_text(encoding="utf-8", errors="replace"), tracker.max_file_chars
                )
            )

    @tool(
        "grep_repo",
        "Search the repo snapshot for a substring/regex-free pattern.",
        {"pattern": str},
    )
    async def grep_repo(args: dict[str, object]) -> dict[str, object]:
        with tool_span(tracker, "grep_repo", str(args.get("pattern", ""))):
            if tracker.budget_exhausted():
                return _tool_text("tool-call budget exhausted; stop and report.", is_error=True)
            tracker.tool_calls += 1
            pattern = str(args.get("pattern", ""))
            if not pattern:
                return _tool_text("empty pattern", is_error=True)
            hits: list[str] = []
            for file in sorted(root.rglob("*")):
                if not file.is_file():
                    continue
                try:
                    for i, line in enumerate(
                        file.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
                    ):
                        if pattern in line:
                            hits.append(f"{file.relative_to(root).as_posix()}:{i}: {line.strip()}")
                            if len(hits) >= _GREP_MAX_HITS:
                                break
                except OSError:
                    continue
                if len(hits) >= _GREP_MAX_HITS:
                    break
            return _tool_text("\n".join(hits) if hits else "no matches")

    @tool("list_dir", "List entries of a directory inside the repo snapshot.", {"path": str})
    async def list_dir(args: dict[str, object]) -> dict[str, object]:
        with tool_span(tracker, "list_dir", str(args.get("path", "."))):
            if tracker.budget_exhausted():
                return _tool_text("tool-call budget exhausted; stop and report.", is_error=True)
            tracker.tool_calls += 1
            path = _resolve(str(args.get("path", ".")))
            if path is None or not path.is_dir():
                return _tool_text(
                    f"no such directory in snapshot: {args.get('path')!r}", is_error=True
                )
            entries = sorted(f"{p.name}/" if p.is_dir() else p.name for p in path.iterdir())
            return _tool_text("\n".join(entries) if entries else "(empty)")

    return [read_file, grep_repo, list_dir]


def _make_server(root: Path, tracker: _Tracker) -> McpSdkServerConfig:
    """Build the in-process MCP server exposing the three sandboxed tools."""
    return create_sdk_mcp_server(
        name=_SERVER_NAME, version="1.0.0", tools=_build_tools(root, tracker)
    )


def _system_prompt(settings: Settings) -> str:
    return investigator_system(
        settings.gate_investigator_max_turns, settings.gate_investigator_max_tool_calls
    )


def _open_questions(state: ReviewState) -> list[str]:
    questions = [
        f.description for f in state.specialist_findings if f.needs_more_context and f.description
    ]
    # A human 'request_info' note is a first-class investigation lead (ITER_04 §06).
    decision = state.human_decision
    if decision and decision.action == DecisionAction.request_info and decision.note:
        questions.append(decision.note)
    return questions


def _build_prompt(state: ReviewState, questions: list[str]) -> str:
    q_block = "\n".join(f"- {q}" for q in questions) or "- (no explicit questions; summarize risk)"
    leads = ", ".join(state.files_changed) or "(none)"
    return (
        "Open questions to settle:\n"
        f"{q_block}\n\n"
        f"Files changed in this PR (starting leads): {leads}\n\n"
        "Investigate using the sandboxed tools, then give your final report."
    )


def _extract_cost(message: object, settings: Settings) -> float:
    """Read cost from a ResultMessage, tolerating SDK surface drift."""
    direct = getattr(message, "total_cost_usd", None)
    if isinstance(direct, (int, float)):
        return float(direct)
    usage = getattr(message, "usage", None)
    if isinstance(usage, dict):
        return estimate_cost(
            settings.gate_model_investigator,
            int(usage.get("input_tokens", 0)),
            int(usage.get("output_tokens", 0)),
        )
    return 0.0


def _message_text(message: object) -> str:
    """Best-effort extraction of assistant/result text from an SDK message."""
    result = getattr(message, "result", None)
    if isinstance(result, str):
        return result
    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts = [getattr(b, "text", "") for b in content if getattr(b, "text", "")]
        return "\n".join(p for p in parts if p)
    return ""


class _InvestigationAnswer(BaseModel):
    """Model-authored narrative for the final structured-output turn. The harness owns the
    metrics (``tool_calls_count``, ``cost_estimate_usd``, ``files_examined``); the model writes
    only the answer, so those are never trusted from the model."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    findings: list[str] = Field(default_factory=list)


async def _summarize(
    questions: list[str], transcript: str, files: list[str], settings: Settings
) -> _InvestigationAnswer:
    """Final structured-output turn: distil the loop transcript into an InvestigationReport."""
    q_block = "\n".join(f"- {q}" for q in questions) or "- (no explicit questions)"
    user = (
        f"Open questions:\n{q_block}\n\n"
        f"Files you examined: {', '.join(files) or '(none)'}\n\n"
        f"Your investigation notes:\n{transcript or '(no notes produced)'}\n\n"
        "Produce your final report: a concise summary answering the questions, and the concrete "
        "findings that settle them (one string each)."
    )
    return await structured_call(
        investigator_model(settings), _InvestigationAnswer, _system_prompt(settings), user
    )


def _loop_can_spawn_subprocess() -> bool:
    """Whether the running loop can start the CLI the SDK drives.

    Windows' ``SelectorEventLoop`` cannot: it raises a bare ``NotImplementedError`` whose message
    is empty, which the SDK reports as ``Failed to start Claude Code:`` with nothing after it.
    ``uvicorn --reload`` selects exactly that loop (``uvicorn/loops/asyncio.py``, where
    ``use_subprocess`` — set by ``--reload`` — forces ``SelectorEventLoop`` even on Windows), so
    the recommended dev command used to disable the investigator with an unreadable error.
    """
    if sys.platform != "win32":
        return True
    return not isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)


async def run_investigation(state: ReviewState, settings: Settings) -> InvestigationReport:
    """Run the bounded agent loop and return a report. Never raises into the graph."""
    with tracer().start_as_current_span(
        "gate.investigator.run",
        context=run_context(state.thread_id),
        attributes={"gate.pr_id": state.pr_id, "gate.loop_count": state.loop_count},
    ) as span:
        report = await _run_investigation(state, settings, span)
        span.set_attributes(
            {
                "gate.investigator.tool_calls": report.tool_calls_count,
                "gate.cost.investigator_usd": report.cost_estimate_usd,
                "gate.investigator.files_examined": len(report.files_examined),
            }
        )
        return report


async def _run_investigation(
    state: ReviewState, settings: Settings, span: trace.Span
) -> InvestigationReport:
    # The sandbox root is injected by the adapter (a fixture's `repo/` snapshot or a live PR's
    # clone), never derived from `pr_id`. The `resolve()` + `is_relative_to()` check below is
    # unchanged in mechanism — only where the root comes from moved.
    root = repo_snapshot_dir(state.pr_id).resolve()
    tracker = _Tracker(
        max_tool_calls=settings.gate_investigator_max_tool_calls,
        max_file_chars=settings.gate_investigator_max_file_chars,
        span_context=trace.set_span_in_context(span),
    )
    if not root.is_dir():
        return InvestigationReport(
            summary=f"investigation inconclusive: no repo snapshot for {state.pr_id}"
        )
    if not _loop_can_spawn_subprocess():
        return InvestigationReport(
            summary=(
                "investigation inconclusive: this event loop cannot spawn a subprocess, so the "
                "Claude Code CLI the Agent SDK drives cannot start. On Windows, `uvicorn "
                "--reload` selects a SelectorEventLoop, which has no subprocess support. Restart "
                "the server without `--reload`."
            )
        )

    questions = _open_questions(state)
    prompt = _build_prompt(state, questions)
    transcript_parts: list[str] = []
    cost = 0.0
    try:
        options = ClaudeAgentOptions(
            model=settings.gate_model_investigator,
            effort=effort_for(
                settings.gate_model_investigator, settings.gate_model_investigator_effort
            ),
            system_prompt=_system_prompt(settings),
            mcp_servers={_SERVER_NAME: _make_server(root, tracker)},
            allowed_tools=_ALLOWED_TOOLS,
            max_turns=settings.gate_investigator_max_turns,
            # The SDK spawns the Claude Code CLI, which inherits os.environ -- and the key is
            # read from .env by pydantic-settings, which never touches os.environ. Without this
            # the sub-agent is the one component running unauthenticated.
            env=(
                {"ANTHROPIC_API_KEY": settings.anthropic_api_key}
                if settings.anthropic_api_key
                else {}
            ),
        )
        turn = 0
        async for message in query(prompt=prompt, options=options):
            turn += 1
            # One span per agent turn. Opened and closed around the message rather than held
            # open across the await: the SDK yields turn-by-turn, so a span per yielded message
            # is the honest granularity available without reaching into its internals.
            with tracer().start_as_current_span(
                "gate.investigator.turn",
                context=tracker.span_context,
                attributes={"gate.turn": turn, "gate.message_type": type(message).__name__},
            ):
                text = _message_text(message)
                if text:
                    transcript_parts.append(text)
                if type(message).__name__ == "ResultMessage":
                    cost = _extract_cost(message, settings)
    except Exception as exc:  # noqa: BLE001 — failure must land on a human, not the graph
        log.info("investigation_error", pr_id=state.pr_id, error=str(exc))
        return InvestigationReport(
            summary=f"investigation inconclusive: {exc}",
            files_examined=tracker.files_examined,
            tool_calls_count=tracker.tool_calls,
            cost_estimate_usd=cost,
        )

    # Final structured-output turn: the loop gathered evidence; this distils it into the report.
    transcript = "\n".join(transcript_parts)
    try:
        answer = await _summarize(questions, transcript, tracker.files_examined, settings)
    except Exception as exc:  # noqa: BLE001 — the synthesis turn must not raise into the graph
        log.info("investigation_summarize_error", pr_id=state.pr_id, error=str(exc))
        return InvestigationReport(
            summary=transcript or f"investigation inconclusive: {exc}",
            findings=[transcript] if transcript else [],
            files_examined=tracker.files_examined,
            tool_calls_count=tracker.tool_calls,
            cost_estimate_usd=cost,
        )

    return InvestigationReport(
        summary=answer.summary or "investigation inconclusive: no answer produced",
        findings=answer.findings,
        files_examined=tracker.files_examined,
        tool_calls_count=tracker.tool_calls,
        cost_estimate_usd=cost,
    )
