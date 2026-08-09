# v2 Backlog

Items from the plan-compliance audit of 2026-08-09 (ITER_01_v2 .. ITER_04_v2). Not a plan artifact
— no frontmatter, no `depends_on`, nothing here is a section spec. Each open entry says what
stands, why it was left, and what closing it costs; closed entries keep their record rather than
being deleted.

Open: **B-01**. Closed: **B-02**.

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

## B-02 — The registered branch of `submit_decision`'s lookup is untested — **CLOSED 2026-08-09**

**Where:** `src/gate/api/routes.py`, `tests/integration/test_decision_endpoint.py`

**What stood:** the lookup was a pair of ternaries, and no test submitted a decision for a thread
still in `app.state.reviews`, so the `registration is not None` half never ran. Coverage did not
catch it: `coverage.py` records no arc for a conditional expression, so both halves of a ternary
count as covered the moment the line executes once.

**How it was closed:** the ternaries are an `if/else` again — the form that fails `fail_under = 100`
loudly when the path goes untested — and
`test_a_resumed_run_is_traced_under_the_registration_not_the_checkpoint` registers a thread whose
`pr_id`/`source` differ from the checkpoint's, then asserts the run span carries the
*registration's* values. Verified by mutation: forcing the fallback branch makes that test fail.

**Still worth knowing:** the ternary blind spot is a property of `coverage.py`, not of this line.
Any other conditional expression in `src/gate` can hide the same gap; this was the one that was
found, not necessarily the only one.

---

## Not in this file

Items the *plan* defers are in ITER_04_v2 `## Out of MVP scope` (mcp-cassette, prompt
benchmarking, GitHub write-back, webhooks, auth, hosted deployment, list pagination, org-wide
policy sync, embedding retrieval). This file is only for gaps between the v2 plan and the shipped
code.
