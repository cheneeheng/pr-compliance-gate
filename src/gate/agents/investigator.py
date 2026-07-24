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
from pydantic import BaseModel, ConfigDict, Field

from gate.config import Settings
from gate.graph.state import DecisionAction, InvestigationReport, ReviewState
from gate.llm.models import estimate_cost, investigator_model, structured_call
from gate.llm.prompts import investigator_system
from gate.logging import get_logger
from gate.paths import repo_snapshot_dir

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


def _build_tools(root: Path, tracker: _Tracker) -> list[SdkMcpTool[object]]:
    """Build the three sandboxed tool closures over ``root``. Separate from server construction
    so the sandbox and budget behavior can be unit-tested without running the SDK loop."""

    def _resolve(path_str: str) -> Path | None:
        return resolve_in_sandbox(root, path_str)

    @tool("read_file", "Read a UTF-8 text file inside the change's repo snapshot.", {"path": str})
    async def read_file(args: dict[str, object]) -> dict[str, object]:
        if tracker.budget_exhausted():
            return _tool_text("tool-call budget exhausted; stop and report.", is_error=True)
        tracker.tool_calls += 1
        path = _resolve(str(args.get("path", "")))
        if path is None or not path.is_file():
            return _tool_text(f"no such file in snapshot: {args.get('path')!r}", is_error=True)
        rel = path.relative_to(root).as_posix()
        if rel not in tracker.files_examined:
            tracker.files_examined.append(rel)
        return _tool_text(_truncate(path.read_text(encoding="utf-8", errors="replace"),
                                    tracker.max_file_chars))

    @tool("grep_repo", "Search the repo snapshot for a substring/regex-free pattern.",
          {"pattern": str})
    async def grep_repo(args: dict[str, object]) -> dict[str, object]:
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
        if tracker.budget_exhausted():
            return _tool_text("tool-call budget exhausted; stop and report.", is_error=True)
        tracker.tool_calls += 1
        path = _resolve(str(args.get("path", ".")))
        if path is None or not path.is_dir():
            return _tool_text(f"no such directory in snapshot: {args.get('path')!r}",
                              is_error=True)
        entries = sorted(
            f"{p.name}/" if p.is_dir() else p.name for p in path.iterdir()
        )
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


async def run_investigation(state: ReviewState, settings: Settings) -> InvestigationReport:
    """Run the bounded agent loop and return a report. Never raises into the graph."""
    root = repo_snapshot_dir(state.pr_id).resolve()
    tracker = _Tracker(
        max_tool_calls=settings.gate_investigator_max_tool_calls,
        max_file_chars=settings.gate_investigator_max_file_chars,
    )
    if not root.is_dir():
        return InvestigationReport(
            summary=f"investigation inconclusive: no repo snapshot for {state.pr_id}"
        )

    questions = _open_questions(state)
    prompt = _build_prompt(state, questions)
    transcript_parts: list[str] = []
    cost = 0.0
    try:
        options = ClaudeAgentOptions(
            model=settings.gate_model_investigator,
            system_prompt=_system_prompt(settings),
            mcp_servers={_SERVER_NAME: _make_server(root, tracker)},
            allowed_tools=_ALLOWED_TOOLS,
            max_turns=settings.gate_investigator_max_turns,
        )
        async for message in query(prompt=prompt, options=options):
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
