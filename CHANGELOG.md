# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[0.1.1]: https://github.com/cheneeheng/pr-compliance-gate/releases/tag/v0.1.1
[0.1.0]: https://github.com/cheneeheng/pr-compliance-gate/releases/tag/v0.1.0
