---
artifact: ITER_05
status: ready
created: 2026-08-08
scope: Record every shipped deviation from the v1 plans — run command, reducers, model tier settings, packaging, and the fixes found running the pipeline for real
patch: true
sections_changed: [03, 04, 05, 06]
sections_unchanged: [01, 02]
depends_on: [ITER_04]
---

# ITER_05 — v1 Patch Notes (retroactive)

**This artifact is written after the fact.** Every change below is already in the code, shipped
as `v0.1.1` and `v0.1.2`. It exists so the plan family stops lying about what the code does. No
implementation follows from it.

## §01 · Concept

> Unchanged — see SKELETON § 01

## §02 · Architecture

> Unchanged — see SKELETON § 02 and the deltas in ITER_01–ITER_03 § 02.

Stated explicitly because it is the reason this is a patch rather than an iteration: **no new
entity, no new API route, no new screen, no concept change.** The seven routes in SKELETON § 02
are the seven routes in `api/routes.py`; the domain models in `state.py` are the SKELETON set
plus the two extensions ITER_01 and ITER_02 already specified (`PolicyChunk.distance`,
`RiskAssessment.model_used`).

## §03 · Tech Stack

**Packaging shipped, though ITER_04 deferred it.** "Packaging / registry distribution (PyPI,
npm)" is in the terminator's out-of-scope list. It was built anyway, because a portfolio artifact
that cannot be installed is harder to evaluate than one that can. It adds no entity, route,
screen, or concept — so it lands here rather than as a new iteration.

- **Build backend:** hatchling. `data/` and `static/` are `force-include`d into the wheel at
  `gate/_bundled/`, so an installed package can ingest the corpus, serve the UI, and run the
  fixtures with no checkout. Adding a runtime asset directory now means adding it to that map.
- **License:** Apache-2.0 via PEP 639 (`license = "Apache-2.0"` + `license-files`), plus authors,
  trove classifiers, and project URLs.
- **CI/tooling, none of it in the plan:** GitHub Actions `ci.yml` (ruff, `mypy --strict`, the
  coverage-gated suite on 3.12 and 3.13); a publish workflow (build → `pip-audit` → Trusted
  Publishing) committed as `publish.yml.disabled` so GitHub never registers it; `pre-commit`
  running `ruff check --fix` + `ruff format`.
- **Not published.** The wheel is built and verified standalone, but PyPI publication is on hold:
  intake is local fixtures, and a package on the index implies it is worth installing for real
  work. Live GitHub intake is the gate for publishing.

> Everything else unchanged — see ITER_01 § 03 and ITER_03 § 03.

## §04 · Backend

### The run command loses `--reload`

SKELETON § 04 documents `uv run uvicorn gate.api.app:app --reload`. **That command disables the
investigator.** `--reload` (and `--workers`) sets uvicorn's `use_subprocess`, which forces a
`SelectorEventLoop` even on Windows; that loop cannot spawn the subprocess the Claude Agent SDK
needs and raises a bare `NotImplementedError` with an empty message, surfacing as
`Failed to start Claude Code:` and nothing else. `--reload` is gone from every documented
command, and `run_investigation` preflights the loop and returns an inconclusive report naming
the cause instead of the empty message.

### The Agent SDK subprocess needs credentials passed explicitly

`pydantic-settings` reads `ANTHROPIC_API_KEY` from `.env` into `Settings`; it never writes
`os.environ`. The Claude Code CLI the SDK spawns inherits `os.environ` — so the investigator,
alone among the components, ran unauthenticated. Credentials now go through
`ClaudeAgentOptions.env`. Everything else receives the key as an argument, which is why a missing
key showed up *only* as a failing investigation.

### `POST /api/reviews` does not await the graph

The SKELETON § 04 stub awaits `graph.ainvoke(...)` inside the handler. Reality: the handler
validates the fixture, registers the thread, and spawns the run as a tracked background task,
returning `{thread_id}` immediately — the UI polls (ITER_04 § 05 already assumed polling, which
an awaiting handler cannot serve). A session-scoped in-memory registry backs `GET /api/reviews`
so a thread is listable before its first checkpoint exists.

**First-review race (fixed in 0.1.2):** a review is registered before its background run writes a
checkpoint, and the detail page polls immediately, so the first review after a server start
returned `404 Review not found.` and the UI stopped polling. A known thread now reads as
`running` until its first checkpoint exists.

### Reducers replace `operator.add`

ITER_02 § 04 specifies `specialist_findings: Annotated[list, operator.add]`. Appending is wrong
across an investigation loop: a second specialist pass accumulates stale `needs_more_context`
findings forever and reconcile never drains the budget. Two custom reducers instead:

- `merge_findings` — replaces a specialist's findings when it emits a fresh batch, so a loop
  supersedes the prior round.
- `coalesce_escalation` — first-write-wins on `escalation`, required because both specialists run
  in the same superstep and can write the channel concurrently.

### Checkpoint allowlist

Not in any plan. LangGraph's msgpack deserializer only reconstructs allowlisted types; everything
in `state.py` was unregistered, so every checkpoint read logged
`Deserializing unregistered type …` and the library says a future version will refuse them —
returning bare dicts where models are expected. Every checkpointer is now wrapped:
`compile_graph(use_state_allowlist(saver))` (new `graph/checkpoint.py`), with
`CHECKPOINT_ALLOWLIST` spelled out at the bottom of `state.py`. Note the trap: `saver.with_allowlist(...)`
*merges* into the existing allowlist, whose default is the sentinel `True`, so merging is a silent
no-op — `use_state_allowlist` constructs the serializer instead.

### `pr_id` is validated at the boundary

`pr_id` arrived from the request body and was joined onto the fixture directory unvalidated — and
the same value becomes the investigator's sandbox root, so a `..` or absolute segment could
relocate the sandbox itself, outside the checks `resolve_in_sandbox` applies *within* it.
`load_pr` now rejects any id that is not a plain directory name (`FixtureError` → 422).

### Config gained four env vars

Beyond the plan's list (SKELETON § 04 + `GATE_MAX_DIFF_CHARS`, `GATE_EMBED_MODEL` from ITER_01 +
the three `GATE_INVESTIGATOR_*` from ITER_03):

- `GATE_RISK_HIGH_BOUND` (default 75) — ITER_02 § 04 called the high bound "fixed"; it is
  env-driven for the same reason the threshold is.
- `GATE_MODEL_CLASSIFIER_EFFORT`, `GATE_MODEL_SPECIALIST_EFFORT`, `GATE_MODEL_INVESTIGATOR_EFFORT`
  — see § 06.
- `POLICY_COLLECTION` (default `policies`) — the Chroma collection name, previously implicit.

### Modules the plan's tree does not name

`paths.py` (asset roots anchored to `__file__`, bundled copy first, repo root as fallback — the
plan's CWD-relative assumption breaks in an installed package); `errors.py` (`GateError`
hierarchy, mapped to status codes by one handler in `api/app.py`); `logging.py` (structlog +
correlation-id middleware, from the project's service standards, not the plan);
`graph/checkpoint.py`; `intake/store.py`; `examples/demo.py`.

### Tests

The plan names one file, `tests/test_graph_smoke.py`. Shipped: `tests/unit/`,
`tests/integration/`, `tests/system/`, with **100% statement and branch coverage of `src/gate`**
enforced by `fail_under = 100`; the `llm`-marked system evals are excluded from that measurement
on purpose. Two consequences worth recording:

- The evals were **uncollectable**: they are named `eval_*.py`, which pytest's default
  `python_files` never matches, so `pytest -m llm` silently selected zero tests despite being
  documented in three places. `python_files` now includes `eval_*.py`.
- Retrieval's empty-query fallback was deleted rather than excluded — it was unreachable.

### `DATA-*` policies route to the security specialist

ITER_02 § 04 slices `policy_context` by `SEC-*` / `LIC-*` prefix and never says where the
data-handling corpus goes. It goes to security (`security = (SEC, DATA)`), which would otherwise
be silently unreviewed.

### Documentation the plan does not mention

`docs/guide/` (a numbered user and operator guide, `HT-01`…`HT-04` and `OP-01`…`OP-05`, with a
glossary and an incident runbook), `ARCHITECTURE.md`, `CLAUDE.md`, `CHANGELOG.md`, and a rewritten
README first screen. The install guide's `pip install` path was also corrected — it pointed
readers at `.env.example`, a file no wheel ships.

## §05 · Frontend

- **Keyboard reachability (fixed in 0.1.1):** "Reviews", "← Reviews", and the per-review links
  were `<a>` elements without `href`, so they took no focus and were unreachable by keyboard.
  They are `<button>`s.
- **Error banners:** the red-banner pattern from SKELETON § 05 now also carries `Review not
  found.`, `Connection issue: …`, and the invalid-fixture-id message; all three are listed in the
  reviewer troubleshooting table.

Everything else unchanged — see SKELETON § 05 and ITER_04 § 05.

## §06 · LLM / Prompts

- **Models:** specialists and investigator default to `claude-sonnet-5`, not the
  `claude-sonnet-4-6` named in SKELETON § 06. Classifier and risk scorer stay on
  `claude-haiku-4-5`. Still env-read, never hardcoded.
- **Reasoning effort per tier** (`low | medium | high | xhigh | max`, default `high`), paired with
  each tier's model id. Withheld from any model id containing `haiku`, which has no effort control
  and rejects the parameter. The investigator's effort applies to both its Agent SDK tool loop and
  its final summarizing turn.
- **No `temperature=0` on effort-carrying tiers.** An effort setting turns on adaptive thinking,
  which the API pairs with its own default temperature. Haiku tiers are unchanged.
- **Structured output method is chosen by effort.** SKELETON § 06 says
  `.with_structured_output()` and stops there — its default is `function_calling`, and the API
  rejects a forced tool call while thinking is on, so specialists quietly lost the structured
  guarantee and answered in prose (`ValidationError`). Effort-carrying tiers now use Claude's
  dedicated structured output (`json_schema`); tiers without an effort keep `function_calling`.

Prompts themselves are unchanged — see ITER_01 § 06, ITER_02 § 06, ITER_03 § 06, ITER_04 § 06.

## Released as

| Release | Carries |
|---|---|
| `v0.1.1` | § 03 packaging block; `pr_id` validation; asset-path resolution; eval collection; keyboard fix; install-guide correction |
| `v0.1.2` | § 06 in full; checkpoint allowlist; `--reload`; SDK credentials; first-review race; `POLICY_COLLECTION`; guide renumbering |

Reducers, background-task execution, the `DATA-*` routing, the module set, and the test layout
predate `v0.1.0` — they were built directly into the MVP and never written back into the plans.
They are recorded here for the same reason as the rest.

## Out of MVP scope

> Unchanged — see ITER_04 "Out of MVP scope", **minus** packaging/registry distribution, which
> shipped (see § 03; publication itself remains on hold behind live GitHub intake).
