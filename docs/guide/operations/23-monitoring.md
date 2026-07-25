# 23 — Monitoring

There is no metrics endpoint, dashboard, or alerting in this build. Monitoring means reading
structured logs on stdout and the review state exposed by the API.

## Where the logs go

structlog writes key-value lines to the server's stdout — the terminal running uvicorn. Capture
them by redirecting:

```bash
uv run uvicorn gate.api.app:app > gate.log 2>&1
```

Each line looks like:

```
timestamp='2026-07-24T10:15:03.221Z' level='info' event='verdict' pr_id='003_auth_change' outcome='approved' correlation_id='9f2c…'
```

Logging is configured at **INFO** level at startup. The pipeline's per-node events
(`classified`, `risk_scored`, `retrieved_policy`, `reconcile_route`, `specialist_done`,
`intake_loaded`) are emitted at DEBUG and are therefore **not visible by default** — there is no
environment variable for the log level in this build.

## Correlation IDs

Every HTTP request carries a correlation id: taken from an incoming `X-Correlation-ID` header, or
generated if absent. It is attached to every log line produced during that request and returned in
the `X-Correlation-ID` response header. Use it to tie a user-reported problem to its log lines:

```bash
curl -i -H 'X-Correlation-ID: debug-001' http://127.0.0.1:8000/api/prs
grep debug-001 gate.log
```

Background graph runs are spawned outside the request that created them, so their log lines carry
the `pr_id` rather than the originating request's correlation id. **Filter pipeline problems by
`pr_id`.**

## Events worth watching

| Event | Level | Meaning | Healthy? |
|---|---|---|---|
| `app_started` | info | Startup complete; the checkpoint path is in the `db` field. | Once per start. |
| `app_stopped` | info | Clean shutdown. | Only when you stopped it. |
| `verdict` | info | A review terminated, with `outcome`. | Expected; one per finished review. |
| `human_decided` | info | Someone submitted a decision, with `action`. | Expected. |
| `investigation_done` | info | An investigation finished; carries `tool_calls` and `cost_usd`. | Expected. Watch `cost_usd`. |
| `specialist_timeout` | info | A specialist blew its time budget; the review escalates. | Rare. Frequent occurrences mean the timeout is too tight or the API is slow. |
| `investigation_error` | info | The sub-agent's tool loop failed; an inconclusive report was returned. | Should be rare. |
| `investigation_summarize_error` | info | The sub-agent's final structured-output turn failed. | Should be rare. |
| `graph_run_error` | info | A background review run raised. **The review is stuck** — nothing retries it. | Always investigate. |
| `gate_error` | info | A domain error was returned to a client, with `status`. 422 is a bad fixture; 404 an unknown thread; 409 a duplicate decision. | 409s are benign (double-clicks). |

## Cost signals

There is no cost aggregation. Two places show spend:

- **`investigation_done` log lines** — `cost_usd` per investigation, measured by the harness (not
  self-reported by the model).
- **The Investigation panel** in the UI — the same estimate, plus the tool-call count and the list
  of files read.

Specialist and classifier spend is not estimated anywhere. Reconcile it against your Anthropic
console.

The cheapest health signal that the cost gates are working: the fraction of reviews ending
`auto_approved` without any Findings panel content. If that fraction collapses, someone lowered
`GATE_RISK_THRESHOLD` — every review is now paying for specialists.

## Manual state inspection

```bash
curl http://127.0.0.1:8000/api/reviews                 # every review this process knows about
curl http://127.0.0.1:8000/api/reviews/<thread_id>     # full state of one review
curl -i http://127.0.0.1:8000/api/reviews/<thread_id>/pending   # 204 = nothing awaiting a human
```

**Verify healthy:** every review is `done` or `awaiting_human`; nothing has sat on `running`
for longer than a few minutes.
