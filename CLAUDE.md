# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An agentic pipeline that reviews pull requests against organizational policy (security,
licensing, data-handling) before merge. It is a **portfolio artifact** demonstrating
production-grade agent orchestration — cost gating, failure modes, human-in-the-loop,
observability — not a hosted product. Intake is from live GitHub (by PR URL, via the GitHub MCP
server) *or* local fixtures; it is single-user with no auth and the GitHub token is read-only.

## Commands

```bash
uv sync --extra dev                       # install (plain `uv sync` skips the dev extra)
uv run pre-commit install                 # once per clone: ruff check --fix + format on commit
uv sync --extra semgrep                   # optional: the investigator's static-analysis tool
cp .env.example .env                      # then set ANTHROPIC_API_KEY (+ GATE_GITHUB_TOKEN for live PRs)
docker compose -f docker-compose.langfuse.yml up -d   # optional: trace backend
uv run uvicorn gate.api.app:app           # serve API + UI at http://127.0.0.1:8000
# NOTE: no --reload. On Windows it selects a SelectorEventLoop, which cannot spawn a
# subprocess, and three things need one: the Agent SDK investigator, the workspace clone, and
# semgrep. All three call `gate.runtime.loop_supports_subprocess()` first.
# There is no ingest step: policy retrieval is a deterministic file lookup.

uv run pytest                             # default suite — NO API calls, NO network
uv run pytest --cov                       # same suite + coverage; fails under 100%
uv run pytest -m llm                      # behavioral evals hitting the real Anthropic API (needs key)
uv run pytest -m network                  # live GitHub smoke tests (needs GATE_GITHUB_TOKEN)
uv run pytest tests/unit/test_reconcile.py::test_name   # single test
uv run ruff check . && uv run ruff format .
uv run mypy src                           # strict mode
```

`pytest` defaults to `-m 'not llm and not network'` (`pyproject.toml addopts`), so the plain
command never spends money, needs a key, or touches the network — the `_test_env` fixture injects a
dummy `ANTHROPIC_API_KEY`, points storage at tmp paths, and sets `GATE_OTEL_ENABLED=false` (a live
OTLP exporter would spend its full retry budget on every lifespan teardown).

Tests are split by what they touch, `conftest.py` at the root applying to all three:

- `tests/unit/` — pure functions, no I/O and no graph (reducers, reconcile's rule ladder, the
  investigator's sandbox and tool closures, the fixture loader, model factories).
- `tests/integration/` — real components wired together with only the LLM/SDK/MCP boundaries
  stubbed (graph nodes, the API against a fake graph, the investigator harness, the GitHub adapter
  against hand-captured JSON, the workspace clone against a local bare repo).
- `tests/system/` — the `llm`- and `network`-marked `eval_*.py` files: the real pipeline against
  the real API and, for beat 4, a real public PR.

The default (`unit` + `integration`) run must keep **100% statement and branch coverage** of
`src/gate` — `[tool.coverage.report] fail_under = 100` enforces it, so a new branch needs a new
test. The `system/` evals are excluded from that measurement on purpose; they cost money and
must never be load-bearing for coverage.

## Architecture

The system is a **LangGraph state machine** (`src/gate/graph/`) checkpointed to SQLite after
every node, fronted by a thin FastAPI app (`src/gate/api/`) that runs graph invocations as
background tasks and polls state.

### The graph is the whole application

`ReviewState` (`graph/state.py`) is the single source of truth — every node returns a partial
dict that LangGraph merges into it, then checkpoints. Understand this file first: it holds all
domain models (which double as LLM structured-output targets, hence `ConfigDict(extra="forbid")`
+ closed-set `StrEnum`s) and two **custom reducers** whose comments explain non-obvious concurrency
semantics:

- `merge_findings` — replaces a specialist's findings when it emits a fresh batch (so an
  investigation loop supersedes the prior round instead of accumulating stale `needs_more_context`
  findings forever).
- `coalesce_escalation` — first-write-wins, required because both specialists run in the same
  superstep and could both write the escalation channel concurrently.

Flow (`graph/build.py` docstring is the authoritative wiring):
```
PRSource.resolve (handler) -> thread registered -> PRSource.load (background) -> ainvoke
intake -> human_gate            (escalation seeded by a failed load; the intake short-circuit)
intake -> classify (Haiku) -> retrieve_policy -> risk_score (Haiku)
  score < GATE_RISK_THRESHOLD -> verdict(auto_approved)        [COST GATE #1: no Sonnet spend]
  else -> {security, license} specialists (Sonnet, parallel)   -> reconcile
    reconcile routes to: investigate | human_gate | verdict
    investigate (Agent SDK) loops back to specialists          [COST GATE #2]
    human_gate: approve|reject -> verdict ; request_info -> investigate
```

### Three subsystems added in v2

- **Deterministic policy retrieval** (`intake/retrieval.py`): `data/policies/index.yaml` maps each
  `change_type` to policy files, plus whole-token keyword overrides against `touched_areas`.
  There is no vector store and no embedding model. `validate_policy_index()` runs in the lifespan
  and fails the *process* on an unmapped enum member, a missing file, or a heading without a
  parseable `policy_id` — a runtime "unknown change_type → empty list" fallback would be
  unreachable in a correct install, and unreachable branches are what this codebase deletes rather
  than pragmas.
- **Tracing** (`telemetry/tracing.py`): OTel spans exported over OTLP/HTTP to a self-hosted
  Langfuse. Read that module's docstring before touching it — it documents three traps (the run
  span must be a *root* linked to the request span because the run outlives the request; the
  parallel specialist fan-out needs an explicit `context=` because contextvars do not follow into
  LangGraph branches; the lifespan must drain background tasks *before* `provider.shutdown()`).
  `tracer()` reads the module's own `_provider`, not the OTel global, because
  `trace.set_tracer_provider` is a process-wide once-guard that would silently refuse the kill
  switch.
- **Live intake** (`intake/sources/`): the `PRSource` protocol has two methods on purpose.
  `resolve` is cheap, runs in the handler, and raises HTTP-mapped errors; `load` is slow, runs in
  the background task, and fails by returning an `EscalationRecord`. Collapsing them to one would
  force either a POST that blocks on a clone or a typo'd URL creating a review row.

### Two cost gates are the point of the design

The demo arc (README "Demo script") exists to exercise them. Do not collapse them:
1. **Risk gate** (`build.py::_route_after_risk`): sub-threshold changes never invoke a Sonnet
   specialist.
2. **Investigator gate** (`reconcile.py` rule 3): the Agent SDK sub-agent runs only when a
   specialist set `needs_more_context` AND loop budget (`GATE_MAX_CYCLES`) remains.

### reconcile is the routing brain — pure Python, no LLM

`graph/nodes/reconcile.py` computes the next route from accumulated findings + loop budget via a
first-match-wins rule ladder, writing `state.route` for the conditional edge to read. Routing
decisions live here, not in an LLM. `loop_count` is incremented **only** by the `investigate`
node so a checkpoint resume can't double-count.

### The investigator is an Agent SDK loop nested inside one graph node

`agents/investigator.py`: LangGraph sees a single `investigate` node; the Claude Agent SDK owns
the inner tool loop.

`agents/` means "owns its own control flow", not "does LLM work" — so it holds the investigator
and nothing else. The specialists stay in `graph/nodes/` because a specialist is one stateless
`structured_call` (no tools, no loop), structurally identical to `classify` and `risk_score`. The
6-line `graph/nodes/investigate.py` is the seam that keeps every node uniformly "state in, partial
state out, no control flow of its own", and confines the `claude-agent-sdk` import — the one
dependency whose surface moves — to a single module. Do not merge the harness into the node.

Key invariants:
- Four **read-only sandboxed tools** (`read_file`, `grep_repo`, `list_dir`, `run_semgrep`) over
  the PR's workspace — a fixture's `repo/` snapshot or a live PR's clone, injected as
  `state.workspace_path`, **never derived from `pr_id`**. `resolve_in_sandbox` blocks `..`
  traversal and absolute-path escapes and is pure/module-level so it's unit-testable without the
  SDK.
- `run_semgrep` (`agents/tools/semgrep.py`) uses the committed `rules/` ruleset resolved through
  `paths.py` to an **absolute** path. Never `--config auto`: it fetches rules over the network at
  run time, breaking the no-network sandbox and making results non-deterministic. A *failed* run
  still increments `semgrep_runs` and still spends a tool call — otherwise a broken scanner is
  free to retry until the turn budget dies.
- **Hard budgets** on turns / tool calls / per-file bytes (`GATE_INVESTIGATOR_*`).
- **Never raises into the graph** — budget exhaustion or SDK error returns an inconclusive
  `InvestigationReport`, so failure lands on a human at the gate, never in a stack trace.
- The **harness owns the metrics** (`tool_calls_count`, `cost_estimate_usd`, `files_examined`);
  the model authors only narrative text in a final structured-output turn. Never trust metrics
  from model output.
- The SDK is young; message/cost extraction is duck-typed on purpose to tolerate surface drift.

### Human-in-the-loop = LangGraph interrupt + resume

`human_gate` raises a LangGraph `interrupt`. The API surfaces it as `awaiting_human` status
(`api/routes.py::_derive_status` reads `snapshot.tasks[].interrupts`); the review resumes via
`Command(resume=...)` on `POST /api/reviews/{thread_id}/decision`. Because state is checkpointed,
this survives process restarts.

### Layering (enforced, non-negotiable)

- **Route handlers** (`api/routes.py`) contain no business logic — load fixture, drive graph,
  shape response. Graph runs are spawned as tracked background tasks (`_spawn`).
- **Model construction** is centralized in `llm/models.py` (`structured_call`, per-tier
  factories, cost estimation) — no node builds raw messages or `ChatAnthropic` itself.
- **Prompts** live in `llm/prompts.py`.
- **Errors**: domain exceptions subclass `GateError` (`errors.py`); a single handler in
  `api/app.py` maps them to status codes (`FixtureError`->422, `ReviewNotFoundError`->404,
  `NoPendingDecisionError`->409).

## Conventions specific to this repo

- **Config**: all knobs are env-driven via `pydantic-settings` (`config.py`, `GATE_*` prefix).
  `get_settings()` is `lru_cache`d — tests must `get_settings.cache_clear()` (conftest does this).
- **Paths**: never use CWD-relative paths for shipped assets. `paths.py` anchors them to
  `ASSET_ROOT`, resolved from `__file__` — the wheel's bundled copy (`gate/_bundled/`, populated by
  hatch `force-include`) when present, else the repo root. `data/`, `static/` and `rules/` are all
  in that map; adding a runtime asset directory means adding it there too. Writable state
  (`checkpoints.db`, `data/workspaces/`) is CWD-relative on purpose: an installed package must not
  write into `site-packages`, and a clone cache especially must not.
- **Fixtures**: a sample PR is `data/sample_prs/<pr_id>/` with `meta.json`, `diff.patch`, and a
  `repo/` snapshot. Changed-file paths are parsed from `diff --git` headers. Malformed fixtures
  fail loud with `FixtureError` (422), never a silently empty review.
- **No CORS / no auth**: the FastAPI app serves the UI (`static/index.html`) same-origin. This is
  deliberate for the single-user demo — do not add CORS middleware.
- Checkpointed sub-values may deserialize as **either dicts or Pydantic instances** — code that
  reads `snapshot.values` (e.g. `list_reviews`) must handle both.
- **Checkpoint versioning**: `GATE_STATE_VERSION` is stamped into `checkpoints.db` and checked in
  the lifespan. Bump it only for a *narrowing* change (a removed or retyped field); purely additive
  fields with defaults still deserialize, and bumping for those would force a pointless delete and
  train readers to ignore the check. There is deliberately no migration — this is local demo data.
- **Checkpoint allowlist**: LangGraph's msgpack deserializer only reconstructs types on an
  allowlist. Unregistered ones warn today (`Deserializing unregistered type …`) and the library
  says it will refuse them in a future version — at which point state comes back as a bare dict
  instead of the model. So **every checkpointer is wrapped**:
  `compile_graph(use_state_allowlist(saver))`, in `api/app.py` and `examples/demo.py` alike.
  `use_state_allowlist` (`graph/checkpoint.py`) *constructs* a `JsonPlusSerializer` with the
  allowlist. Do **not** reach for `saver.with_allowlist(...)`: it merges into the existing
  allowlist, and the default allowlist is the sentinel `True`, so merging is a silent no-op that
  returns the saver unchanged — it appears to work and fixes nothing unless
  `LANGGRAPH_STRICT_MSGPACK=true` is set, which is exactly how the trap gets past a test.
  `CHECKPOINT_ALLOWLIST` (bottom of `graph/state.py`) names every model and `StrEnum` in that
  module, deliberately spelled out rather than derived from `vars()` — an allowlist is a security
  boundary and should be readable. **Adding a type to `state.py` means adding it there too**;
  `test_state.py::test_the_checkpoint_allowlist_covers_every_type_in_this_module` fails if you
  forget. `tests/integration/test_checkpoint_allowlist.py` asserts the round trip warns nothing
  and that undeclared types are refused — assert on both, since only the second catches a no-op.
- **Three components need an event loop with subprocess support**: the Agent SDK (which drives
  the Claude Code CLI), the workspace clone (`git`), and semgrep. `uvicorn --reload` (and
  `--workers`) sets `use_subprocess`, which forces a `SelectorEventLoop` even on Windows
  (`uvicorn/loops/asyncio.py`), and that loop raises a bare `NotImplementedError` — empty message —
  which the SDK surfaces as `Failed to start Claude Code:` with nothing after it. **Never document
  or run `--reload`.** All three call the one shared check, `gate.runtime.loop_supports_subprocess`,
  and degrade with `gate.runtime.UNSUPPORTED_LOOP_REASON` naming the cause. A fourth call site
  means calling that check, not writing a fourth copy.
- **The Agent SDK subprocess does not inherit the API key.** `pydantic-settings` reads `.env`
  into `Settings`; it never writes `os.environ`. The Claude Code CLI the SDK spawns inherits
  `os.environ`, so the investigator must pass credentials explicitly via `ClaudeAgentOptions.env`
  (`agents/investigator.py`). Everything else gets the key as an argument, which is why a missing
  key shows up *only* as a failing investigation.

## Toolchain rules (from pyproject.toml)

- Python 3.12+, `mypy strict = true` with the pydantic plugin — do not downgrade to silence errors.
- ruff only (select `E,F,I,UP,B,SIM`), line length 100. Use 3.12 built-in generics.
- Third-party packages without stubs are listed under `[[tool.mypy.overrides]]` with
  `ignore_missing_imports` — extend that list rather than scattering `# type: ignore`.

## Out of MVP scope

mcp-cassette record/replay, prompt benchmarking beyond fixture smoke evals, writing results back to
GitHub (the token is read-only by design), webhook/GitHub-App triggers, auth/multi-user, hosted
deployment *of the application* (the Langfuse compose file is a dev-time sidecar), pagination on
list endpoints, org-wide policy sync, and embedding-based retrieval (revisit only at the threshold
documented in `ITER_01_v2 §02`). See `.agents_workspace/planning/v2`.
