# OP-04 — Monitoring

[← Guide index](../index.md)

There is no metrics endpoint and no alerting in this build. Monitoring means three things: reading
structured logs on stdout, inspecting review state through the API, and — when tracing is enabled
— reading traces in Langfuse.

Traces are where per-step timing and per-call cost live. Logs are where failures announce
themselves. Use logs to find that something went wrong; use traces to find where the time and the
money went.

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

When tracing is enabled, every log line also carries `trace_id` and `span_id`:

```
event='verdict' pr_id='003_auth_change' correlation_id='9f2c…' trace_id='4bf92f…' span_id='00f067…'
```

That is the join between a log line and a Langfuse trace. Copy the `trace_id` out of a log line to
open the same review in the trace UI, or copy it out of the UI's **View trace** link to grep the
log.

## Traces

Skip this section if `GATE_OTEL_ENABLED=false`. Setup is in
[`OP-01-install.md`](OP-01-install.md); the settings are in
[`OP-02-configure.md`](OP-02-configure.md).

One review produces one trace named `gate.review.run`, containing one span per pipeline step:

```
gate.review.run                       ← the whole review; its duration IS the review duration
  ├─ gate.node.intake
  ├─ gate.node.classify
  │    └─ gate.llm.Classification     ← one model call, with tokens and cost
  ├─ gate.node.risk_score
  ├─ gate.node.security_specialist
  ├─ gate.node.license_specialist
  ├─ gate.node.reconcile
  ├─ gate.node.investigate
  │    └─ gate.investigator.run
  │         └─ gate.investigator.tool.run_semgrep
  └─ gate.node.verdict
```

The `POST /api/reviews` request is a **separate** trace linked to this one, because the review
outlives the HTTP call that started it. Do not look for the review under the request.

### Attributes worth filtering on

| Attribute | On | Use |
|---|---|---|
| `gate.pr_id` | every span | Find one PR's runs. |
| `gate.thread_id` | every span | Find **both** legs of a review that paused at the human gate. |
| `gate.risk.level` | the run span | Confirm the risk gate: filter `low`, expect no specialist model calls. |
| `gate.route.taken` | the run span | Which way `reconcile` sent the review. |
| `gate.loop_count.final` | the run span | Investigation rounds used, against `GATE_MAX_CYCLES`. |
| `gate.escalation.reason` | the run span | Why a review needed a human. |
| `gate.cost.investigator_usd` | the run span | Investigation spend. |
| `gate.llm.cost_estimate_usd` | each `gate.llm.*` span | Per-call spend, every tier. |
| `gen_ai.usage.input_tokens` / `output_tokens` | each `gate.llm.*` span | Token counts as the API reported them. |
| `gate.llm.thinking_tokens` | each `gate.llm.*` span | Thinking tokens, kept separate so they reconcile against a bill. |
| `gate.node.interrupted` | `gate.node.human_gate` | The review paused for a human. Not an error. |

**Verify the cost gate is working:** filter traces on `gate.risk.level=low` and confirm none
contains a `gate.llm.*` span naming the specialist model. That is the design claim, checked rather
than assumed.

### Trace-level symptoms

| What you see | What it means |
|---|---|
| A trace that stops mid-graph | The run was killed, or the server was shut down during it. Not a routing bug. |
| Two traces for one review | Expected when the review paused at the human gate. Search `gate.thread_id` to get both. |
| Hundreds of one-span traces | UI polling. Each poll is its own request trace. Noise, not a fault. |
| Reviews complete but Langfuse is empty | An export problem, not a pipeline problem — see [`OP-05-incident-runbook.md`](OP-05-incident-runbook.md). |

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

There is no cost aggregation and no budget alerting. Three places show spend, in increasing order
of completeness:

- **`investigation_done` log lines** — `cost_usd` per investigation, measured by the harness (not
  self-reported by the model). Available whether or not tracing is on.
- **The Investigation panel** in the UI — the same estimate, plus the tool-call count, the semgrep
  run count, and the list of files read.
- **Traces** — `gate.llm.cost_estimate_usd` on **every** model call, classifier and specialists
  included, plus `gate.cost.investigator_usd` on the run span. This is the only place
  classifier and specialist spend is visible.

All figures are estimates from a static price table against reported token counts. Reconcile them
against your Anthropic console before trusting them for anything that matters.

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

---

[← OP-03 Routine operations](OP-03-routine-operations.md) · [Guide index](../index.md) · [OP-05 Incident runbook →](OP-05-incident-runbook.md)
