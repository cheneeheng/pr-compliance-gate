# HT-01 — How to review a pull request

[← Guide index](../index.md)

- **When:** you want a policy review of a pull request — either a live one on GitHub, or one of
  the bundled sample PRs.
- **Prerequisites:** the server is running and reachable at <http://127.0.0.1:8000>. For a live
  GitHub pull request, your operator must have configured a read-only `GATE_GITHUB_TOKEN`.
- **Time:** seconds for a low-risk change; up to a couple of minutes when specialists and an
  investigation run. A live pull request adds time for the first clone of that repository.

Both sources run through the identical pipeline. Everything under **Read the result** applies
either way.

## Start a review of a sample PR

1. Open <http://127.0.0.1:8000>. Click **← Reviews** first if you are on a detail page.
2. Leave the **Sample PR** option selected.
3. Pick a PR from the dropdown. Each entry shows its title and how many files it changes, for
   example `Refactor session validation in auth middleware (2 files)`.
4. Click **Start review**. The detail view opens with a `running` badge.

**Verify:** the review appears in the list on the **Reviews** page with a status badge and a
**Source** badge reading `fixture`.

**If it fails:** a banner reading `Failed to start review: fixture … missing meta.json or
diff.patch` means the fixture on disk is incomplete — that is an operator fix, see
[`../operations/OP-03-routine-operations.md`](../operations/OP-03-routine-operations.md).

## Start a review of a live GitHub pull request

1. On the **Reviews** page, select **GitHub PR URL**. The URL box enables and the sample-PR
   dropdown greys out.
2. Paste the full pull-request URL, for example
   `https://github.com/owner/repo/pull/123`. Only that exact form is accepted — no `owner/repo#123`
   shorthand.
3. Click **Start review**.

The server checks the URL and your access **before** the review is created, so a mistake is
reported immediately instead of producing a broken review:

| Banner | Meaning |
|---|---|
| `unrecognized pull-request URL …` | The URL is not in the accepted form. Correct it and retry. |
| `… token lacks scope for this pull request` | The repository is private, or the token is under-scoped. Operator issue. |
| `… pull request or repository not found` | Wrong owner, repo, or number — or the repository is private and invisible to the token. |

**Verify:** the detail page opens with a **Source** badge reading `github` and a link to the pull
request on GitHub.

**If it fails after the review has started:** fetching the diff and cloning the repository happen
in the background, so failures there appear as an **Escalation** panel rather than a banner. See
`pr_too_large` and `source_unavailable` under [Escalation](#escalation) below.

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
- **Budget** — the tool calls it used, the number of semgrep scans it ran, and the estimated cost
  in dollars to four decimal places. All three are measured by the harness, not reported by the
  model.
- **Files examined** — exactly which files it read.
- **Static analysis** — semgrep results, if it ran a scan. Each hit shows a severity (`info`,
  `warn`, `block`), the rule that fired, the file and line, and a message naming the policy it
  maps to.

Read **Static analysis** carefully, because a dash and a "no findings" message mean different
things:

| Static analysis shows | Meaning |
|---|---|
| One or more hits | Semgrep ran and matched. Each hit is a concrete file and line you can check. |
| `no static findings` | Semgrep ran and matched nothing. |
| `—` | Semgrep never ran on this review. It says nothing about the code. |

A summary beginning `investigation inconclusive:` means the sub-agent hit a budget ceiling or
errored. That is a designed outcome, not a crash: the review continues to a human.

### Escalation

Present only when the review was escalated. The reason is one of:

- `timeout` — a specialist exceeded its time budget.
- `max_cycles` — questions remained after the investigation budget was spent.
- `disagreement` — the two specialists rated the same policy differently.
- `pr_too_large` — the pull request changes more files than `GATE_GH_MAX_FILES`. The gate refuses
  rather than reviewing a subset. Split the pull request, or ask your operator to raise the cap.
- `source_unavailable` — GitHub could not be reached, rate-limited the fetch, or the repository
  could not be cloned. Retry later; if it repeats, it is an operator issue.

The last two happen while the pull request is being fetched, before any model runs. A review that
escalates for either reason has empty **Classification**, **Risk**, and **Findings** panels — that
is expected, not a second failure.

### Verdict

The terminal outcome — `approved`, `rejected`, or `auto_approved` — with a rationale. See
[`HT-04-read-a-verdict.md`](HT-04-read-a-verdict.md).

### View trace

When your operator has enabled tracing, the detail page shows a **View trace** link that opens
this review's full execution in Langfuse — every step, how long it took, and what each model call
cost. Use it when you want to know *why* a review was slow or expensive.

No link means tracing is switched off, or the review ran before it was enabled. That is not a
failure. One caveat worth knowing: if the review paused at the human gate, the link opens the part
*before* the pause. To see what happened after your decision, search the review's thread id in
Langfuse.

## Watch progress

- The **Reviews** list refreshes statuses every five seconds. Statuses are `running`,
  `awaiting_human`, and `done`. Each row also shows the review's **Source** — `fixture` or
  `github`.
- The detail page polls on its own; leave it open.
- Closing the browser does not cancel a review. Reopen the page and click the PR id to return.

**Verify:** the badge eventually reaches `done` (or `awaiting_human`, waiting on you).

**If it fails:** a review stuck on `running` for minutes is an operator issue — see
[`../operations/OP-05-incident-runbook.md`](../operations/OP-05-incident-runbook.md).

---

[Guide index](../index.md) · [HT-02 Decide at the human gate →](HT-02-decide-at-the-human-gate.md)
