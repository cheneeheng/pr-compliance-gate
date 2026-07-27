# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-07-27

First release published to PyPI: `pip install pr-compliance-gate`. No functional change to the
pipeline — packaging, licensing, and release automation only.

### Added

- Publishing metadata in `pyproject.toml` — Apache-2.0 license and `LICENSE` file, authors,
  trove classifiers, and project URLs.
- The policy corpus, PR fixtures, and UI are bundled into the wheel, so an installed package can
  ingest, serve the UI, and run the demo fixtures without a repository checkout.
- GitHub Actions: `ci.yml` (ruff, `mypy --strict`, and the coverage-gated suite on 3.12 and 3.13)
  and `publish.yml` — build, `pip-audit` of the locked runtime dependencies, then Trusted
  Publishing to TestPyPI on `workflow_dispatch` and to PyPI on a published release, guarded by a
  check that the release tag matches the built distribution.
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
