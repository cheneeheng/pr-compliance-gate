# Reference

[← Guide index](index.md)

## HTTP API

Base URL: `http://127.0.0.1:8000`. No authentication, no CORS, same-origin UI.

| Method | Path | Purpose | Success | Errors |
|---|---|---|---|---|
| `GET` | `/` | Serve the UI. | `200` HTML | — |
| `GET` | `/api/prs` | List available sample PRs. Malformed fixtures are omitted. | `200` array of `{pr_id, title, files_changed_count}` | — |
| `POST` | `/api/reviews` | Start a review. Body: exactly one of `{"pr_id": "003_auth_change"}` or `{"pr_url": "https://github.com/owner/repo/pull/123"}`. | `200 {"thread_id": "…"}` | `422` malformed/unknown fixture, bad URL, or both/neither field; `403` token lacks scope; `404` repo or PR not found |
| `GET` | `/api/reviews` | List reviews known to this process. | `200` array of `{thread_id, pr_id, source, status, risk_level}` | — |
| `GET` | `/api/reviews/{thread_id}` | Full review state plus `status` and `langfuse_ui_base`. | `200` object | `404` unknown thread |
| `GET` | `/api/reviews/{thread_id}/pending` | The packet awaiting a human decision. | `200` packet, or `204` if nothing is pending | `404` unknown thread |
| `POST` | `/api/reviews/{thread_id}/decision` | Submit a decision. Body: `{"action": "approve", "note": ""}`. | `200 {"status": "resumed"}` | `404` unknown thread; `409` no pending decision |

Error responses are `{"error": "<message>"}`. Request bodies reject unknown fields.

Every request accepts and echoes an `X-Correlation-ID` header; one is generated if absent.

Example:

```bash
# a bundled fixture
curl -X POST http://127.0.0.1:8000/api/reviews \
  -H 'content-type: application/json' \
  -d '{"pr_id":"001_docs_typo"}'

# a live GitHub pull request
curl -X POST http://127.0.0.1:8000/api/reviews \
  -H 'content-type: application/json' \
  -d '{"pr_url":"https://github.com/owner/repo/pull/123"}'
```

The URL is validated and the repository is checked for access **synchronously**, so a bad URL or a
token without scope is an HTTP error and no review is created. Fetching the diff and cloning the
repository happen afterwards in the background; failures there become an escalation on a review
that already exists, not an HTTP error.

The detail response is the full review state plus two added fields: `status` (derived) and
`langfuse_ui_base` (server config, empty when tracing is disabled). Review state itself is
documented below.

## Review state

One `ReviewState` object per review, checkpointed to SQLite after every pipeline step. It is the
body of `GET /api/reviews/{thread_id}`, and it is what a checkpoint holds.

| Field | Type | Written by | Notes |
|---|---|---|---|
| `pr_id` | string | the source adapter | A fixture directory name, or `owner/repo#number` for a live pull request. |
| `thread_id` | string | the source adapter | The review id used in every `/api/reviews/{thread_id}` URL. |
| `trace_id` | string or `null` | the source adapter | 32 hex characters. `null` when tracing is disabled. |
| `source` | `fixture` or `github` | the source adapter | |
| `pr_ref` | `PRRef` or `null` | the source adapter | Live pull requests only. |
| `workspace_path` | string or `null` | the source adapter | The investigator's sandbox root. `null` when the load failed before materializing one. |
| `diff` | string | the source adapter | The unified diff. Empty when the load failed. |
| `files_changed` | array of string | the source adapter | Parsed from the diff's `diff --git` headers. |
| `policy_context` | array of `PolicyChunk` | `retrieve_policy` | Every section of every mapped policy file. |
| `classification` | `Classification` or `null` | `classify` | |
| `risk` | `RiskAssessment` or `null` | `risk_score` | |
| `specialist_findings` | array of `Finding` | both specialists | Merged per specialist — see *Channels that do not overwrite*. |
| `investigation` | `InvestigationReport` or `null` | `investigate` | The most recent round only. |
| `loop_count` | integer | `investigate` | Investigation rounds used, against `GATE_MAX_CYCLES`. Only `investigate` increments it, so resuming from a checkpoint cannot double-count. |
| `escalation` | `EscalationRecord` or `null` | the source adapter, either specialist, or `reconcile` | First write wins — see *Channels that do not overwrite*. |
| `human_decision` | `HumanDecision` or `null` | `human_gate`, on resume | |
| `verdict` | `Verdict` or `null` | `verdict` | Populated means the review is `done`. |
| `route` | string or `null` | `reconcile` | Internal: the routing decision the next edge reads. |

The source adapter writes its fields in the background task, **before** the first step runs. So a
review whose load failed still carries a `pr_id`, a `source` and an `escalation` — and nothing
that a model would have produced.

### Nested objects

| Object | Fields |
|---|---|
| `PRRef` | `owner`, `repo`, `number`, `head_sha`, `base_sha`, `html_url`, `title` |
| `PolicyChunk` | `text`, `policy_id`, `source_file`. No similarity score: retrieval does not rank. |
| `Classification` | `change_type`, `summary`, `touched_areas` (array of string) |
| `RiskAssessment` | `score` (0–100), `level`, `reasons` (array of string), `model_used` |
| `Finding` | `specialist`, `severity`, `policy_id`, `description`, `confidence` (0.0–1.0), `needs_more_context` |
| `InvestigationReport` | `summary`, `findings` (array of string), `files_examined`, `tool_calls_count`, `cost_estimate_usd`, `static_analysis` (array of `StaticFinding`), `semgrep_runs` |
| `StaticFinding` | `rule_id`, `severity`, `path`, `line`, `message` |
| `EscalationRecord` | `reason`, `detail` |
| `HumanDecision` | `action`, `note`, `decided_at` |
| `Verdict` | `outcome`, `rationale` |

Every enumerated value above is listed under [Statuses and enumerations](#statuses-and-enumerations).

On `InvestigationReport`, the model authors `summary` and `findings` only. Every count —
`tool_calls_count`, `files_examined`, `cost_estimate_usd`, `static_analysis`, `semgrep_runs` — is
measured by the harness.

### Channels that do not overwrite

Two fields have more than one writer, so both need a merge rule:

- **`specialist_findings`.** A fresh batch from one specialist replaces that specialist's earlier
  findings and leaves the other specialist's untouched. Without this, a second investigation round
  would pile on top of the first, and a question already answered would still read as
  `needs_more_context` forever.
- **`escalation`.** The first write wins. Both specialists run in the same parallel step, so both
  can escalate at once.

Every other field is last-write-wins.

## Statuses and enumerations

| Set | Values |
|---|---|
| Review status | `running`, `awaiting_human`, `done` |
| Change type | `code`, `dependency`, `config`, `docs`, `data_handling` |
| Risk level | `low`, `medium`, `high` |
| Specialist | `security`, `license` |
| Finding severity | `info`, `warn`, `block` |
| Escalation reason | `timeout`, `disagreement`, `max_cycles`, `pr_too_large`, `source_unavailable` |
| Decision action | `approve`, `reject`, `request_info` |
| Verdict outcome | `approved`, `rejected`, `auto_approved` |
| Source | `fixture`, `github` |
| Static finding severity | `info`, `warn`, `block` — mapped from semgrep's `INFO`/`WARNING`/`ERROR` |

`pr_too_large` and `source_unavailable` are raised while the pull request is being fetched, before
any model runs. Every other escalation reason is raised mid-pipeline.

`status` is derived, not stored: a populated verdict means `done`, a pending interrupt means
`awaiting_human`, otherwise `running`.

## Routing rules

Two routing points, both plain application code with no model involved.

**At intake.** If loading the pull request already failed — `pr_too_large` or
`source_unavailable` — the review goes straight to the human gate, skipping classification, risk
scoring, and both specialists. A review whose outcome is already decided never pays for a model
call.

**At reconcile,** a first-match-wins ladder:

1. An escalation is already recorded (for example a specialist timeout) → human gate.
2. Any `block`-severity finding → human gate.
3. A finding needs more context **and** loop budget remains → investigate.
4. A finding needs more context and the budget is spent → escalate `max_cycles` → human gate.
5. Two specialists rated the same policy differently → escalate `disagreement` → human gate.
6. Otherwise → verdict.

The loop counter is incremented only by the investigation step, so resuming from a checkpoint
cannot double-count it.

## Investigator sandbox

The sub-agent has exactly four read-only tools — `read_file`, `grep_repo`, `list_dir`,
`run_semgrep` — rooted at the review's workspace: a fixture's `repo/` snapshot, or a live pull
request's shallow clone under `data/workspaces/<head_sha>/`. The root is injected by the source
adapter, never derived from `pr_id`. Relative traversal (`..`) and absolute paths are rejected.
There is no write, network, or shell access — `run_semgrep` uses a committed local ruleset, never
`--config auto`, so the scan stays offline and reproducible.

Turn, tool-call, per-file byte, and semgrep run/result budgets are enforced by the harness, and the
harness — not the model — reports the tool-call count, semgrep run count, cost estimate, static
findings, and list of files examined. A failed semgrep run still spends a run and a tool call, so a
broken scanner cannot be retried for free.

## Policy retrieval

`data/policies/index.yaml` maps each change type to the policy files that govern it, plus keyword
overrides matched against the classification's touched areas. Retrieval returns **every** `##`
section of every mapped file — there is no similarity search, no vector store, no embedding model,
and no ingestion step.

Keyword matching is whole-token and case-folded: an override on `auth` matches a touched area of
"auth module" but not "author metadata".

The index is validated at startup. The server refuses to start if a change type is unmapped, a
named file is missing, or a `##` heading does not begin with a policy id like `SEC-01`.

## Span vocabulary

Present only when `GATE_OTEL_ENABLED=true`. Use these to write filter queries in Langfuse; see
[`operations/OP-04-monitoring.md`](operations/OP-04-monitoring.md) for what to watch.

| Span | Emitted by |
|---|---|
| `gate.review.run` | One per graph run. The root; its duration is the review duration. |
| `gate.node.<name>` | One per pipeline step, using the node names from the routing rules above. |
| `gate.llm.<Schema>` | One per model call, named after the structured-output schema it requested. |
| `gate.investigator.run` | One per investigation. |
| `gate.investigator.turn` | One per turn of the sub-agent's loop. |
| `gate.investigator.tool.<tool>` | One per sandboxed tool call. |
| `gate.mcp.<tool>` | One per GitHub MCP tool call. |

| Attribute | On | Meaning |
|---|---|---|
| `gate.pr_id`, `gate.thread_id` | most spans | Review identity. `gate.thread_id` finds both legs of a paused review. |
| `gate.risk.level` | run span | `low`, `medium`, `high`. |
| `gate.route.taken` | run span | Where `reconcile` sent the review. |
| `gate.loop_count.final` | run span | Investigation rounds used. |
| `gate.escalation.reason` | run span | Why a human was needed. |
| `gate.cost.investigator_usd` | run span | Investigation spend. |
| `gate.specialists.timed_out` | run span | True when the escalation reason was `timeout`. |
| `gate.node.route` | node spans | The route a node wrote, when it wrote one. |
| `gate.node.interrupted` | `gate.node.human_gate` | The review paused for a human. Control flow, not an error. |
| `gen_ai.request.model` | LLM spans | Which model was called. |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | LLM spans | Exactly as the API reported them. |
| `gate.llm.thinking_tokens` | LLM spans | Kept out of `output_tokens` so both reconcile against a bill. |
| `gate.llm.cost_estimate_usd` | LLM spans | Per-call estimate from a static price table. |
| `gate.investigator.tool_calls`, `gate.investigator.files_examined`, `gate.investigator.semgrep_runs`, `gate.investigator.static_findings` | `gate.investigator.run` | Harness-measured investigation totals. |
| `tool.name`, `tool.target` | `gate.investigator.tool.*` | Which tool ran, against which sandboxed path. |
| `gate.semgrep.rule_hits` | `gate.investigator.tool.run_semgrep` | How many hits the scan returned. `0` on a failed run too — the reason reaches the investigation summary, not the span. |

A review that pauses at the human gate produces **two** traces: one up to the pause, one for the
resumed leg. The UI's **View trace** link points at the first. Search `gate.thread_id` for both.

The design reasoning behind all of this is in [`../tracing-design.md`](../tracing-design.md).

## File layout

| Path | Contents |
|---|---|
| `data/policies/*.md` | The policy corpus. Each `##` heading beginning with an id like `SEC-01` becomes one retrievable chunk. |
| `data/sample_prs/<pr_id>/meta.json` | `pr_id` and `title`. |
| `data/sample_prs/<pr_id>/diff.patch` | Unified diff; the changed-file list is parsed from its `diff --git` headers. |
| `data/sample_prs/<pr_id>/repo/` | The snapshot the investigator may read. Optional. |
| `checkpoints.db` | SQLite checkpoints — every review's persisted state. Path set by `GATE_DB_PATH`. |
| `data/policies/index.yaml` | Maps each change type to the policy files that govern it, plus keyword overrides. Validated at startup. |
| `data/workspaces/<head_sha>/` | Shallow clone of a live PR; the investigator's sandbox root. Derived; LRU-evicted. Path set by `GATE_WORKSPACE_DIR`. |
| `rules/*.yaml` | The committed, pinned semgrep ruleset. Bundled into the wheel. |
| `static/index.html` | The entire UI, served by the app. |
| `examples/demo.py` | The narrated three-beat demo. |

## Not in this build

Authentication and multi-user support, hosted deployment *of the application*, writing results
back to GitHub (the token is read-only by design), webhook or GitHub-App triggers, and pagination
on list endpoints are all out of scope.
