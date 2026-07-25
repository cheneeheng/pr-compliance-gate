# 10 — How to review a pull request

**When:** you want a policy review of one of the available sample PRs.
**Prerequisites:** the server is running and reachable at <http://127.0.0.1:8000>.
**Time:** seconds for a low-risk change; up to a couple of minutes when specialists and an
investigation run.

## Start the review

1. Open <http://127.0.0.1:8000>. Click **← Reviews** first if you are on a detail page.
2. Pick a PR from the dropdown. Each entry shows its title and how many files it changes, for
   example `Refactor session validation in auth middleware (2 files)`.
3. Click **Start review**. The detail view opens with a `running` badge.

**Verify:** the review appears in the list on the **Reviews** page with a status badge.

**If it fails:** a banner reading `Failed to start review: fixture … missing meta.json or
diff.patch` means the fixture on disk is incomplete — that is an operator fix, see
[`../operations/22-routine-operations.md`](../operations/22-routine-operations.md).

## Read the result

The detail page shows six panels, top to bottom. Each is empty (—) until the pipeline reaches it.

### Classification

What kind of change this is, decided by a cheap model.

- **Type** — one of `code`, `dependency`, `config`, `docs`, `data_handling`.
- **Summary** — a one-line description of the change.
- **Touched areas** — the parts of the system the change reaches.

### Risk

- **Score** — 0–100.
- **Level** — `low`, `medium`, or `high`. The level is computed from the score against configured
  thresholds, not chosen by the model. `low` means the review short-circuits to an automatic
  approval; specialists never run.
- **Model** — which model produced the score.
- **Reasons** — the drivers behind the score. Read these first when a score surprises you.

### Findings

Grouped by specialist (`security`, `license`). Each finding shows:

- a **severity** — `info`, `warn`, or `block`;
- the **policy id** it cites, for example `SEC-03` or `LIC-02`;
- a description;
- optionally `(needs context)`, meaning the specialist could not decide from the diff alone.

Any `block` finding sends the review to the human gate. Any `(needs context)` finding triggers an
investigation if budget remains.

### Investigation

Present only when the autonomous sub-agent ran.

- **Summary** — what it found, in its own words.
- **Tool calls** — how many read-only file operations it used.
- **Cost estimate** — dollars, to four decimal places. Measured by the harness, not reported by
  the model.
- **Files examined** — exactly which files it read.

A summary beginning `investigation inconclusive:` means the sub-agent hit a budget ceiling or
errored. That is a designed outcome, not a crash: the review continues to a human.

### Escalation

Present only when the review was escalated. The reason is one of:

- `timeout` — a specialist exceeded its time budget.
- `max_cycles` — questions remained after the investigation budget was spent.
- `disagreement` — the two specialists rated the same policy differently.

### Verdict

The terminal outcome — `approved`, `rejected`, or `auto_approved` — with a rationale. See
[`13-read-a-verdict.md`](13-read-a-verdict.md).

## Watch progress

- The **Reviews** list refreshes statuses every five seconds. Statuses are `running`,
  `awaiting_human`, and `done`.
- The detail page polls on its own; leave it open.
- Closing the browser does not cancel a review. Reopen the page and click the PR id to return.

**Verify:** the badge eventually reaches `done` (or `awaiting_human`, waiting on you).

**If it fails:** a review stuck on `running` for minutes is an operator issue — see
[`../operations/24-incident-runbook.md`](../operations/24-incident-runbook.md).
