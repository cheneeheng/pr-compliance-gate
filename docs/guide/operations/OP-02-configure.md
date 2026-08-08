# OP-02 — Configuration

All configuration is environment-driven, read from `.env` in the working directory or from the
process environment. Settings are loaded **once per process** and cached — every change requires a
restart.

Unknown keys in `.env` are ignored, so a typo fails silently by falling back to the default.
Check a setting actually took effect by observing behaviour, not by trusting the file.

## Secrets

| Var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(empty)* | Required for any real run. Unset means every model call fails. Never commit it. |

## Storage

| Var | Default | Purpose |
|---|---|---|
| `GATE_DB_PATH` | `checkpoints.db` | SQLite checkpoint file. Deleting it loses every review's history and any review paused at the human gate. |
| `GATE_STATE_VERSION` | `2` | Schema generation of the checkpointed state, stamped into the database on first use. If it does not match, the app **refuses to start** rather than crashing on resume. There is no migration for local demo data: delete `checkpoints.db` and re-run. |
| `GATE_POLICY_INDEX` | *(unset)* | Overrides the policy index location. Unset, it resolves to the bundled/repo copy of `data/policies/index.yaml` — never a working-directory-relative path. |
| `GATE_WORKSPACE_DIR` | `data/workspaces` | Clone cache for live pull requests, keyed by `head_sha`. Safe to delete; it is re-cloned on demand. |
| `GATE_WORKSPACE_MAX_GB` | `5` | Cache budget. Least-recently-used workspaces are evicted at startup and after each clone. |

Writable paths are resolved relative to the process working directory, so start the server from
the same directory every time or set absolute paths. Shipped assets (the policy corpus, the
semgrep rules, the UI) are resolved from the package instead and never depend on the working
directory.

## Tracing

| Var | Default | Purpose |
|---|---|---|
| `GATE_OTEL_ENABLED` | `true` | Kill switch. `false` installs a no-op tracer: the app runs fully with no trace backend, and the UI omits the **View trace** link entirely rather than rendering a dead one. |
| `GATE_OTEL_SERVICE_NAME` | `pr-compliance-gate` | `service.name` on every span. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:3000/api/public/otel` | OTLP/HTTP target. **No `GATE_` prefix** — this is the OpenTelemetry SDK's own variable name, kept so anyone who has configured OTel before sets it without reading these docs. |
| `OTEL_EXPORTER_OTLP_HEADERS` | *(empty)* | `key=value,key2=value2` — carries the Langfuse basic-auth header. Also unprefixed. |
| `GATE_LANGFUSE_UI_BASE` | `http://localhost:3000` | Used *only* to build the UI's trace deep-link. |

> Getting the two unprefixed names wrong is silent: the exporter falls back to `localhost:4318`,
> which is usually right in local development and wrong everywhere else, so it shows up as traces
> vanishing rather than as an error.

## Live GitHub intake

| Var | Default | Purpose |
|---|---|---|
| `GATE_GITHUB_TOKEN` | *(empty)* | Read-only PAT: `contents:read` + `pull_requests:read`. No write scopes — the gate never comments, labels, or merges. Passed explicitly to the MCP subprocess, which inherits nothing from `.env`. |
| `GATE_GH_MCP_COMMAND` | `npx -y @modelcontextprotocol/server-github` | The GitHub MCP server, launched as a stdio subprocess. |
| `GATE_GH_MAX_FILES` | `300` | Above this, the review escalates with `pr_too_large` instead of reviewing part of the change. A compliance gate that silently misses files is worse than one that refuses. |

## Static analysis

| Var | Default | Purpose |
|---|---|---|
| `GATE_SEMGREP_TIMEOUT_S` | `60` | Per-scan wall clock. On expiry the tool reports `semgrep unavailable: timed out …`; the investigation continues. |
| `GATE_SEMGREP_MAX_RESULTS` | `50` | Hits beyond this are dropped with an explicit "N further results omitted" line. |
| `GATE_SEMGREP_MAX_RUNS` | `2` | Scans per investigation. Semgrep is deterministic, so re-running an unchanged tree is pure waste. Each run also costs one investigator tool call. |
| `GATE_SEMGREP_RULES_DIR` | *(unset)* | Overrides the bundled `rules/` directory. `--config auto` is never used: it fetches rules over the network at run time, which breaks the sandbox and makes results non-reproducible. |

## Models

Each tier is a pair: a model id, and the reasoning effort sent with it.

| Var | Default | Purpose |
|---|---|---|
| `GATE_MODEL_CLASSIFIER` | `claude-haiku-4-5` | The cheap tier: classification and risk scoring. Every review pays for this. |
| `GATE_MODEL_CLASSIFIER_EFFORT` | `high` | Effort for that tier. Inert while the tier is Haiku (see below). |
| `GATE_MODEL_SPECIALIST` | `claude-sonnet-5` | The security and license specialists. Only reviews above the risk threshold pay for this. |
| `GATE_MODEL_SPECIALIST_EFFORT` | `high` | Effort for that tier. |
| `GATE_MODEL_INVESTIGATOR` | `claude-sonnet-5` | The autonomous sub-agent. Only investigations pay for this. |
| `GATE_MODEL_INVESTIGATOR_EFFORT` | `high` | Effort for that tier, applied to both the sub-agent's tool loop and its final summarizing turn. |

> **Warning:** the model ids are defaults, not guarantees. Verify each exists on your account
> before a real run; an invalid id fails at call time, mid-review.

An effort is one of `low`, `medium`, `high`, `xhigh`, or `max` — how much reasoning the model
spends per call. Anything outside that set fails at startup rather than mid-review. Lowering the
specialist and investigator efforts is the cheapest way to cut spend above the risk gate; raising
them costs more on every call that gets past it.

Effort reaches only the tiers that accept it — Sonnet, Opus, and Fable. A model id containing
`haiku` is never sent one, because Haiku has no effort control and the API rejects the call. That
test is on the configured id, not on the tier, so a tier pointed at Haiku is covered wherever it
appears, and pointing one at Sonnet opts it in automatically. This is why the classifier's effort
is inert at its default: lower the model to Haiku and the setting simply does not apply.

One side effect worth knowing: an effort setting turns on adaptive thinking, and the API pairs
that with its own default temperature. The effort-carrying tiers therefore no longer run at
temperature 0, so repeated reviews of one fixture can word their findings differently. Haiku
tiers keep temperature 0. This affects wording, not the routing rules — `reconcile` is plain
code.

## The risk gate

| Var | Default | Valid range | Effect |
|---|---|---|---|
| `GATE_RISK_THRESHOLD` | `40` | 0–100, below the high bound | Scores **below** this end the review immediately at `auto_approved`. Raising it approves more automatically and spends less; lowering it sends more work to the specialists. Set to `0` to send everything to the specialists. |
| `GATE_RISK_HIGH_BOUND` | `75` | above the threshold, ≤ 100 | The `medium`/`high` boundary. Scores at or above it are labelled `high`. This affects the displayed level only — routing turns on the threshold. |

The level is derived in application code from the score, so retuning the gate never means
re-prompting a model.

## Orchestration budgets

| Var | Default | Effect |
|---|---|---|
| `GATE_MAX_CYCLES` | `2` | Total investigations per review. Automatic investigations and reviewer **Request more info** actions share this budget. When it is exhausted with open questions remaining, the review escalates with reason `max_cycles`. |
| `GATE_SPECIALIST_TIMEOUT_S` | `60` | Per-specialist wall-clock budget. On timeout the review escalates with reason `timeout` — it never hangs. |
| `GATE_MAX_DIFF_CHARS` | `20000` | Diff text is truncated to this length before being sent to a model. Raising it costs more tokens per call; lowering it can hide the relevant hunk from the reviewer agents. |

## Investigator sub-agent budgets

These are hard ceilings enforced by the harness, not requests to the model. Exhausting any of them
returns an inconclusive report rather than raising an error.

| Var | Default | Effect |
|---|---|---|
| `GATE_INVESTIGATOR_MAX_TURNS` | `8` | Maximum model turns in the tool loop. |
| `GATE_INVESTIGATOR_MAX_TOOL_CALLS` | `15` | Maximum read-only tool calls (`read_file`, `grep_repo`, `list_dir`) across the whole investigation. |
| `GATE_INVESTIGATOR_MAX_FILE_CHARS` | `15000` | Maximum characters returned from any single file read. |

The investigator can only read inside `data/sample_prs/<pr_id>/repo/`. Path traversal (`..`) and
absolute paths are rejected by the sandbox; it has no write, network, or shell access.

## Applying a change

1. Edit `.env`.
2. Stop the server (`Ctrl+C`).
3. Start it again:

   ```bash
   uv run uvicorn gate.api.app:app
   ```

4. Start a review and confirm the new behaviour — for example, after raising
   `GATE_RISK_THRESHOLD`, a previously escalating fixture should now come back `auto_approved`.

**Verify:** the behaviour changed as intended. There is no endpoint that echoes effective
configuration; behaviour is the check.

**If it fails:** confirm you edited the `.env` in the directory the server was started from, and
that the variable name matches the table exactly.
