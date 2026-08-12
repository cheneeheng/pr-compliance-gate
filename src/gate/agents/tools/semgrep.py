"""The investigator's semgrep tool: real static analysis inside the existing agent loop.

The ITER_03 toolset was read-only exploration — ``read_file``, ``grep_repo``, ``list_dir`` — and
its findings were only as good as what grep surfaced, which for a *security and licensing* gate is
thin. Semgrep is added as a **tool inside the loop** rather than a new graph node, which
reinforces rather than dilutes the architectural claim that LangGraph sees one node and the Agent
SDK owns the inner loop.

Every failure mode returns ``semgrep unavailable: <reason>`` as a tool *result*. Nothing here
raises into the graph, consistent with the rest of the harness.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from gate.config import Settings
from gate.graph.state import Severity, StaticFinding
from gate.logging import get_logger
from gate.paths import RULES_DIR
from gate.runtime import UNSUPPORTED_LOOP_REASON, loop_supports_subprocess

log = get_logger(__name__)

# semgrep's severity vocabulary mapped onto the gate's. Anything unrecognized reads as `info`
# rather than being dropped — a hit the reviewer cannot see is worse than a mis-ranked one.
_SEVERITY: dict[str, Severity] = {
    "ERROR": Severity.block,
    "WARNING": Severity.warn,
    "INFO": Severity.info,
}

# semgrep exits 1 when it finds something and 0 when it does not. Both are successful runs.
_OK_RETURN_CODES = (0, 1)


def rules_dir(settings: Settings) -> Path:
    return settings.gate_semgrep_rules_dir or RULES_DIR


def _shape(results: list[dict[str, Any]], root: Path) -> list[StaticFinding]:
    """Keep rule id, path, line, message and severity; drop the rest.

    semgrep JSON is large and mostly metadata — feeding it raw into the agent's context would
    spend the turn budget on noise.
    """
    findings: list[StaticFinding] = []
    for item in results:
        extra = item.get("extra") or {}
        start = item.get("start") or {}
        raw_path = Path(str(item.get("path", "")))
        try:
            display = raw_path.resolve().relative_to(root).as_posix()
        except ValueError:
            display = raw_path.as_posix()
        findings.append(
            StaticFinding(
                rule_id=str(item.get("check_id", "")),
                severity=_SEVERITY.get(str(extra.get("severity", "")).upper(), Severity.info),
                path=display,
                line=int(start.get("line", 0)),
                message=str(extra.get("message", "")).strip(),
            )
        )
    return findings


async def run_semgrep(
    target: Path, root: Path, settings: Settings
) -> tuple[list[StaticFinding], str | None]:
    """Scan ``target`` with the pinned local ruleset. Returns (findings, failure reason)."""
    config = rules_dir(settings).resolve()
    if not loop_supports_subprocess():
        # Third `create_subprocess_exec` call site after the Agent SDK and the workspace clone,
        # and it fails identically: bare NotImplementedError, empty message.
        return [], f"unsupported event loop — {UNSUPPORTED_LOOP_REASON}"
    if not config.is_dir():
        return [], f"no ruleset at {config}"

    # Fixed argv, never `shell=True`: every element comes from config or a sandbox-checked path,
    # never from model output. The config path is the absolute resolved directory, never the
    # literal `rules/` — a relative config path re-introduces the CWD assumption the asset
    # resolver exists to kill.
    argv = [
        "semgrep",
        "--config",
        str(config),
        "--json",
        "--quiet",
        "--metrics=off",
        "--disable-version-check",
        "--timeout",
        str(int(settings.gate_semgrep_timeout_s)),
        str(target),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except (FileNotFoundError, NotImplementedError) as exc:
        return [], f"semgrep binary not available ({exc.__class__.__name__})"

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=settings.gate_semgrep_timeout_s + 5
        )
    except TimeoutError:
        process.kill()
        return [], f"timed out after {settings.gate_semgrep_timeout_s:.0f}s"

    if process.returncode not in _OK_RETURN_CODES:
        detail = stderr.decode(errors="replace").strip().splitlines()
        return [], f"exit {process.returncode}: {detail[-1] if detail else 'no output'}"

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return [], "unparseable JSON output"
    return _shape(list(payload.get("results", [])), root), None


def render(findings: list[StaticFinding], max_results: int) -> str:
    """The text the agent sees. Bounded — the caps are the context strategy, not a summarizer."""
    if not findings:
        return "semgrep found no matches."
    shown = findings[:max_results]
    lines = [f"{f.severity.value} {f.rule_id} {f.path}:{f.line} — {f.message}" for f in shown]
    if len(findings) > len(shown):
        lines.append(f"[... {len(findings) - len(shown)} further results omitted ...]")
    return "\n".join(lines)
