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
| `GATE_CHROMA_DIR` | `.chroma` | Chroma persistence directory for the policy corpus. Safe to delete — re-create it by re-running ingestion. |
| `POLICY_COLLECTION` | `policies` | Name of the Chroma collection holding the policy chunks. Note it carries no `GATE_` prefix. Changing it points the app at a different collection inside the same directory — the old one is left behind, and retrieval returns nothing until you re-run ingestion under the new name. |

Both paths are resolved relative to the process working directory, so start the server from the
same directory every time or set absolute paths.

## Models

| Var | Default | Purpose |
|---|---|---|
| `GATE_MODEL_CLASSIFIER` | `claude-haiku-4-5` | The cheap tier: classification and risk scoring. Every review pays for this. |
| `GATE_MODEL_SPECIALIST` | `claude-sonnet-4-6` | The security and license specialists. Only reviews above the risk threshold pay for this. |
| `GATE_MODEL_INVESTIGATOR` | `claude-sonnet-4-6` | The autonomous sub-agent. Only investigations pay for this. |
| `GATE_EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Local embedding model for policy retrieval. Runs on your machine; no API spend. Changing it requires re-running ingestion. |

> **Warning:** the model ids are defaults, not guarantees. Verify each exists on your account
> before a real run; an invalid id fails at call time, mid-review.

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
   uv run uvicorn gate.api.app:app --reload
   ```

4. Start a review and confirm the new behaviour — for example, after raising
   `GATE_RISK_THRESHOLD`, a previously escalating fixture should now come back `auto_approved`.

**Verify:** the behaviour changed as intended. There is no endpoint that echoes effective
configuration; behaviour is the check.

**If it fails:** confirm you edited the `.env` in the directory the server was started from, and
that the variable name matches the table exactly.
