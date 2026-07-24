# PR Compliance Gate

An agentic pipeline that reviews pull requests against organizational policy (security,
licensing, data-handling) before merge. A cheap classifier and risk scorer gate the pipeline;
medium/high-risk changes fan out to specialist agents, which can request deeper autonomous
investigation; high-risk or contested reviews stop at a human approval gate. Every step is
checkpointed to SQLite and resumable.

This is a portfolio artifact demonstrating production-grade agent orchestration — failure modes,
cost gating, and human-in-the-loop — not a hosted product.

## Pipeline

```
intake -> classify (Haiku) -> retrieve_policy -> risk_score (Haiku)
  risk < threshold            -> verdict (auto_approved)          [cost gate #1: no Sonnet spend]
  else -> {security, license} specialists (Sonnet, parallel, timeout-wrapped)
       -> reconcile (pure-Python routing brain)
            needs_more_context & budget -> investigate (Agent SDK sub-agent)  [cost gate #2]
            block / escalation           -> human_gate (interrupt)
            clean                         -> verdict
  human_gate: approve|reject -> verdict ; request_info -> investigate
```

## Setup

```bash
uv sync                      # install dependencies
cp .env.example .env         # then set ANTHROPIC_API_KEY
uv run python -m gate.intake.ingest   # embed the policy corpus into Chroma
```

## Run

```bash
uv run uvicorn gate.api.app:app --reload
```

Open http://127.0.0.1:8000 — the FastAPI app serves the UI (same origin, no CORS, no auth: a
single-user local demo).

## Demo script

Each beat names the failure-mode or cost feature it demonstrates. This arc is the artifact. Run
it headless (no UI, scripted human decisions, narrated stage-by-stage) with
`uv run python examples/demo.py` — each beat below maps to one section of its output.

1. **`001_docs_typo` — auto-approved, no Sonnet spend.** A README typo classifies as `docs`,
   scores below the risk threshold, and exits at `verdict(auto_approved)` straight from the
   Haiku scorer. **Cost gate #1**: a specialist is never invoked.

2. **`002_new_dependency` — license block, human reject.** Adding a GPL-3.0 runtime dependency
   classifies as `dependency`, clears the risk gate into the specialists, and the license
   specialist raises a `block` finding citing `LIC-02`. Reconcile routes to the human gate; the
   reviewer clicks **Reject**. Verdict: `rejected`.

3. **`003_auth_change` — investigation then human approve.** A session-validation refactor
   classifies as `code`. The security specialist cites `SEC-03` and sets `needs_more_context`
   (it cannot confirm the change is safe from the diff alone). Reconcile routes to
   **investigate** — the Claude Agent SDK sub-agent reads `src/acme/auth.py` and the logging
   config under hard turn/tool/byte budgets (tool-call count and cost estimate are visible in
   the UI). **Cost gate #2**: the investigator runs only because reconcile's rule 3 fired. The
   review reaches the human gate; the reviewer clicks **Request more info** (the note becomes an
   investigation lead), a re-investigation runs within the shared loop budget, and the reviewer
   then **Approves**. Verdict: `approved`.

## Configuration

All configuration is env-driven (see `.env.example`). Notable knobs:

| Var | Meaning |
|---|---|
| `GATE_RISK_THRESHOLD` / high bound (75) | cost gate #1 boundary and the low/medium/high split |
| `GATE_MAX_CYCLES` | total investigations per review (machine + human `request_info` share it) |
| `GATE_SPECIALIST_TIMEOUT_S` | per-specialist timeout; on timeout the review escalates, never hangs |
| `GATE_INVESTIGATOR_MAX_TURNS` / `_MAX_TOOL_CALLS` / `_MAX_FILE_CHARS` | investigator hard budgets |
| `GATE_MODEL_*` | model per tier (Haiku for the cheap gate, Sonnet for specialists/investigator) |

Model ids in `.env.example` are env-overridable defaults — verify them against the live
Anthropic API before a real run.

## Tests

```bash
uv run pytest            # default suite (no API calls)
uv run pytest -m llm     # behavioral evals that hit the real Anthropic API (needs a key + ingest)
```

## Out of MVP scope

Live GitHub intake, auth/multi-user, hosted deployment, packaging, tracing dashboards,
pagination, real static-analysis investigator tools. See `.agents_workspace/planning/v1`.
