# v2 Backlog

Items from the plan-compliance audits of the v2 family (ITER_01_v2 .. ITER_04_v2). Not a plan
artifact — no frontmatter, no `depends_on`, nothing here is a section spec. Each open entry says
what stands, why it was left, and what closing it costs; closed entries keep their record rather
than being deleted.

Open: **B-03**, **B-04**. Closed: **B-01**, **B-02**.

Decisions behind these entries: `.agents_workspace/DECISION_LOG.md` entries 21, 23 and 24.

---

## B-01 — `risk_score` still uses the blunt diff truncation — **CLOSED 2026-08-10**

**Where:** `src/gate/graph/nodes/risk.py:29`

**What stood:** `risk_score` called `truncate_diff` (head+tail, sliced mid-hunk). `classify` and
both specialists were moved to `truncate_diff_by_file` during an earlier audit; the risk scorer
was not, because ITER_03_v2 §06 named "the classifier and specialists" and nothing else.

**How it was closed:** the plan was decided to be the thing that was wrong, not the omission.
ITER_03_v2 §06 now names every node that reads the diff — classify, risk_score and both
specialists — and `risk_score` calls `truncate_diff_by_file`. Plan first, then code: the §06 text
carries a dated clarification so a reader sees the correction rather than silent drift.

**Impact:** none observable. It only bites on a diff over `GATE_MAX_DIFF_CHARS` (20 000) reaching
`risk_score` — i.e. a *fixture*, since the live path already truncates per-file in
`GitHubMCPSource.load`. No shipped fixture is near the budget, so no eval expectation moves.

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

## B-03 — `rules/` ships no vendored registry rules

**Where:** `rules/gate-policy.yaml`

**What stands:** ITER_04_v2 §04 specifies "a handful of registry rules vendored locally plus two
or three hand-written rules aligned to the actual policy corpus". Only the hand-written half
shipped: four rules (`SEC-01`, `SEC-02`, `SEC-03`, `DATA-03`), including both rules the spec named
as examples.

**Why left:** vendoring third-party rule files is a dependency and licensing decision, not an
implementation detail — the semgrep registry ships under LGPL-2.1, which puts an attribution and
redistribution obligation on a repo that currently carries only Apache-2.0 content. The same
paragraph of the spec gives the purpose of the ruleset — rules that map to a `policy_id` are what
make the investigator's output *citable* "rather than generic lint noise" — and registry rules are
the generic half. Adding them without a human deciding the licence question is the larger risk.

**Impact today:** the investigator detects only what the four hand-written rules cover. Fixture 003
fires two of them, so the demo beat is unaffected.

**Cost to close:** copy the chosen registry rules into `rules/`, add their licence header and a
`NOTICE` entry, and extend `docs/semgrep-rules.md`. Decide the licence question first.

---

## B-04 — The demo beats link no trace screenshots

**Where:** `README.md` "Demo script"

**What stands:** ITER_04_v2 §04 says "each beat names the mechanism it demonstrates, and each links
a trace screenshot". The four beats name their mechanisms; none links an image, and the repo holds
no screenshot assets.

**Why left:** a real screenshot needs a live Langfuse container plus a paid end-to-end run, and a
fabricated one would misrepresent the artifact. This is a capture task for a human, not a code
change.

**Impact today:** the portfolio claim "proven, not asserted" rests on the reader running the demo
rather than on visible evidence in the README.

**Cost to close:** four screenshots from one `docker compose up` plus one `pytest -m llm` run,
committed under `docs/` and linked from each beat.

---

## Not in this file

Items the *plan* defers are in ITER_04_v2 `## Out of MVP scope` (mcp-cassette, prompt
benchmarking, GitHub write-back, webhooks, auth, hosted deployment, list pagination, org-wide
policy sync, embedding retrieval). This file is only for gaps between the v2 plan and the shipped
code.
