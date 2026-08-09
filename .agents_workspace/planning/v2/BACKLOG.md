# v2 Backlog

Deliberately-unfixed items from the plan-compliance audit of 2026-08-09 (ITER_01_v2 ..
ITER_04_v2). Not a plan artifact — no frontmatter, no `depends_on`, nothing here is a section
spec. Each entry says what stands, why it was left, and what closing it costs.

Decisions behind these entries: `.agents_workspace/DECISION_LOG.md` entry 21.

---

## B-01 — `risk_score` still uses the blunt diff truncation

**Where:** `src/gate/graph/nodes/risk.py:29`

**What stands:** `risk_score` calls `truncate_diff` (head+tail, sliced mid-hunk). `classify` and
both specialists were moved to `truncate_diff_by_file` during the audit; the risk scorer was not.

**Why left:** ITER_03_v2 §06 names "the classifier and specialists" and nothing else. Widening the
change to a node the spec did not name is scope creep, and the spec is the authority the audit
runs against.

**Impact today:** none observable. It only bites on a diff over `GATE_MAX_DIFF_CHARS` (20 000) that
reaches `risk_score` untruncated — i.e. a *fixture*, since the live path already truncates
per-file in `GitHubMCPSource.load`. No shipped fixture is near the budget.

**Cost to close:** two lines (swap the import and the call). Decide first whether the plan intends
the risk scorer to be included; if so, ITER_03_v2 §06 should say so rather than the code drifting
ahead of it.

---

## B-02 — The registered branch of `submit_decision`'s lookup is untested

**Where:** `src/gate/api/routes.py:233-235`, `tests/integration/test_decision_endpoint.py`

**What stands:**

```python
pr_id = registration.pr_id if registration else snapshot.values.get("pr_id", "")
source = registration.source if registration else snapshot.values.get("source", "fixture")
```

No test submits a decision for a thread that is still in `app.state.reviews`, so the
`registration is not None` half never runs. Coverage does not catch it: `coverage.py` records no
arc for a conditional expression, so both halves of a ternary count as covered the moment the line
executes once.

**Why left:** the audit rewrote this as an `if/else`, which surfaced the gap as a real coverage
failure — then reverted to the ternary rather than add a test the task did not ask for. The gap is
pre-existing; the rewrite only made it visible.

**Impact today:** `fail_under = 100` is green, and for this path that green is not evidence. The
same blind spot applies to every other ternary in `src/gate` — this is the one that was found, not
necessarily the only one.

**Cost to close:** one test — register a thread, interrupt it, POST a decision, assert the run span
carries that registration's `pr_id` and `source`. Then restore the `if/else` form, which is the
version that fails loudly when the path goes untested again.

---

## Not in this file

Items the *plan* defers are in ITER_04_v2 `## Out of MVP scope` (mcp-cassette, prompt
benchmarking, GitHub write-back, webhooks, auth, hosted deployment, list pagination, org-wide
policy sync, embedding retrieval). This file is only for gaps between the v2 plan and the shipped
code.
