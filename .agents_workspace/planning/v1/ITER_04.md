---
artifact: ITER_04
status: ready
created: 2026-07-12
scope: Live HITL web gate — decision endpoint with checkpointed resume, enabled UI decision flow, scripted demo
sections_changed: [04, 05, 06]
sections_unchanged: [01, 02, 03]
depends_on: [SKELETON, ITER_01, ITER_02, ITER_03]
mvp: true
mvp_target: A locally runnable compliance gate where a sample PR flows classify → risk → specialists → investigation → human web approval → verdict, checkpoint-resumable, with cost gates and engineered failure modes demonstrable end-to-end
---

# ITER_04 — Human-in-the-Loop Gate and Demo (MVP)

## §01 · Concept

> Unchanged — see SKELETON § 01

## §02 · Architecture

> Unchanged — see SKELETON § 02 and deltas in ITER_01–ITER_03 § 02. No new components or entities; this iteration wires the already-specified `/pending` and `/decision` routes to the already-specified `human_gate` interrupt.

## §03 · Tech Stack

> Unchanged — see ITER_03 § 03 (no new dependencies; polling, not SSE/WebSockets — see § 05)

## §04 · Backend

**`GET /api/reviews/{thread_id}/pending` (real):** reads the thread's checkpoint; if the graph is interrupted at `human_gate`, returns the interrupt payload `{pr_id, risk, findings, investigation, escalation}` (the review packet the human needs); otherwise `204`. Unknown `thread_id` → `404` (no ownership dimension — auth is out of scope, so 403-vs-404 leakage doesn't apply; noted deliberately).

**`POST /api/reviews/{thread_id}/decision` (real):** body `{action: approve|reject|request_info, note}` →

1. Validate the thread is actually interrupted at `human_gate`; if not, `409 {"error": "no pending decision"}` — this also makes double-submits harmless (second click hits 409), the idempotency story for the demo.
2. Write `HumanDecision` and resume via `Command(resume=...)` on the checkpointed thread.
3. Routing on resume: `approve`/`reject` → `verdict` (mapping per ITER_02 § 04). `request_info` → the graph routes back through `investigate`, which increments `loop_count` (sole-increment invariant holds); if the loop budget is already exhausted, reconcile's rule 4 re-escalates and the gate re-interrupts with `escalation.reason="max_cycles"` — the human sees *why* rather than looping silently. Human `request_info` deliberately shares the machine loop budget: `GATE_MAX_CYCLES` bounds total investigations per review regardless of who asked.
4. Response: `{status: "resumed"}`; the UI re-polls for the outcome.

**README demo script (authored this iteration):** setup (`uv sync`, `.env`, ingest), then the three-fixture arc — 001 docs typo → auto-approved, no Sonnet spend (cost gate #1); 002 dependency add → license `block` finding → human gate → reject; 003 auth change → specialist uncertainty → investigation (tool calls + cost visible) → human gate → request more info → re-investigation → approve. Each beat names the failure-mode/cost feature it demonstrates — this script *is* the portfolio artifact.

**Tests:** decision-endpoint tests (mocked graph): 409 on non-interrupted thread, resume called with correct `Command`, `request_info` at exhausted budget re-interrupts with `max_cycles`.

## §05 · Frontend

- **DecisionPanel enabled** (the SKELETON § 05 convention closes here): buttons active only when `/pending` returns a payload; renders the review packet — risk, findings grouped by specialist with severity badges, investigation summary, escalation banner when present.
- **Polling:** detail view polls `/api/reviews/{thread_id}` + `/pending` every 2 s while status is `running` or `awaiting_human`; stops on `done`. List view polls every 5 s. (Polling over SSE: trivial to implement, no keep-alive/heartbeat machinery, perfectly adequate for a local single-user demo.)
- **States:** submitting a decision disables the panel with an inline spinner until the poll reflects the resume; a 409 shows "already decided — refreshing" and re-polls; network errors keep the existing red-banner pattern from SKELETON § 05.

## §06 · LLM / Prompts

- **No new prompts.** The `request_info` note is appended to the open-questions input the investigator already consumes (ITER_03 § 06) — the human's question becomes a first-class investigation lead.
- **Evaluation:** `tests/eval_demo.py` (marker `llm`) runs the full three-fixture demo arc headlessly, driving decisions through the API, asserting each fixture's final `verdict.outcome` matches a new `expected_outcome` field in `meta.json`. Green run = the demo is presentable.

## Out of MVP scope

- Live GitHub intake via GitHub MCP (fixtures only for now)
- mcp-cassette record/replay in tests (separate track)
- Auth / multi-user on the web UI
- Hosted or containerized deployment
- Packaging / registry distribution (PyPI, npm)
- Observability dashboards (LangSmith/tracing beyond stdout logging)
- Pagination on list endpoints
- Real static-analysis tooling (semgrep etc.) as investigator tools
- Prompt benchmarking beyond fixture smoke evals
