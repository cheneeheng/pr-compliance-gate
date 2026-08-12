"""Runnable showcase: drive the demo reviews end to end and narrate each stage.

Unlike the ``llm``-marked eval (``tests/system/eval_demo.py``, which only asserts terminal
outcomes), this prints the pipeline story — classification, the risk cost gate, specialist
findings, the autonomous investigation with its semgrep hits, tool-call count and cost, the
human-in-the-loop decisions, and the final verdict — so you can see what the gate does without
clicking through the UI.

It drives the compiled LangGraph directly (in-memory checkpointer, scripted human decisions), so
no server is needed. The PR itself is loaded through the same ``PRSource`` adapter the API uses:
``load`` runs *before* ``ainvoke`` and seeds the diff, file list and workspace path into the
initial state, because the ``intake`` node fetches nothing of its own.

Prerequisites (real Anthropic API calls):
    cp .env.example .env            # then set ANTHROPIC_API_KEY
    uv sync --extra semgrep         # optional: the investigator's static-analysis tool
    docker compose -f docker-compose.langfuse.yml up -d   # optional: trace backend

With tracing enabled (the default) each beat prints a Langfuse deep-link. Set
``GATE_OTEL_ENABLED=false`` to run with no backend; the link is then omitted rather than dead.
Beat 4 reviews a live public pull request and needs ``GATE_GITHUB_TOKEN``; without one it is
skipped and the first three fixture beats still run offline.

Run:
    uv run python examples/demo.py                      # every beat
    uv run python examples/demo.py 003_auth_change      # a single beat
    uv run python examples/demo.py live --pr-url URL    # beat 4 against your own PR
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from gate.config import get_settings
from gate.graph.build import compile_graph
from gate.graph.checkpoint import use_state_allowlist
from gate.graph.state import initial_state
from gate.intake.sources import select_source
from gate.logging import configure_logging
from gate.telemetry.tracing import (
    init_tracing,
    review_run_span,
    set_run_outcome_attributes,
    shutdown_tracing,
    trace_id_of,
)


@dataclass(frozen=True)
class Beat:
    """One demo beat: what to review, what it demonstrates, and the human's scripted replies."""

    name: str  # what you pass on the command line
    ref: str  # a fixture id, or a pull-request URL when `live`
    demonstrates: str
    decisions: list[tuple[str, str]] = field(default_factory=list)
    live: bool = False


BEATS: list[Beat] = [
    Beat(
        "001_docs_typo",
        "001_docs_typo",
        "cost gate #1 — a docs typo scores below the risk threshold and auto-approves with "
        "zero Sonnet spend",
    ),
    Beat(
        "002_new_dependency",
        "002_new_dependency",
        "policy block + human reject — a GPL-3.0 dependency trips the license specialist; "
        "reconcile routes to the human, who rejects",
        [("reject", "")],
    ),
    Beat(
        "003_auth_change",
        "003_auth_change",
        "autonomous investigation + human-in-the-loop loop — the security specialist needs "
        "context, the Agent SDK sub-agent investigates under budget with semgrep (cost gate #2), "
        "the reviewer requests more info, a re-investigation runs, and the reviewer then approves",
        [("request_info", "confirm the signature check is enforced"), ("approve", "")],
    ),
    Beat(
        "live",
        "https://github.com/psf/requests/pull/6710",
        "live GitHub intake — the URL is resolved through the MCP server, the changed-file list "
        "is paged, the repo is shallow-cloned at head_sha into the investigator's sandbox, and "
        "the same pipeline runs on real input",
        [("approve", "")],
        live=True,
    ),
]


def _field(obj: Any, name: str) -> Any:
    """Read a field from a checkpointed sub-value that may be a dict or a Pydantic instance."""
    if obj is None:
        return None
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _print_gate(payload: dict[str, Any], action: str, note: str) -> None:
    print("  human gate : review paused for a decision")
    esc = payload.get("escalation")
    if esc:
        print(f"      escalation: {esc.get('reason')} — {esc.get('detail')}")
    for f in payload.get("findings") or []:
        if f.get("severity") == "block":
            print(
                f"      block: {f.get('specialist')} cites {f.get('policy_id')} "
                f"— {f.get('description')}"
            )
    quoted = f' ("{note}")' if note else ""
    print(f"      -> reviewer decides: {action}{quoted}")


def _narrate(beat: Beat, title: str, v: dict[str, Any]) -> None:
    settings = get_settings()
    print("=" * 96)
    print(f"PR {v.get('pr_id')} — {title}  [source: {v.get('source')}]")
    print(f"Demonstrates: {beat.demonstrates}")
    ref = v.get("pr_ref")
    if ref:
        print(f"Pull request: {_field(ref, 'html_url')} @ {_field(ref, 'head_sha')[:12]}")
    trace_id = v.get("trace_id")
    if trace_id and settings.gate_otel_enabled:
        base = settings.gate_langfuse_ui_base.rstrip("/")
        print(f"Trace       : {base}/traces/{trace_id}")
    print()

    c = v.get("classification")
    print(f"  classify   : {_field(c, 'change_type')} — {_field(c, 'summary')}")

    r = v.get("risk")
    if r:
        print(
            f"  risk       : score {_field(r, 'score')}/100 "
            f"(level {_field(r, 'level')}), threshold {settings.gate_risk_threshold}"
        )

    findings = v.get("specialist_findings") or []
    inv = v.get("investigation")
    if not findings and not inv:
        print("  cost gate 1: risk below threshold — no Sonnet specialist invoked")
    for f in findings:
        ctx = " [needs more context]" if _field(f, "needs_more_context") else ""
        print(
            f"  finding    : [{_field(f, 'severity')}] {_field(f, 'specialist')} "
            f"cites {_field(f, 'policy_id')} — {_field(f, 'description')}{ctx}"
        )

    if inv:
        cost = _field(inv, "cost_estimate_usd") or 0.0
        files = _field(inv, "files_examined") or []
        runs = _field(inv, "semgrep_runs") or 0
        print(f"  investigate: {_field(inv, 'summary')}")
        print(
            f"               files={files} tool_calls={_field(inv, 'tool_calls_count')} "
            f"semgrep_runs={runs} est_cost=${cost:.4f}  (cost gate 2)"
        )
        for item in _field(inv, "findings") or []:
            print(f"               - {item}")
        # `semgrep_runs`, not the list, is what separates "ran and found nothing" from "never
        # ran" — the same distinction the UI's static-analysis panel keys off.
        hits = _field(inv, "static_analysis") or []
        if runs and not hits:
            print("  semgrep    : ran, no static findings")
        for hit in hits:
            print(
                f"  semgrep    : [{_field(hit, 'severity')}] {_field(hit, 'rule_id')} "
                f"{_field(hit, 'path')}:{_field(hit, 'line')} — {_field(hit, 'message')}"
            )

    esc = v.get("escalation")
    if esc:
        print(f"  escalation : {_field(esc, 'reason')} — {_field(esc, 'detail')}")

    verdict = v.get("verdict")
    if verdict:
        print()
        print(f"  VERDICT    : {_field(verdict, 'outcome')} — {_field(verdict, 'rationale')}")


async def _resume_until_done(
    graph: Any, config: dict[str, Any], decisions: list[tuple[str, str]]
) -> dict[str, Any]:
    """Feed the scripted human decisions at each interrupt until a verdict lands."""
    idx = 0
    while True:
        snapshot = await graph.aget_state(config)
        values = snapshot.values or {}
        if values.get("verdict") is not None:
            return values
        pending = [intr for task in snapshot.tasks for intr in task.interrupts]
        if pending and idx < len(decisions):
            action, note = decisions[idx]
            idx += 1
            _print_gate(pending[0].value, action, note)
            await graph.ainvoke(Command(resume={"action": action, "note": note}), config)
            continue
        return values  # no verdict and no actionable interrupt: stop and narrate what we have


async def _drive(graph: Any, beat: Beat) -> tuple[str, dict[str, Any]]:
    """Run one beat to completion under its own run span. Returns (title, final state values).

    The adapter split is the same one the API uses: ``resolve`` is the cheap preflight (a fixture
    directory check, or one MCP metadata round trip) and ``load`` is the slow half that fetches
    the diff and materializes the workspace. ``load`` runs here, before ``ainvoke``, because the
    ``intake`` node fetches nothing — it only records what arrived. Kept inline rather than shared
    with ``api/routes.py`` so this file reads end to end on its own.
    """
    source, ref_input = (
        select_source(None, beat.ref) if beat.live else select_source(beat.ref, None)
    )
    ref = await source.resolve(ref_input)
    pr_id = f"{ref.owner}/{ref.repo}#{ref.number}" if ref else beat.ref
    thread_id = uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}

    # One root span for the whole beat, including the resumed legs. The API opens a fresh run
    # span per resume because each is its own background task; here the beat is one script, so a
    # single trace tells the whole story.
    with review_run_span(pr_id, thread_id, source=source.source_name) as span:
        payload = await source.load(ref_input, ref)
        state = initial_state(payload.pr_id, thread_id)
        state.trace_id = trace_id_of(span)
        state.source = source.source_name
        state.pr_ref = payload.ref
        state.workspace_path = payload.workspace_path
        state.diff = payload.diff
        state.files_changed = payload.files_changed
        state.escalation = payload.escalation
        await graph.ainvoke(state, config)
        values = await _resume_until_done(graph, config, beat.decisions)
        set_run_outcome_attributes(span, values)
    return payload.title, values


def _select(only: str | None, pr_url: str | None) -> list[Beat] | None:
    beats = [b for b in BEATS if only is None or b.name == only]
    if not beats:
        print(
            f"unknown beat {only!r}; choose one of: {', '.join(b.name for b in BEATS)}",
            file=sys.stderr,
        )
        return None
    if pr_url:
        beats = [
            Beat(b.name, pr_url, b.demonstrates, b.decisions, b.live) if b.live else b
            for b in beats
        ]
    return beats


async def _run(only: str | None, pr_url: str | None) -> int:
    configure_logging(logging.WARNING)  # silence per-node logs so the narration stays readable
    settings = get_settings()
    if not settings.anthropic_api_key:
        print(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and set the key.",
            file=sys.stderr,
        )
        return 1

    beats = _select(only, pr_url)
    if beats is None:
        return 1

    # The instrumentor wants an ASGI app to hang request spans off; this script has no HTTP
    # surface, so a throwaway instance stands in and only the graph's own spans are recorded.
    init_tracing(FastAPI(), settings)
    try:
        async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
            graph = compile_graph(use_state_allowlist(saver))
            for beat in beats:
                if beat.live and not settings.gate_github_token:
                    print("=" * 96)
                    print(f"SKIPPED {beat.name}: GATE_GITHUB_TOKEN is not set.\n")
                    continue
                title, values = await _drive(graph, beat)
                _narrate(beat, title, values)
                print()
    finally:
        # Same ordering as the API lifespan: the runs are finished, so flushing last is what gets
        # the tail of every trace to Langfuse instead of dropping it in the batch buffer.
        shutdown_tracing()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PR Compliance Gate demo reviews.")
    parser.add_argument(
        "beat",
        nargs="?",
        help=f"run a single beat ({', '.join(b.name for b in BEATS)}); default runs all",
    )
    parser.add_argument(
        "--pr-url",
        help="pull-request URL for the live beat (default: a known public PR)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.beat, args.pr_url)))


if __name__ == "__main__":
    main()
