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
