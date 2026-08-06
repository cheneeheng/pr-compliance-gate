# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An agentic pipeline that reviews pull requests against organizational policy (security,
licensing, data-handling) before merge. It is a **portfolio artifact** demonstrating
production-grade agent orchestration — cost gating, failure modes, human-in-the-loop — not a
hosted product. Intake is from local fixtures, not live GitHub; it is single-user with no auth.

## Commands

```bash
uv sync --extra dev                       # install (plain `uv sync` skips the dev extra)
uv run pre-commit install                 # once per clone: ruff check --fix + format on commit
cp .env.example .env                      # then set ANTHROPIC_API_KEY for real runs
uv run python -m gate.intake.ingest       # embed the policy corpus into Chroma (one-time)
uv run uvicorn gate.api.app:app --reload  # serve API + UI at http://127.0.0.1:8000

uv run pytest                             # default suite — NO API calls (see addopts below)
uv run pytest --cov                       # same suite + coverage; fails under 100%
uv run pytest -m llm                      # behavioral evals hitting the real Anthropic API (needs key + ingest)
uv run pytest tests/unit/test_reconcile.py::test_name   # single test
uv run ruff check . && uv run ruff format .
uv run mypy src                           # strict mode
```

`pytest` defaults to `-m 'not llm'` (`pyproject.toml addopts`), so the plain command never spends
money or needs a key — the `_test_env` fixture injects a dummy `ANTHROPIC_API_KEY` and points
storage at tmp paths.

Tests are split by what they touch, `conftest.py` at the root applying to all three:

- `tests/unit/` — pure functions, no I/O and no graph (reducers, reconcile's rule ladder, the
  investigator's sandbox and tool closures, the fixture loader, model factories).
- `tests/integration/` — real components wired together with only the LLM/SDK boundary stubbed
  (graph nodes, the API against a fake graph, the investigator harness, ingestion/retrieval).
- `tests/system/` — the `llm`-marked `eval_*.py` files: the real pipeline against the real API.

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
intake -> classify (Haiku) -> retrieve_policy -> risk_score (Haiku)
  score < GATE_RISK_THRESHOLD -> verdict(auto_approved)        [COST GATE #1: no Sonnet spend]
  else -> {security, license} specialists (Sonnet, parallel)   -> reconcile
    reconcile routes to: investigate | human_gate | verdict
    investigate (Agent SDK) loops back to specialists          [COST GATE #2]
    human_gate: approve|reject -> verdict ; request_info -> investigate
```

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
- Three **read-only sandboxed tools** (`read_file`, `grep_repo`, `list_dir`) over the PR's repo
  snapshot (`data/sample_prs/<pr_id>/repo/`). `resolve_in_sandbox` blocks `..` traversal and
  absolute-path escapes and is pure/module-level so it's unit-testable without the SDK.
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
  hatch `force-include`) when present, else the repo root. Adding a runtime asset directory means
  adding it to that `force-include` map too. `repo_snapshot_dir(pr_id)` is the investigator
  sandbox root. Writable state (`checkpoints.db`, `.chroma/`) is CWD-relative on purpose: an
  installed package must not write into `site-packages`.
- **Fixtures**: a sample PR is `data/sample_prs/<pr_id>/` with `meta.json`, `diff.patch`, and a
  `repo/` snapshot. Changed-file paths are parsed from `diff --git` headers. Malformed fixtures
  fail loud with `FixtureError` (422), never a silently empty review.
- **No CORS / no auth**: the FastAPI app serves the UI (`static/index.html`) same-origin. This is
  deliberate for the single-user demo — do not add CORS middleware.
- Checkpointed sub-values may deserialize as **either dicts or Pydantic instances** — code that
  reads `snapshot.values` (e.g. `list_reviews`) must handle both.
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

Live GitHub intake, auth/multi-user, hosted deployment, tracing dashboards, real static-analysis
investigator tools. See `.agents_workspace/planning/v1`.
