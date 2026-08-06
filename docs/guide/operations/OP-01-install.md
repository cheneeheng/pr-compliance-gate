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
- **Chroma vector store** (`.chroma/` by default) — the embedded policy corpus, built from
  `data/policies/*.md`. Embeddings are computed locally, so retrieval costs no API spend.
- **Anthropic API** — the only external dependency, called for classification, risk scoring, the
  specialists, and the investigator sub-agent.
- **Fixture directory** (`data/sample_prs/`) — the pull requests under review. There is no live
  GitHub intake.

## Prerequisites

- Python 3.12 or newer.
- [`uv`](https://docs.astral.sh/uv/) on `PATH` (for the from-source install below).
- An Anthropic API key with access to the configured models.
- Outbound HTTPS to the Anthropic API.
- Roughly 500 MB free disk: the local embedding model is downloaded on first ingest.

The steps below install from a checkout. The project is **not published to PyPI** — publication is
on hold until the pipeline reads live GitHub pull requests instead of local fixtures — so there is
no `pip install pr-compliance-gate` to run. You can still install a wheel you build yourself
(`uv build`, then `pip install dist/pr_compliance_gate-*.whl`); the corpus, fixtures, and UI are
bundled in it. Such an install ships no `.env.example`, so skip steps 1–2 and set the key directly
in the environment (`export ANTHROPIC_API_KEY=sk-ant-...`, or write your own `.env` in the working
directory), then follow steps 4–5 with every `uv run` prefix dropped.

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

4. Ingest the policy corpus into Chroma. This is a one-time step, repeated only when
   `data/policies/*.md` changes:

   ```bash
   uv run python -m gate.intake.ingest
   ```

   Expected output:

   ```
   ingested 7 policy chunks into .chroma
   ```

   Seven is the count for the corpus shipped in `data/policies/` (three files, seven `##`
   policy headings). Any count above zero is fine. A count of `0` means no
   policy heading matched the expected `SEC-01`-style id format — see
   [`OP-05-incident-runbook.md`](OP-05-incident-runbook.md).

5. Start the server:

   ```bash
   uv run uvicorn gate.api.app:app
   ```

   > **Warning:** do not add `--reload`. It makes uvicorn pick an event loop that cannot spawn a
   > subprocess on Windows, and the investigator drives the Claude Code CLI as a subprocess — so
   > every investigation would come back inconclusive. Nothing else in the pipeline is affected,
   > which is what makes it confusing to diagnose.

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

4. Confirm end-to-end behaviour with the cheapest fixture: start a review of `001_docs_typo` in
   the UI. It should reach `done` with verdict `auto_approved`.

**Verify:** step 4 produced an `auto_approved` verdict, and `checkpoints.db` now exists in the
working directory.

**If it fails:** see [`OP-05-incident-runbook.md`](OP-05-incident-runbook.md). The most common
first-run failures are an unset `ANTHROPIC_API_KEY` and a model id that does not exist on the
account.

## Next

- [`OP-02-configure.md`](OP-02-configure.md) — tune the gates and budgets.
- [`OP-03-routine-operations.md`](OP-03-routine-operations.md) — day-to-day tasks.
