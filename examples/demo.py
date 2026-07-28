"""Runnable showcase: drive the three demo reviews end to end and narrate each stage.

Unlike the ``llm``-marked eval (``tests/system/eval_demo.py``, which only asserts terminal
outcomes), this prints the pipeline story — classification, the risk cost gate, specialist
findings, the autonomous investigation with its tool-call count and cost, the human-in-the-loop
decisions, and the final verdict — so you can see what the gate does without clicking through
the UI.

It drives the compiled LangGraph directly (in-memory checkpointer, scripted human decisions), so
no server is needed.

Prerequisites (real Anthropic API calls):
    cp .env.example .env            # then set ANTHROPIC_API_KEY
    uv run python -m gate.intake.ingest   # one-time: embed the policy corpus

Run:
    uv run python examples/demo.py                 # all three beats
    uv run python examples/demo.py 003_auth_change # a single beat
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from gate.config import get_settings
from gate.graph.build import compile_graph
from gate.graph.state import initial_state
from gate.intake.loader import load_pr
from gate.logging import configure_logging

# (pr_id, what it demonstrates, scripted human decisions as (action, note) pairs)
BEATS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "001_docs_typo",
        "cost gate #1 — a docs typo scores below the risk threshold and auto-approves with "
        "zero Sonnet spend",
        [],
    ),
    (
        "002_new_dependency",
        "policy block + human reject — a GPL-3.0 dependency trips the license specialist; "
        "reconcile routes to the human, who rejects",
        [("reject", "")],
    ),
    (
        "003_auth_change",
        "autonomous investigation + human-in-the-loop loop — the security specialist needs "
        "context, the Agent SDK sub-agent investigates under budget (cost gate #2), the reviewer "
        "requests more info, a re-investigation runs, and the reviewer then approves",
        [("request_info", "confirm the signature check is enforced"), ("approve", "")],
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


def _narrate(pr_id: str, title: str, demonstrates: str, v: dict[str, Any]) -> None:
    settings = get_settings()
    print("=" * 96)
    print(f"PR {pr_id} — {title}")
    print(f"Demonstrates: {demonstrates}")
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
        print(f"  investigate: {_field(inv, 'summary')}")
        print(
            f"               files={files} tool_calls={_field(inv, 'tool_calls_count')} "
            f"est_cost=${cost:.4f}  (cost gate 2)"
        )
        for item in _field(inv, "findings") or []:
            print(f"               - {item}")

    esc = v.get("escalation")
    if esc:
        print(f"  escalation : {_field(esc, 'reason')} — {_field(esc, 'detail')}")

    verdict = v.get("verdict")
    if verdict:
        print()
        print(f"  VERDICT    : {_field(verdict, 'outcome')} — {_field(verdict, 'rationale')}")


async def _drive(graph: Any, pr_id: str, decisions: list[tuple[str, str]]) -> dict[str, Any]:
    """Run one review to completion, feeding scripted human decisions at each interrupt."""
    thread_id = uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}
    await graph.ainvoke(initial_state(pr_id, thread_id), config)

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


async def _run(only: str | None) -> int:
    configure_logging(logging.WARNING)  # silence per-node logs so the narration stays readable
    settings = get_settings()
    if not settings.anthropic_api_key:
        print(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env, set the key, then run:\n"
            "  uv run python -m gate.intake.ingest   # one-time policy ingest",
            file=sys.stderr,
        )
        return 1

    beats = [b for b in BEATS if only is None or b[0] == only]
    if not beats:
        ids = ", ".join(b[0] for b in BEATS)
        print(f"unknown PR id {only!r}; choose one of: {ids}", file=sys.stderr)
        return 1

    async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
        graph = compile_graph(checkpointer)
        for pr_id, demonstrates, decisions in beats:
            title = load_pr(pr_id).title
            values = await _drive(graph, pr_id, decisions)
            _narrate(pr_id, title, demonstrates, values)
            print()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PR Compliance Gate demo reviews.")
    parser.add_argument(
        "pr_id",
        nargs="?",
        help="run a single beat (e.g. 003_auth_change); default runs all three",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.pr_id)))


if __name__ == "__main__":
    main()
