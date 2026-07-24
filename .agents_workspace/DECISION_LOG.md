# Decision Log — PR Compliance Gate

### Entry 1

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-23
**Task:** Implement all v1 plans (SKELETON + ITER_01..04) into a greenfield repo.

**Context:** The implement-from-plan flow describes SKELETON (scaffold + canned nodes) then
each iteration replacing canned output with real code. The repo is greenfield (no tracked
files). Replaying every iteration literally would mean writing canned stubs and rewriting
them up to four times.
**Decision:** Build each module directly to its final resolved state (through the ITER_04
`mvp: true` terminator), using the iteration specs to determine final behavior. Section scope
and the `depends_on` resolution order are still honored to decide what each file must contain.
**Impact / Risk:** Skips the intermediate "canned but wired" milestone. Low risk — the final
graph structure is identical; only the throwaway stubs are omitted.
**Outcome:** In progress.

### Entry 2

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-23
**Task:** Model IDs and observability defaults.

**Context:** Plan specifies `claude-haiku-4-5` (classifier/risk) and `claude-sonnet-4-6`
(specialists/investigator) as env-driven defaults. Project invariants mandate structlog +
correlation_id, which the plan does not mention but does not contradict (plan keeps "stdout
logging" in MVP scope).
**Decision:** Use the plan's model strings as `.env.example` defaults (env-overridable, per
plan). Add a minimal structlog config and a correlation-id middleware to satisfy the
non-negotiable observability invariant without exceeding plan scope.
**Impact / Risk:** Model ID strings must be verified against the live Anthropic API before a
real run; flagged for the user. Logging addition is small and additive.
**Outcome:** In progress.

### Entry 3

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-23
**Task:** Reducer for `ReviewState.specialist_findings`.

**Context:** ITER_02 §04 specifies `Annotated[list, operator.add]` to merge the parallel
security/license fan-out. But specialists also re-run after each investigation loop. With plain
`operator.add`, round-1 findings (including `needs_more_context=True`) accumulate next to
round-2 findings, so reconcile's rule 3 would keep firing until the loop budget drains and
rule 4 escalates — the "investigation resolved the question → clean verdict" path (rule 6)
becomes unreachable.
**Decision:** Implement `merge_findings(existing, new)` that replaces all findings from a
specialty when that specialty emits a fresh batch. Order-independent across the two parallel
specialists (satisfies the original clobber concern), and a new investigation round cleanly
supersedes the prior one.
**Impact / Risk:** Deviates from the literal `operator.add` in the plan; strictly more correct
for the looping case. No downside for the single-pass case.
**Outcome:** Implemented in `graph/state.py`.

### Entry 4

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-23
**Task:** Which specialist reviews DATA-* (data-handling) policies.

**Context:** The plan defines two specialists (security, license) and three policy areas
(security, licensing, data-handling). ITER_02 §04 maps only `SEC-*`/`LIC-*` slices explicitly;
`DATA-*` is unassigned.
**Decision:** Route `DATA-*` chunks to the security specialist (`security = (SEC, DATA)`,
`license = (LIC,)`). PII/data-handling is a security concern and there is no third specialist.
**Impact / Risk:** Data-handling policy would otherwise go unreviewed. Low risk.
**Outcome:** Implemented in `graph/nodes/specialists.py`.

### Entry 5

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-23
**Task:** Listing reviews + non-blocking graph execution.

**Context:** (a) `GET /api/reviews` must list reviews, but LangGraph's checkpointer has no
first-class "list all threads" call. (b) The SKELETON stub `await graph.ainvoke(...)` inside
`POST /api/reviews` is synchronous, but ITER_04 §05 introduces 2s polling — which only means
anything if the POST returns before the run finishes.
**Decision:** (a) Keep a session-scoped in-memory `OrderedDict` registry (thread_id -> pr_id)
in `app.state.reviews` to back the list view; per-review state/status/risk are read live from
the checkpoint. (b) Launch each graph run (and each resume) as a background `asyncio` task and
return `{thread_id}` / `{status:"resumed"}` immediately, so polling reflects progress.
**Impact / Risk:** The list view is session-scoped (reviews created before a restart won't
appear in the list, though each is still reachable by `thread_id` via its persisted
checkpoint). Background-task exceptions are logged, not surfaced to the client. Both acceptable
for a single-user local demo; flagged for the user.
**Outcome:** Implemented in `api/app.py` and `api/routes.py`.

### Entry 6

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-23
**Task:** Concurrent-write safety on `ReviewState.escalation`.

**Context:** Both specialists run in the same LangGraph superstep. On a double timeout both write
`escalation`; a reducer-less channel raises `InvalidUpdateError` on concurrent writes. The plan
did not mark this field with a reducer.
**Decision:** Add a `coalesce_escalation` first-wins reducer (`Annotated[..., coalesce_escalation]`).
Reconcile only writes escalation when none exists yet, so first-wins is correct everywhere.
**Impact / Risk:** Prevents a latent crash on the double-timeout path. No behavioral change on
the single-write paths.
**Outcome:** Implemented in `graph/state.py`.

### Entry 7

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-24
**Task:** Plan-compliance audit of the v1 family (SKELETON + ITER_01..04) — where to place the
missing ITER_04 §04 test and whether to add the missing ITER_02 §06 `eval_routes.py`.

**Context:** ITER_04 §04 specifies three decision-endpoint tests "(mocked graph)"; the third —
`request_info` at an exhausted loop budget re-interrupts with `max_cycles` — was absent. Written
against the existing `_FakeGraph` it would assert nothing real (the fake never routes). ITER_02
§06 specifies `tests/eval_routes.py`; the repo instead folds route coverage into `eval_demo.py`.
**Decision:** (a) Cover the ITER_04 case in `test_decision_endpoint.py` but against the real
routing functions (`_route_after_gate` -> `investigate` -> `reconcile`) with `run_investigation`
monkeypatched, so the assertion is about the actual resume path, not the fake. (b) Do not add
`eval_routes.py`: `eval_demo.py` runs the same three fixtures end-to-end and asserts terminal
outcomes, which imply the routes; the substitution is already documented in its module docstring.
`expected_route` in each `meta.json` is consequently unread — kept as fixture documentation.
**Impact / Risk:** (a) The test is no longer purely endpoint-level, so an endpoint regression that
bypasses those routing functions would not be caught by it (the 409/404/Command tests still cover
the endpoint itself). (b) One planned eval filename does not exist; route regressions surface as
outcome mismatches instead.
**Outcome:** (a) implemented. (b) **superseded by entry 8** — the user asked for the remaining
open items to be closed, so `eval_routes.py` was written after all.

### Entry 8

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-24
**Task:** Close the two items left open by the v1 plan-compliance audit — reconcile's shadowed
rule 5, and the missing `tests/eval_routes.py`.

**Context:** ITER_02 §04 words rule 5 (disagreement) as severities spanning `info/warn` vs
`block` between specialists on one policy, ordered after rule 2 (any block -> human_gate). Those
two statements are inconsistent: rule 2 escalates every finding set containing a block, so the
rule-5 condition can never be reached, making the branch dead and `EscalationReason.disagreement`
unusable. Either the ordering or the condition has to give.
**Decision:** Keep the plan's ordering (it is stated as deliberate) and widen the condition to
what survives rule 2: two specialists citing the same `policy_id` at *different* severities.
The plan docs are left untouched as historical artifacts; this entry records the divergence.
Also wrote the planned `eval_routes.py` (reversing entry 7b), which asserts each fixture's
`expected_route` at its first resting point — the complement of `eval_demo`'s terminal outcomes.
**Impact / Risk:** More reviews can now escalate as `disagreement`. In practice near-zero change:
the specialists own disjoint policy-id prefixes (`SEC-*`/`DATA-*` vs `LIC-*`), so both citing one
policy is currently unreachable — the rule is there for when the prefix split loosens. Widening
also means a benign info-vs-warn split now stops at a human; that is the intended fail-safe
direction. `eval_routes` adds one more `llm`-marked eval to the paid run.
**Outcome:** Implemented; default suite 27 passed, ruff and mypy strict clean. The `llm` evals
were not run (API spend).
