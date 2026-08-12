# Tracing — design note

Why the tracing subsystem looks the way it does, the three traps it was built around, and what
it still does not do. Written after implementing ITER_02_v2.

This is an engineering note, not part of the user/operator guide in [`guide/`](guide/index.md).
Operator-facing tracing config lives in
[`guide/operations/OP-02-configure.md`](guide/operations/OP-02-configure.md); the failure runbook
is in [`guide/operations/OP-05-incident-runbook.md`](guide/operations/OP-05-incident-runbook.md).

---

## 1. Telemetry vs. tracing

Observability conventionally has three signals. This project implements one and a half:

| Signal | What it is | Here |
|---|---|---|
| **Traces** | Causally-linked spans — what happened, in what order, nested | implemented |
| **Metrics** | Aggregated counters and gauges over time | **not** implemented |
| **Logs** | Discrete text events | pre-existing (structlog); now carries `trace_id` / `span_id` |

Hence `gate/telemetry/tracing.py`: `telemetry` is the umbrella package, `tracing` is the one
signal inside it. Metrics were skipped deliberately — at tens of runs a trace already carries the
cost and duration numbers, and a Prometheus endpoint would be a second system telling the same
story less precisely.

### Three facts about spans

Everything in §3 follows from these. They are worth stating explicitly because the third one is
counter-intuitive and is what makes trap 3.2 possible.

**1. A span is a timed box** — a name, a start, an end, plus attributes.

```
              |<--------- 2.1 seconds --------->|
              ┌─────────────────────────────────┐
              │  gate.node.classify             │
              └─────────────────────────────────┘
           t=0.2                              t=2.3
```

**2. A parent contains its children in time.** "Parent" is not decoration — it is the claim
*this happened during that*.

```
   ┌──────────────────────────────────────────────────┐
   │  PARENT                                          │
   │      ┌──────────┐      ┌──────────────┐          │
   │      │ child A  │      │   child B    │          │
   │      └──────────┘      └──────────────┘          │
   └──────────────────────────────────────────────────┘
   starts BEFORE                          ends AFTER
```

**3. A span with no parent starts a brand-new trace.**

```
   span WITH a parent      →  joins the parent's trace
   span WITHOUT a parent   →  becomes a ROOT  →  NEW trace, new trace_id
```

A lost parent therefore does **not** produce a misplaced span. It produces a span in a *different
trace*, which is why §3.2's failure is silent.

A **trace** is a tree of spans sharing a `trace_id`.

### Two things called "context"

Both types appear in `tracing.py`, from different packages, and they are unrelated. One is an
identity, the other is a container:

| | `SpanContext` (`opentelemetry.trace`) | `Context` (`opentelemetry.context`) |
|---|---|---|
| What it is | A span's **identity** | A **container** of ambient values |
| Holds | `trace_id`, `span_id`, flags, tracestate | "the current span", baggage |
| Serialisable | Yes — this *is* the `traceparent` header | No |
| Answers | "which span?" | "which span is current here?" |
| Taken by | `Link(...)` | `start_as_current_span(context=...)` |

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
             ^^ ^------- trace_id (32 hex) ------^ ^--span_id--^ ^flags
                └──────────── a serialised SpanContext ────────────┘
```

`trace.set_span_in_context(span)` is the bridge: span in, `Context` out. That is the line stored
in `_RUN_CONTEXTS` (§3.2). An *empty* `Context()` is what forces a root (§3.1) — a state no
`SpanContext` can express, because "no span at all" is not an identity.

The two accessors are named for their job rather than their type:

| Function | Returns | Read from | Used for |
|---|---|---|---|
| `current_request_context()` | `SpanContext` | the ambient contextvar, in the handler | building a **link** (§3.1) |
| `run_context(thread_id)` | `Context` | the `_RUN_CONTEXTS` dict, from any task | setting a **parent** (§3.2) |

A `SpanContext` is **valid** only when both ids are non-zero. `NoOpTracerProvider` hands out
all-zero ids, so `current_request_context()` and `trace_id_of()` return `None` under
`GATE_OTEL_ENABLED=false` without either function testing the setting (§5).

**Instrumentation, not infrastructure.** Spans are written in application code using real
semantic conventions and exported over OTLP/HTTP. Langfuse is addressed purely as an endpoint —
point the same exporter at Jaeger or Tempo and none of the application code changes. Standing up
a collector + trace store + query UI was considered and rejected: three moving parts against a
project whose constraint is a single-command local run, and raw OTel has no LLM semantics
(tokens, cost, prompt capture), which is exactly the schema work Langfuse already did.

---

## 2. The span tree

```
POST /api/reviews  ──link──►  gate.review.run          ← ROOT, opened in the background task
   (FastAPIInstrumentor,        ├─ gate.node.intake
    ends in ~ms)                ├─ gate.node.classify
                                │     └─ gate.llm.Classification
                                ├─ gate.node.retrieve_policy
                                ├─ gate.node.risk_score
                                │     └─ gate.llm.RiskAssessment
                                ├─ gate.node.security_specialist  ┐ parallel branches,
                                │     └─ gate.llm._FindingList    │ separate asyncio tasks
                                ├─ gate.node.license_specialist   ┘
                                ├─ gate.node.reconcile
                                ├─ gate.node.investigate
                                ├─ gate.investigator.run     ← PEER of the node, not its child
                                │     ├─ gate.investigator.turn (×N)      (why: §3.2)
                                │     ├─ gate.investigator.tool.run_semgrep
                                │     ├─ gate.investigator.tool.read_file
                                │     └─ gate.llm._InvestigationAnswer    (the final turn)
                                ├─ gate.node.human_gate      ← gate.node.interrupted = true
                                └─ gate.node.verdict
```

Pipeline: `TracerProvider` → `BatchSpanProcessor` → `OTLPSpanExporter` → self-hosted Langfuse.
Sampling is `ALWAYS_ON`; at this volume dropping traces buys nothing and makes the demo
non-reproducible.

### Where the code lives

| Concern | Location |
|---|---|
| Provider, kill switch, run span, outcome attributes | `src/gate/telemetry/tracing.py` |
| `init_tracing` / `tracer()` / `review_run_span` / `set_run_outcome_attributes` | same, lines 70 / 112 / 144 / 180 |
| `traced_node` decorator | `src/gate/graph/nodes/__init__.py:25` |
| Applied uniformly at wiring time | `src/gate/graph/build.py` (the `add_node` loop) |
| Run span opened in the background task | `src/gate/api/routes.py:79` (`_spawn`) |
| Lifespan init + drain-then-flush teardown | `src/gate/api/app.py:63`, `:73` |
| LLM spans + token capture | `src/gate/llm/models.py:108`, `:133` |
| Investigator run / turn / tool spans | `src/gate/agents/investigator.py:330`, `:403`, `:113` |
| Log correlation processor | `src/gate/logging.py:34` |
| Tests (in-memory exporter) | `tests/unit/test_tracing.py` |
| Dev-time backend | `docker-compose.langfuse.yml` |

---

## 3. Three traps, designed for rather than debugged

### 3.1 The graph run outlives its HTTP request

`POST /api/reviews` returns a `thread_id` as soon as the background task spawns. Fixture 003, real
numbers: the response goes out at **t = 0.012 s**; the run finishes at **t ≈ 95 s**.

```
 t=0.000   ┌── POST /api/reviews handler starts
 t=0.008   │   resolve() — one cheap metadata call
 t=0.011   │   create_task(run the whole review)  ────────┐
 t=0.012   └── RESPONSE SENT. Handler is finished.        │
                                                          │  the review runs on
 t=0.10                                                   ├─ intake
 t=2.3                                                    ├─ classify
 t=12.4                                                   ├─ investigate
 t=95.0                                                   └─ verdict
```

Had the run been a *child* of the request span, apply fact 2 and the claim is impossible:

```
  ┌────┐
  │POST│   0.000 ──┤ 0.012        ← this box is 12 ms wide
  └────┘
     ↓ claims to CONTAIN:
                                       ┌───────────────────────────────┐
                                       │  gate.node.investigate        │
                                       └───────────────────────────────┘
                                    12.4                            71.9
     ✗ the child STARTS 12.4 s AFTER the parent ENDED
```

The real harm is not the ugly waterfall — most backends still stitch by `parent_span_id`. It is
that **no span holds the review's duration**:

```
 TRACE VIEW                                                   duration
 ─────────────────────────────────────────────────────────────────────
 POST /api/reviews          [█]                                 12 ms   ← ROOT
   gate.node.intake            [█]                              40 ms
   gate.node.classify           [██]                             2.2 s
   gate.node.investigate            [██████████████████]        59.5 s
   gate.node.verdict                                  [█]      200 ms
 ─────────────────────────────────────────────────────────────────────
                             ↑ "how long does a review take?" reads the ROOT: 12 ms
```

That number is true of the HTTP call and useless about the work — and *plausible* for an endpoint,
so nothing looks broken. Under a buffering `BatchSpanProcessor` the 12 ms parent is also exported
at t≈5 s, long before the t=71.9 s child that names it.

**Fix.** The needed relationship is not containment:

```
 PARENT  =  "B happened INSIDE A"        LINK  =  "A is related to B"

   ┌── A ─────────────────┐               ┌── A ──┐      ┌── B ──────────────┐
   │      ┌── B ──┐       │               │       │─────►│                   │
   │      └───────┘       │               └───────┘      └───────────────────┘
   └──────────────────────┘                 12 ms              95 s
```

So `gate.review.run` is opened as a **root** inside the background task, with `context=Context()`
— an explicitly *empty* context, which forces a root even though a request span is ambient at that
moment. The tie back to the request is a **span link**, which expresses "related to" without
implying containment.

```python
# routes.py:94 — in the HANDLER, while the request span is still alive
link_to = current_request_context()          # ① must be read here, not in the task

# tracing.py:158 — inside review_run_span, in the BACKGROUND TASK
with tracer().start_as_current_span(
    "gate.review.run",
    context=Context(),        # ② empty context = "I refuse any parent. I am a root."
    links=links,              # ③ but here is what I am related to
)
```

Result — two traces, each honest about itself:

```
 TRACE A                                TRACE B
 ┌────────────────────────┐             ┌──────────────────────────────────────┐
 │ POST /api/reviews      │──link──►    │ gate.review.run            95 s      │
 │ 12 ms                  │             │   ├─ gate.node.intake                │
 └────────────────────────┘             │   ├─ gate.node.classify              │
  answers                               │   ├─ gate.node.investigate           │
  "how fast is the API?"                │   └─ gate.node.verdict               │
                                        └──────────────────────────────────────┘
                                         answers "how long is a review?"
```

### 3.2 OTel context does not cross task boundaries

When you open a span without naming a parent, the tracer reads "the current span" from a
`contextvars.ContextVar` — a sticky note every running task carries:

```
   ┌──── context ──────────────┐
   │  current span:            │      note says "gate.review.run" → that is the parent
   │     [ gate.review.run ]   │      note is BLANK  → NO parent → (fact 3) NEW TRACE
   └───────────────────────────┘
```

When a new `asyncio` task starts, the creator's context is **photocopied** into it:

```
    creator task                              new task
    ┌──── context ────┐    photocopy at       ┌──── context ────┐
    │ current: RUN    │ ───create_task()───►  │ current: RUN    │
    └─────────────────┘       moment          └─────────────────┘
```

Two properties of that copy, both verifiable in a five-line script: it is taken **at
`create_task()` time**, and writing on either copy never changes the other.

The problem is that **you never call `create_task` for a node — LangGraph's executor does**. The
photocopy your node receives is whatever was on the executor's note at whatever moment the
executor chose to schedule:

```
  YOUR CODE                    LANGGRAPH EXECUTOR              THE NODE'S TASK
  ─────────                    ──────────────────              ───────────────
                               ┌──── context ────┐
                               │ current: BLANK  │  ← its snapshot predates
                               └─────────────────┘     your run span
  open run span
  ┌──── context ────┐
  │ current: RUN    │
  └─────────────────┘
        │  (your box is NOT the one that gets copied)
        ✗
                               ...later, fans out
                               2 branches using
                               ITS OWN copy ──────────►  ┌──── context ────┐
                                                         │ current: BLANK  │
                                                         └─────────────────┘
                                                          traced_node reads BLANK
                                                          → NO PARENT → NEW TRACE
```

Instrument naively and one review of fixture 002 produces **three traces**:

```
trace 4bf92f…   gate.review.run → intake, classify, retrieve, risk, reconcile
trace 91c2ab…   gate.node.security_specialist → gate.llm._FindingList     (orphan root)
trace e77a04…   gate.node.license_specialist  → gate.llm._FindingList     (orphan root)
```

Every span exists, every timestamp is right, nothing logs an error. It looks like three things
were traced instead of one — and it quietly invalidates the project's headline claim, because the
Sonnet spans are in traces 2 and 3 while `gate.risk.level` is on trace 1:

```
 CLAIM:  "low-risk PRs never invoke a Sonnet specialist"
 QUERY:  filter traces where gate.risk.level = low, then count Sonnet spans

   gate.risk.level = low   ──────► trace 4bf92f      (attribute on the run span)
   Sonnet spans            ──────► traces 91c2ab, e77a04
   the filter selects ────────────► trace 4bf92f only

 ┌─ CASE A: the cost gate WORKS. Sonnet never called. ──────────────┐
 │   query → trace 4bf92f → 0 Sonnet spans → reported: PASS         │
 └──────────────────────────────────────────────────────────────────┘
 ┌─ CASE B: the cost gate is BROKEN. Sonnet wrongly called twice. ──┐
 │   those 2 spans live in 91c2ab / e77a04, which the filter        │
 │   never looks at                                                 │
 │   query → trace 4bf92f → 0 Sonnet spans → reported: PASS         │
 └──────────────────────────────────────────────────────────────────┘
                         SAME ANSWER BOTH TIMES
```

A check that returns PASS whether the thing works or not is not a check. The evidence becomes
unfalsifiable: it cannot distinguish a working cost gate from a broken one. Secondary damage:
`gate.cost.investigator_usd` is per-trace, so scattered spend cannot be summed.

**Fix, in two halves.** *Publish* — the run span's context is captured where the run span is
opened and stored per `thread_id`:

```python
_RUN_CONTEXTS[thread_id] = trace.set_span_in_context(span)
```

*Consume* — `traced_node` ignores its own note and passes the stored one explicitly:

```python
with tracer().start_as_current_span(
    f"gate.node.{fn.__name__}",
    context=run_context(state.thread_id),   # explicit, not ambient
    ...
)
```

```
   the node's task                      _RUN_CONTEXTS  (module-level dict)
   ┌──── context ────┐                  ┌──────────────────────┐
   │ current: BLANK  │   ✗ ignored      │ "thread-abc" → RUN   │  ✓ used
   └─────────────────┘                  └──────────────────────┘
                                                  │
                                                  ▼
                                        parent = gate.review.run
```

`state.thread_id` is the key because it is the one identifier that reaches every node through
checkpointed state.

> **The subtlety worth internalising.** Explicit `context=` is *usually* a repair, applied where a
> task boundary is crossed: node spans (LangGraph schedules them) and investigator turn and tool
> spans (the Agent SDK schedules those). LLM spans sit inside a node on the *same* task, within
> the node span's `with` block, so ambient context works there and no explicit parent is needed.
> If you add a span and wonder which to use, ask: does someone else's scheduler own the task this
> runs on?

#### The one case where `context=` is not a repair

`gate.investigator.run` (`agents/investigator.py:330`) passes `run_context(state.thread_id)` too —
but here **the ambient parent is intact**. `run_investigation` is called from the `investigate`
node, on that node's own task, inside the node span's `with` block, so ambient context would name
`gate.node.investigate`. Passing the run context deliberately overrides that:

```
   AMBIENT would give:                    EXPLICIT gives (current behaviour):

   gate.node.investigate    59.5 s        gate.node.investigate    59.5 s
     └─ gate.investigator.run  59.4 s     gate.investigator.run    59.4 s
          ├─ turn ×N                        ├─ turn ×N
          └─ tool.read_file                 └─ tool.read_file
   two spans, same duration, nested       one pipeline step, one investigation
```

`investigate` is a six-line pass-through (`graph/nodes/investigate.py`) whose entire duration *is*
this call, so nesting buys a second box the same width as the first. Flat, the investigation reads
as a peer of the pipeline steps.

Verified by exporting the tree in memory, not by reading the code — this note replaces an earlier
version of the span tree above that drew `gate.investigator.run` as a child of the node.

Pinned by `test_tracing.py::test_the_investigator_span_is_a_peer_of_its_node_not_a_child`, which
was confirmed to fail when the `context=` argument is removed. Before that test the shape was
unasserted, and deleting the argument as a "cleanup" would have re-shaped the trace silently.

The tempting shortcut — capture the context inside the `intake` node — is wrong in a way that
looks fine at a glance: `intake` is itself a child of the run span, so its note says `intake`, and
every later node parents to it.

```
   YOU GET A CHAIN:                       YOU WANTED A FAN:

   gate.review.run                        gate.review.run
    └─ gate.node.intake                    ├─ gate.node.intake
        └─ gate.node.classify              ├─ gate.node.classify
            └─ gate.node.risk_score        ├─ gate.node.risk_score
                └─ security_specialist     ├─ security_specialist
                    └─ reconcile           ├─ license_specialist
                                           └─ reconcile
```

One trace, all spans present, no errors — and it claims classify happened *inside* intake, which
is false.

`traced_node` is applied at **wiring time** in `build.py`, not as a per-node `@` decorator, so no
node can be silently untraced by someone forgetting to add one.

### 3.3 Buffered spans die on exit

`BatchSpanProcessor` batches for throughput (default: every 5 s, or at 512 spans). An *un-ended*
span is never exported at all.

Ctrl-C the server three seconds into fixture 003's investigation. Langfuse shows:

```
gate.review.run
  ├─ gate.node.intake      ✓
  ├─ gate.node.classify    ✓
  ├─ gate.node.reconcile   ✓
  └─ (nothing)                    ← gate.node.investigate never appears
```

You would read that as "reconcile routed to verdict" and go debug the rule ladder. The run was
killed.

**Fix, and the ordering is load-bearing:**

```python
finally:
    await _drain(app.state.bg_tasks)   # cancel + await in-flight runs FIRST
    shutdown_tracing()                 # then force-flush and tear down
```

Reversed, the still-running investigation calls `span.end()` three seconds later against a
shut-down processor, which logs *"Already shutdown, dropping span"* and discards it. Identical
symptom — missing `investigate` — but now caused by our own teardown, and only on a *graceful*
shutdown, which is exactly when you would assume the data is trustworthy.

---

## 4. Attributes, and why those specifically

### On the run span (set after `ainvoke` returns)

```
gate.risk.level          gate.route.taken           gate.loop_count.final
gate.escalation.reason   gate.cost.investigator_usd gate.specialists.timed_out
```

These exist to convert claims into evidence. Filter `gate.risk.level=low` and confirm zero
`gate.llm.*` spans naming Sonnet — that is cost gate #1, demonstrated rather than asserted. Same
for gate #2: `gate.loop_count.final` against `GATE_MAX_CYCLES` shows the budget draining.

`set_run_outcome_attributes` reads checkpointed sub-values that may arrive as **either** dicts or
Pydantic instances, hence the `_field` helper.

### On LLM spans

OTel GenAI semantic conventions (`gen_ai.system`, `gen_ai.request.model`,
`gen_ai.usage.input_tokens` / `output_tokens`) plus Langfuse's `input.value` / `output.value` so
prompts render as text rather than a bare timing bar. Two non-standard attributes are deliberate:

- **`gate.llm.effort` + `gate.llm.structured_output_method`** — this pair is what silently broke
  the v1 specialists. An effort setting turns on adaptive thinking; the API then refuses to
  *force* a tool call; the default `function_calling` method quietly loses its guarantee and the
  model is free to answer in prose, which surfaces as a schema validation error. Recorded on the
  span, the next occurrence is a filter query rather than a debugging session.
- **`gate.llm.thinking_tokens`** kept separate from `gen_ai.usage.output_tokens`, which stays
  exactly as the API reports it. An effort setting makes thinking a real cost line; folding it
  into a conventional attribute would make the two unreconcilable against a bill.

Token counts come from a `UsageMetadataCallbackHandler`, not from the result, because
`with_structured_output` returns the parsed Pydantic instance rather than the raw `AIMessage`.
The alternative (`include_raw=True`) would change the return shape *and* demote schema-validation
failures from an exception to a field nobody checks.

### Errors

Node spans set `record_exception=False, set_status_on_exception=False` and handle errors by hand.
The human gate raises `GraphInterrupt` as ordinary control flow, and marking every
human-in-the-loop review `ERROR` would make the status field useless for finding real failures.
So interrupts get `gate.node.interrupted=true`; genuine exceptions get `StatusCode.ERROR` plus a
recorded exception.

---

## 5. The kill switch, and why it is not one line

`GATE_OTEL_ENABLED=false` must work — a reviewer cloning the repo should not need Docker to see
the gate run.

The obvious implementation, `trace.set_tracer_provider(NoOpTracerProvider())`, **silently does
nothing**. OTel guards that setter with a process-wide `Once`: any second call in the process —
the kill switch after an enabled run, or a second app instance in a test session — logs
*"Overriding of current TracerProvider is not allowed"* and returns. A test caught this
(DECISION_LOG entry 19).

So the module holds its own `_provider` and `tracer()` reads that, falling back to the OTel global
only when unset. `set_tracer_provider` is still called best-effort for third-party instrumentation
that reads the global, but nothing here depends on it succeeding.

Consequence to be aware of: the gate's spans no longer follow a provider installed by unrelated
code in the same process. That is the intended trade.

---

## 6. Log correlation

A structlog processor injects the active span's ids into every log line:

```
event='verdict' pr_id='003_auth_change' correlation_id=… trace_id=4bf92f… span_id=00f067…
```

So a Langfuse trace and a stdout line for the same review are joinable. Cheap, and it would be
strange to add tracing without it.

---

## 7. Limitations

### 7.1 One HITL review produces two traces — the UI links only the first

The resume leg (`POST /decision`) opens its **own** root run span with a new `trace_id`, and
`ReviewState.trace_id` is deliberately not overwritten (`Command(resume=…)` carries no state to
seed, and the deep-link should keep pointing where the review started).

- **trace 1** — intake → … → human_gate. This is what the **View trace** link opens.
- **trace 2** — human_gate resumed → verdict. Carries the *final* route and the verdict.
  **No link from the UI.**

Concretely, on fixture 003 (request_info → re-investigation → approve) the second investigation
and its semgrep spans live in trace 2, invisible from the review page. Also `gate.loop_count.final`
on trace 1 is not final — it is the count as of the pause.

**Workaround:** search `gate.thread_id` in Langfuse; it returns both.

**Decided 2026-08-12 — keep the two traces, say so in the UI.** The review page now prints
`first leg — search gate.thread_id=<id> for all` beside the **View trace** link, so the reader
learns the limit at the moment it matters instead of from this file. The link was *not* swapped
for a Langfuse search URL: `GATE_LANGFUSE_UI_BASE` defaults to a bare host with no project
segment, so a search URL would stack a second unverified guess on the deep-link's first. Making
one trace of the two legs needs the run span's `span_id` in `ReviewState` — additive, no state
version bump — and is worth doing only if §7.3 shows the traces arriving at all.

> Note, because an earlier reading of this was wrong: run-span attributes on the *first* leg are
> **not** sparse. On interrupt, `ainvoke` returns every state channel already written plus
> `__interrupt__` — verified:
> ```
> ['__interrupt__', 'diff', 'escalation', 'files_changed', 'loop_count',
>  'policy_context', 'pr_id', 'source', 'specialist_findings', 'thread_id']
> ```
> For fixture 002, `risk` and `route` have been written by then, so `gate.risk.level` and
> `gate.route.taken=human_gate` are both present. Missing keys are only things that genuinely had
> not happened yet.

### 7.2 Every UI poll creates a request span

A review parked at the human gate for four minutes while the reviewer reads the findings:

- detail poll every 2 s → 120 × `GET /api/reviews/{id}`
- plus `GET /api/reviews/{id}/pending` on each `awaiting_human` poll → another 120
- plus the list view at 5 s intervals

≈ **240 single-span traces** surrounding ~12 spans of actual review. The review trace is buried in
the trace list.

Likely one-line fix — `FastAPIInstrumentor.instrument_app` accepts an `excluded_urls` regex:

```python
FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls="api/reviews/.+")
```

**Untested.** Verify the parameter name and pattern against the installed
`opentelemetry-instrumentation-fastapi` before relying on it.

**Decided 2026-08-12 — back the polling off, leave the spans unfiltered.** The detail view now
polls every **10 s** while a review is `awaiting_human` and every 2 s otherwise
(`static/index.html`, `schedule()`). The same four minutes at the gate produce ≈48 request traces
instead of ≈240. One line, no OTel surface touched, and it cuts server load as a side effect.

`excluded_urls` stays unapplied until §7.3 closes, for two reasons:

- The parameter name and matching semantics are still unverified against the installed package.
- `api/reviews/.+` is too broad — it also matches `POST /api/reviews/{id}/decision`, a real human
  action worth tracing. A pattern that excludes only the two `GET` polls has to do that work
  itself, since `excluded_urls` matches on path without regard to method.

The deeper reason to wait: filtering spans before confirming that *any* span arrives gives a
missing trace two possible causes at once, and §7.3 is exactly that unconfirmed.

### 7.3 Never verified against a live Langfuse

The default endpoint is `http://localhost:3000/api/public/otel`. `OTLPSpanExporter` posts to that
URL verbatim — it appends nothing — and Langfuse has moved this path across major versions (some
releases expect `/api/public/otel/v1/traces`).

The failure mode is the bad kind: Langfuse returns **404**, the exporter logs a retry warning to
stderr and eventually drops the batch, and **the review completes perfectly**. Nothing in the app
breaks; you get an empty Langfuse and no reason why. A missing or wrong
`OTEL_EXPORTER_OTLP_HEADERS` gives a **401** with identical symptoms.

Every assertion in the test suite runs through OTel's `InMemorySpanExporter`, which proves the
span *tree* — parenting, links, attributes, kill switch — and proves nothing about whether
Langfuse accepts the payload. **First thing to check on first real run.**

### 7.4 What is actually in the payload

`input.value` on a `gate.llm._FindingList` span is the specialist system prompt concatenated with
the truncated diff — up to `GATE_MAX_DIFF_CHARS`, i.e. **20,000 characters of source code**. For a
live GitHub PR that is someone else's repository content sitting in the ClickHouse volume the
compose file mounts.

Acceptable on localhost with public repos, which is the stated scope. It stops being acceptable
the moment that Langfuse is shared, or the gate reviews a private repository. `ANTHROPIC_API_KEY`
and `GATE_GITHUB_TOKEN` are never span attributes — the model factory reads them from settings and
never echoes them.

---

## 8. Open questions for tomorrow

1. Verify the OTLP ingestion path against the Langfuse image tag pinned in
   `docker-compose.langfuse.yml`, then run one fixture review end-to-end and confirm the trace
   renders with prompts visible. (§7.3)
2. Decide whether to exclude the polling routes from request instrumentation. (§7.2)
3. Decide whether the two-trace HITL split is worth fixing — options are linking trace 2 back to
   trace 1, or surfacing both from the review page. Neither is free; the current behaviour is
   correct, just inconvenient. (§7.1)
