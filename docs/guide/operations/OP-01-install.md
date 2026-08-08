# OP-01 — Installation

Stand up the service on a single machine and verify it is healthy.

## System overview

One process serves everything:

- **FastAPI app** (`gate.api.app:app`) — serves the JSON API *and* the UI from the same origin.
  There is no CORS middleware and no authentication; both are deliberate for a single-user local
  demo. Do not expose this port to a network you do not control.
- **LangGraph state machine** — the review pipeline. Runs as a background task per review inside
  the same process.
- **SQLite checkpoint file** (`checkpoints.db` by default) — every review step is checkpointed
  here. This is what makes a paused review survive a restart.
- **Policy corpus** (`data/policies/`) — Markdown documents plus `index.yaml`, which maps each
  change type to the files that govern it. Retrieval is a deterministic file lookup: no vector
  store, no embedding model, no ingestion step. The corpus is validated at startup, so a broken
  one fails the process rather than a review.
- **Anthropic API** — called for classification, risk scoring, the specialists, and the
  investigator sub-agent.
- **Pull-request sources** — a fixture directory (`data/sample_prs/`) *and* live GitHub, reached
  by PR URL through the GitHub MCP server. Live PRs are shallow-cloned into a workspace cache
  (`data/workspaces/` by default), which becomes the investigator's sandbox root.
- **Langfuse** (optional) — a dev-time trace backend started from
  `docker-compose.langfuse.yml`. The app runs normally with it down; set `GATE_OTEL_ENABLED=false`
  to install a no-op tracer.
- **Semgrep** (optional) — the investigator's static-analysis tool, installed with
  `uv sync --extra semgrep`. Absent, investigations still run and report
  `semgrep unavailable: …`.

## Prerequisites

- Python 3.12 or newer.
- [`uv`](https://docs.astral.sh/uv/) on `PATH` (for the from-source install below).
- An Anthropic API key with access to the configured models.
- Outbound HTTPS to the Anthropic API.
- For live GitHub intake: `git` on `PATH`, a read-only GitHub PAT (`contents:read` +
  `pull_requests:read`), and whatever runtime `GATE_GH_MCP_COMMAND` needs (Node/`npx` by default).
- Disk for the clone cache: budget at least `GATE_WORKSPACE_MAX_GB` (5 GB default).
- Optional: Docker, to run the Langfuse trace backend.

The steps below install from a checkout. The project is **not yet published to PyPI** — the hold
that kept it unpublished (live GitHub intake) has been lifted, but publication remains a separate
release decision — so there is no `pip install pr-compliance-gate` to run. You can still install a wheel you build yourself
(`uv build`, then `pip install dist/pr_compliance_gate-*.whl`); the corpus, fixtures, and UI are
bundled in it. Such an install ships no `.env.example`, so skip steps 1–2 and set the key directly
in the environment (`export ANTHROPIC_API_KEY=sk-ant-...`, or write your own `.env` in the working
directory), then follow step 4 with every `uv run` prefix dropped.

## Steps

1. Install dependencies, including dev extras:

   ```bash
   uv sync --extra dev
   ```

   Plain `uv sync` installs runtime dependencies only; the `pytest`/`ruff`/`mypy` tooling lives in
   the `dev` extra.

2. Create the environment file from the template:

   ```bash
   cp .env.example .env
   ```

   On Windows PowerShell:

   ```powershell
   Copy-Item .env.example .env
   ```

3. Open `.env` and set your key:

   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```

   > **Warning:** `.env` holds a live credential. It must never be committed. Review every other
   > setting in [`OP-02-configure.md`](OP-02-configure.md) before a real run — in particular, confirm
   > the `GATE_MODEL_*` ids exist on the live API, since they are only defaults.

   For live GitHub intake, also set:

   ```
   GATE_GITHUB_TOKEN=ghp_...
   ```

   Use a **read-only** token (`contents:read`, `pull_requests:read`). The gate never comments,
   labels, or merges, so a write scope buys nothing and costs blast radius.

   > There is no ingestion step. Policy retrieval reads `data/policies/index.yaml` directly, and
   > the server validates it at startup — an unmapped change type, a missing file, or a heading
   > without a `SEC-01`-style id fails the process with a named error rather than producing
   > reviews that cite nothing. See [`OP-05-incident-runbook.md`](OP-05-incident-runbook.md).

4. Start the server:

   ```bash
   uv run uvicorn gate.api.app:app
   ```

   > **Warning:** do not add `--reload` or `--workers`. They make uvicorn pick an event loop that
   > cannot spawn a subprocess on Windows, and three things need one: the investigator (which
   > drives the Claude Code CLI), the workspace clone (`git`), and semgrep. All three degrade with
   > a message naming this cause; nothing else in the pipeline is affected, which is what makes it
   > confusing to diagnose.

5. Optional — start the trace backend and re-check the review detail page for a **View trace**
   link:

   ```bash
   docker compose -f docker-compose.langfuse.yml up -d
   ```

   Create a Langfuse project in its UI, then put its OTLP credentials in
   `OTEL_EXPORTER_OTLP_HEADERS` and restart the app. With `GATE_OTEL_ENABLED=false` the app runs
   normally and the link is omitted entirely rather than rendered dead.

## Post-install health check

1. Confirm the app started. The log line is:

   ```
   app_started db=checkpoints.db
   ```

2. Confirm the fixtures load:

   ```bash
   curl http://127.0.0.1:8000/api/prs
   ```

   Expected output — a non-empty JSON array:

   ```json
   [{"pr_id":"001_docs_typo","title":"Fix typo in README","files_changed_count":1}, ...]
   ```

3. Confirm the UI serves: open <http://127.0.0.1:8000> and check the dropdown is populated.
   The **GitHub PR URL** radio next to it enables the live-intake input.

4. Confirm end-to-end behaviour with the cheapest fixture: start a review of `001_docs_typo` in
   the UI. It should reach `done` with verdict `auto_approved`.

**Verify:** step 4 produced an `auto_approved` verdict, and `checkpoints.db` now exists in the
working directory.

5. Optional — confirm live intake: paste a public pull-request URL into the **GitHub PR URL**
   field. A malformed URL must come back as an immediate red banner (a `422`) with *no* review row
   created; a valid one appears in the list with a `github` badge while the clone runs.

**If it fails:** see [`OP-05-incident-runbook.md`](OP-05-incident-runbook.md). The most common
first-run failures are an unset `ANTHROPIC_API_KEY`, a model id that does not exist on the
account, and — for live intake — a `GATE_GITHUB_TOKEN` that is missing or lacks
`pull_requests:read`.

## Next

- [`OP-02-configure.md`](OP-02-configure.md) — tune the gates and budgets.
- [`OP-03-routine-operations.md`](OP-03-routine-operations.md) — day-to-day tasks.
