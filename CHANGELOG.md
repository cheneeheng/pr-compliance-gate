# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

**PyPI publication is coming within the next few patch releases.** v0.2.0 lifted the hold — the
condition was live GitHub intake, which it ships — but did not act on it. Publishing needs the
one-time Trusted Publishing setup on pypi.org and `.github/workflows/publish.yml.disabled` renamed
back to `.yml`; until that lands, install from a checkout or a wheel you build yourself.

## [0.2.0] - 2026-08-12

The v2 plan family: live GitHub intake, end-to-end tracing, deterministic policy retrieval, and
real static analysis inside the investigator.

The pipeline reads real pull requests now, which was the stated condition for lifting the PyPI
hold. The hold is lifted; publishing remains a separate decision and this release does not take
it — `publish.yml` is still committed as `.disabled`, so no trigger in it can fire.

### Added

- **Live GitHub pull-request intake.** `POST /api/reviews` now takes `pr_url` as an alternative
  to `pr_id` (exactly one required). A `PRSource` adapter splits the work at the request/background
  seam: `resolve` runs in the handler and maps a bad URL, a missing repo, or an under-scoped token
  to `422`/`404`/`403` *before* a review thread exists; `load` runs in the background and fails by
  escalation instead. Metadata, the paginated changed-file list, and the diff come from the GitHub
  MCP server; the repo tree is a shallow clone at `head_sha`, LRU-evicted against
  `GATE_WORKSPACE_MAX_GB`, which becomes the investigator's sandbox root.
- **OpenTelemetry tracing** exported over OTLP/HTTP to a self-hosted Langfuse
  (`docker-compose.langfuse.yml`, a dev-time sidecar). Spans cover every graph node, every LLM
  call (with GenAI semantic-convention attributes plus effort, structured-output method, and
  thinking tokens), and every investigator turn and tool call. The run span carries
  `gate.risk.level`, `gate.route.taken`, `gate.cost.investigator_usd` and friends, which is what
  turns the README's cost-gate claims into evidence. `ReviewState.trace_id` deep-links the UI;
  `GATE_OTEL_ENABLED=false` installs a no-op tracer so the app runs with no backend.
- **`run_semgrep`, a fourth investigator tool**, over a committed, pinned ruleset in `rules/`
  whose rules map to policy ids. Budgeted by `GATE_SEMGREP_TIMEOUT_S`, `_MAX_RESULTS`, and
  `_MAX_RUNS`, and counted against the existing tool-call budget. Results reach the re-run
  specialists as citable evidence. `InvestigationReport` gains `static_analysis[]` and
  `semgrep_runs`; the UI renders them, keying its empty state off the run count so "ran and found
  nothing" is distinguishable from "never ran".
- An **intake short-circuit**: a review whose `load` failed routes straight to the human gate
  instead of paying Haiku to classify nothing and Sonnet to review nothing. It still runs through
  the graph, so the failed review stays checkpointed, resumable and traced.
- `EscalationReason` gains `pr_too_large` and `source_unavailable`; `ReviewState` gains `source`,
  `pr_ref`, `workspace_path` and `trace_id`; new entities `PRRef` and `StaticFinding`.
- `GATE_STATE_VERSION`, stamped into `checkpoints.db` and checked at startup, so an incompatible
  checkpoint refuses to start rather than crashing on resume.
- A `network` pytest marker for tests that reach GitHub, excluded from the default run alongside
  `llm`.

### Changed

- **Policy retrieval is now a deterministic `change_type` → file lookup** driven by
  `data/policies/index.yaml`, with whole-token keyword overrides. It is validated at startup — an
  unmapped change type, a missing file, or a heading without a policy id fails the process, not a
  review. Retrieval now returns the *complete* set of headings for the relevant files rather than
  a top-k sample, so a specialist can no longer miss a policy because it ranked seventh.
- Diff truncation for live PRs is per-file aware: whole hunks reach the model, and files dropped
  by the budget are named in the trailing marker.
- The event-loop subprocess preflight moved to `gate.runtime` and is now shared by the Agent SDK,
  the workspace clone, and semgrep — all three fail identically on a `SelectorEventLoop`.
- The `pr_id` path-safety check moved onto the fixture adapter, where it belongs: a live `pr_id`
  legitimately contains `/` and `#`, and the sandbox root is no longer derived from it.
- `GET /api/reviews/{thread_id}` returns `{...ReviewState, langfuse_ui_base}` rather than a bare
  snapshot; list items gain `source`.

### Removed

- **Chroma, the embedding model, and the ingestion step.** `langchain-chroma`, `chromadb`,
  `langchain-huggingface` and `sentence-transformers` are gone, along with
  `uv run python -m gate.intake.ingest`, `GATE_CHROMA_DIR`, `GATE_EMBED_MODEL` and
  `POLICY_COLLECTION`. Embedding search does not earn its place at a three-document corpus keyed
  by a closed enum; the threshold for revisiting it is documented rather than left implicit.
- `PolicyChunk.distance` — there is no similarity score without a similarity search. This is the
  one narrowing in the family and the reason `GATE_STATE_VERSION` exists.

### Security

- Upgraded two locked runtime dependencies off known vulnerabilities: `cryptography`
  49.0.0 -> 50.0.0 (PYSEC-2026-3552) and `langgraph-checkpoint-sqlite` 3.1.0 -> 3.1.1
  (PYSEC-2026-3636). Both fit the existing version constraints; only `uv.lock` moved.
- The release audit's ignore list now names three advisories against `mcp` 1.23.3 —
  PYSEC-2026-3481, -3482 and -3483 — in place of the `chromadb` entry, which went stale when
  Chroma was removed. All three are in the package's *server* half; this project imports
  `ClientSession` and `stdio_client` only and never runs an MCP server. The version is also not a
  free choice: `semgrep==1.172.0`, the pinned static-analysis extra and the current latest
  release, pins `mcp==1.23.3` exactly.

## [0.1.2] - 2026-08-06

Per-tier reasoning effort, a move to Sonnet 5, and the fixes that fell out of running the
pipeline for real — including two that silently disabled the investigator.

### Added

- `GATE_MODEL_CLASSIFIER_EFFORT`, `GATE_MODEL_SPECIALIST_EFFORT`, and
  `GATE_MODEL_INVESTIGATOR_EFFORT` — reasoning effort (`low`, `medium`, `high`, `xhigh`, `max`,
  default `high`) paired with each tier's model. Withheld from any model id containing `haiku`,
  which has no effort control and rejects the parameter. The investigator's effort applies to
  both its Agent SDK tool loop and its final summarizing turn.
- A glossary in the user and operator guide, and a `Documentation` section in the README linking
  to it.

### Changed

- Default specialist and investigator model is now `claude-sonnet-5` (was `claude-sonnet-4-6`).
- The effort-carrying tiers no longer pin `temperature=0`: an effort setting turns on adaptive
  thinking, which the API pairs with its own default temperature. Haiku tiers are unchanged.
- Guide pages renamed to per-folder `HT-01`…`HT-04` and `OP-01`…`OP-05` prefixes, with the root
  pages unnumbered and `index.md` as the entry point.

### Fixed

- `Deserializing unregistered type …` warnings on every checkpoint read. Each checkpointer now
  gets a serializer constructed with the project's state types (`use_state_allowlist`, in the new
  `graph/checkpoint.py`). LangGraph warns on unregistered types today and says it will refuse
  them in a future version, which would return bare dicts where models are expected.
- Every investigation failing under the documented start command with
  `Failed to start Claude Code:` and no further detail. `uvicorn --reload` forces a
  `SelectorEventLoop`, which cannot spawn the subprocess the Agent SDK needs and raises an
  empty `NotImplementedError`. `--reload` is gone from every documented command, and the
  investigator now preflights the loop and reports the cause instead of the empty message.
- The investigator running unauthenticated. The Claude Code CLI that the Agent SDK spawns
  inherits `os.environ`, but `pydantic-settings` reads `ANTHROPIC_API_KEY` from `.env` without
  ever setting it there, so the sub-agent — alone among the components — received no credentials.
  It is now passed explicitly via `ClaudeAgentOptions.env`.
- The first review after a server start failing with `Review not found.` in the UI. A review is
  registered before its background run writes a checkpoint, and the page polls immediately; the
  404 stopped polling and stranded it. A known thread now reads as `running` until its first
  checkpoint exists.
- Specialists failing with a `ValidationError` once an effort was set. An effort turns on
  adaptive thinking, and the API rejects a forced tool call while thinking is on, so
  `with_structured_output`'s default `function_calling` method quietly lost its guarantee and the
  model could answer in prose. Effort-carrying tiers now use Claude's dedicated structured output
  (`json_schema`); tiers without an effort keep `function_calling`.
- Documented `POLICY_COLLECTION`, which was absent from both the configuration reference and
  `.env.example`.
- The incident runbook now lists all five fixture-rejection messages, including
  `invalid sample PR id`, and corrects two descriptions: the id pattern permits hyphens, and an
  invalid `meta.json` is a JSON parse failure only.
- Added the `Review not found.`, `Connection issue: …`, and invalid-id banners to the reviewer
  troubleshooting table.

## [0.1.1] - 2026-07-27

Packaging, licensing, and release automation only — no functional change to the pipeline.

**Not published to PyPI.** The wheel is complete and verified to run standalone, but publication is
deliberately on hold: the project reads pull requests from local fixtures, and a package on PyPI
implies it is worth installing for real work. Live GitHub intake is the gate for publishing; until
then, install from a checkout or a locally built wheel. The release workflow ships disabled — it is
committed as `publish.yml.disabled`, which GitHub does not register, so no trigger in it can fire.

### Added

- Publishing metadata in `pyproject.toml` — Apache-2.0 license and `LICENSE` file, authors,
  trove classifiers, and project URLs.
- The policy corpus, PR fixtures, and UI are bundled into the wheel, so an installed package can
  ingest, serve the UI, and run the demo fixtures without a repository checkout.
- GitHub Actions: `ci.yml` (ruff, `mypy --strict`, and the coverage-gated suite on 3.12 and 3.13)
  and a publish workflow — build, `pip-audit` of the locked runtime dependencies, then Trusted
  Publishing to TestPyPI on `workflow_dispatch` and to PyPI on a published release, guarded by a
  check that the release tag matches the built distribution. It is committed disabled (see above)
  and has never run.
- `pre-commit` config running `ruff check --fix` and `ruff format` on commit
  (`uv run pre-commit install` once per clone).
- `gate.__version__`, read from the installed distribution metadata so the version string is
  never duplicated in source.
- `GATE_RISK_HIGH_BOUND` in `.env.example`; it was documented and honoured but absent from the
  template.

### Security

- `pr_id` arrives from the request body and was joined onto the fixture directory unvalidated —
  and the same value becomes the investigator sub-agent's sandbox root. A `..` or absolute
  segment could therefore relocate the sandbox itself, outside the path checks
  `resolve_in_sandbox` applies *within* it. `load_pr` now rejects any id that is not a plain
  directory name (`FixtureError` -> 422).

### Fixed

- `pytest -m llm` silently selected zero tests: the system evals are named `eval_*.py`, which
  pytest's default `python_files` never matches. All four eval modules were uncollectable despite
  being documented in the README, `CLAUDE.md`, and the operator guide.
- The UI's "Reviews", "← Reviews", and per-review links were `<a>` elements without `href`, so
  they were unreachable by keyboard and took no focus. They are now `<button>`s.
- The install guide told a `pip install` reader to skip to the step that copies `.env.example` —
  a file no wheel ships. It now points them at setting the key in the environment.

- `gate.paths` resolved its asset directories two levels above the package, which only exists in a
  source checkout — an installed package could not find `data/` or `static/`. It now prefers the
  bundled copy and falls back to the repo root. `REPO_ROOT` is renamed `ASSET_ROOT` accordingly.
- Documented install command is `uv sync --extra dev`; plain `uv sync` does not install the dev
  extra, so the documented test commands failed on a fresh checkout.
- Reformatted the eight source and test files that had drifted from `ruff format`, so the new
  pre-commit hook and the CI format check start from a clean tree.

## [0.1.0] - 2026-07-25

Initial release of the PR Compliance Gate MVP.

### Added

- LangGraph state machine (`src/gate/graph/`) that reviews a pull request against policy:
  `intake -> classify -> retrieve_policy -> risk_score -> specialists -> reconcile -> verdict`,
  checkpointed to SQLite after every node so a review survives a process restart.
- Two cost gates: sub-threshold changes are auto-approved without any Sonnet spend, and the
  Agent SDK investigator runs only when a specialist asks for more context and loop budget remains.
- Parallel security and licensing specialists (Sonnet) reconciled by a pure-Python rule ladder —
  routing decisions are code, not an LLM call.
- Autonomous investigator sub-agent (`agents/investigator.py`) with three read-only sandboxed tools
  (`read_file`, `grep_repo`, `list_dir`) over the PR's repo snapshot, hard turn/tool/byte budgets,
  and harness-owned metrics. It never raises into the graph: exhaustion returns an inconclusive
  report so failure lands on a human, not a stack trace.
- Human-in-the-loop approval gate via LangGraph `interrupt`, resumable through
  `POST /api/reviews/{thread_id}/decision`.
- FastAPI application (`src/gate/api/`) serving the REST surface and a same-origin single-page UI,
  running graph invocations as tracked background tasks.
- Policy corpus ingestion and retrieval over Chroma (`uv run python -m gate.intake.ingest`).
- Local PR fixtures under `data/sample_prs/` exercising the full demo arc, with loud `FixtureError`
  handling for malformed input.
- Documentation: `README.md`, `ARCHITECTURE.md` with diagrams and a decision log, a numbered user
  and operator guide, a narrated CLI demo, and `CLAUDE.md` for future agent sessions.

### Testing

- Suite split by scope — `tests/unit/`, `tests/integration/`, and `llm`-marked `tests/system/`
  evals — at 100% statement and branch coverage of `src/gate`, enforced by `fail_under = 100`.
  The default `pytest` run makes no API calls.
- `mypy --strict` and `ruff` clean.

[0.2.0]: https://github.com/cheneeheng/pr-compliance-gate/releases/tag/v0.2.0
[0.1.2]: https://github.com/cheneeheng/pr-compliance-gate/releases/tag/v0.1.2
[0.1.1]: https://github.com/cheneeheng/pr-compliance-gate/releases/tag/v0.1.1
[0.1.0]: https://github.com/cheneeheng/pr-compliance-gate/releases/tag/v0.1.0
