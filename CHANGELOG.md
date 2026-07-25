# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/cheneeheng/pr-compliance-gate/releases/tag/v0.1.0
