# 40 — Reference

## HTTP API

Base URL: `http://127.0.0.1:8000`. No authentication, no CORS, same-origin UI.

| Method | Path | Purpose | Success | Errors |
|---|---|---|---|---|
| `GET` | `/` | Serve the UI. | `200` HTML | — |
| `GET` | `/api/prs` | List available sample PRs. Malformed fixtures are omitted. | `200` array of `{pr_id, title, files_changed_count}` | — |
| `POST` | `/api/reviews` | Start a review. Body: `{"pr_id": "003_auth_change"}`. | `200 {"thread_id": "…"}` | `422` malformed/unknown fixture |
| `GET` | `/api/reviews` | List reviews known to this process. | `200` array of `{thread_id, pr_id, status, risk_level}` | — |
| `GET` | `/api/reviews/{thread_id}` | Full review state plus `status`. | `200` object | `404` unknown thread |
| `GET` | `/api/reviews/{thread_id}/pending` | The packet awaiting a human decision. | `200` packet, or `204` if nothing is pending | `404` unknown thread |
| `POST` | `/api/reviews/{thread_id}/decision` | Submit a decision. Body: `{"action": "approve", "note": ""}`. | `200 {"status": "resumed"}` | `404` unknown thread; `409` no pending decision |

Error responses are `{"error": "<message>"}`. Request bodies reject unknown fields.

Every request accepts and echoes an `X-Correlation-ID` header; one is generated if absent.

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/reviews \
  -H 'content-type: application/json' \
  -d '{"pr_id":"001_docs_typo"}'
```

## Statuses and enumerations

| Set | Values |
|---|---|
| Review status | `running`, `awaiting_human`, `done` |
| Change type | `code`, `dependency`, `config`, `docs`, `data_handling` |
| Risk level | `low`, `medium`, `high` |
| Specialist | `security`, `license` |
| Finding severity | `info`, `warn`, `block` |
| Escalation reason | `timeout`, `disagreement`, `max_cycles` |
| Decision action | `approve`, `reject`, `request_info` |
| Verdict outcome | `approved`, `rejected`, `auto_approved` |

`status` is derived, not stored: a populated verdict means `done`, a pending interrupt means
`awaiting_human`, otherwise `running`.

## Routing rules

`reconcile` decides the next step with a first-match-wins ladder — pure Python, no model:

1. An escalation is already recorded (for example a specialist timeout) → human gate.
2. Any `block`-severity finding → human gate.
3. A finding needs more context **and** loop budget remains → investigate.
4. A finding needs more context and the budget is spent → escalate `max_cycles` → human gate.
5. Two specialists rated the same policy differently → escalate `disagreement` → human gate.
6. Otherwise → verdict.

The loop counter is incremented only by the investigation step, so resuming from a checkpoint
cannot double-count it.

## Investigator sandbox

The sub-agent has exactly three read-only tools — `read_file`, `grep_repo`, `list_dir` — rooted at
`data/sample_prs/<pr_id>/repo/`. Relative traversal (`..`) and absolute paths are rejected. There
is no write, network, or shell access. Turn, tool-call, and per-file byte budgets are enforced by
the harness, and the harness — not the model — reports the tool-call count, cost estimate, and
list of files examined.

## File layout

| Path | Contents |
|---|---|
| `data/policies/*.md` | The policy corpus. Each `##` heading beginning with an id like `SEC-01` becomes one retrievable chunk. |
| `data/sample_prs/<pr_id>/meta.json` | `pr_id` and `title`. |
| `data/sample_prs/<pr_id>/diff.patch` | Unified diff; the changed-file list is parsed from its `diff --git` headers. |
| `data/sample_prs/<pr_id>/repo/` | The snapshot the investigator may read. Optional. |
| `checkpoints.db` | SQLite checkpoints — every review's persisted state. Path set by `GATE_DB_PATH`. |
| `.chroma/` | The embedded policy corpus. Derived; rebuildable. Path set by `GATE_CHROMA_DIR`. |
| `static/index.html` | The entire UI, served by the app. |
| `examples/demo.py` | The narrated three-beat demo. |

## Not in this build

Live GitHub intake, authentication and multi-user support, hosted deployment, tracing dashboards,
pagination, and real static-analysis investigator tools are all out of scope.
