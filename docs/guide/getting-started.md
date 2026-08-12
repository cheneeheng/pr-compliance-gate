# Getting started

[← Guide index](index.md)

Goal: complete one review end to end and see a verdict, in about five minutes.

## Before you begin

- The server is installed and running. If it is not, ask your operator, or follow
  [`operations/OP-01-install.md`](operations/OP-01-install.md) yourself.
- You can open <http://127.0.0.1:8000> in a browser.
- The policy corpus is present and valid. There is no ingestion step — retrieval is a
  deterministic file lookup — and the server refuses to start if the corpus is broken, so if it
  is running, the corpus is fine.

There is no login. Anyone who can reach the page can start and decide reviews.

## Steps

1. Open <http://127.0.0.1:8000>. You see the heading **PR Compliance Gate**, a dropdown of sample
   PRs, and a **Start review** button. If no reviews exist yet, the page reads
   "No reviews yet — start one above."

2. Choose **Refactor session validation in auth middleware** from the dropdown. This is fixture
   `003_auth_change`, the one that exercises the whole pipeline.

3. Click **Start review**. The button changes to "Starting…" and the page switches to the review
   detail view, showing the PR id and a status badge reading `running`.

4. Wait. The detail view polls on its own; panels fill in as the pipeline progresses —
   **Classification**, then **Risk**, then **Findings**. A panel that has not been reached yet
   shows a dash (—).

5. When the status badge turns `awaiting_human`, a **Decision** panel appears at the bottom with a
   note box and three buttons: **Approve**, **Reject**, **Request more info**.

6. Type `confirm the signature check is enforced` in the note box and click **Request more info**.
   The buttons disable and a "Submitting…" spinner appears; the status returns to `running` while
   the investigator re-examines the repository.

7. When the status returns to `awaiting_human`, click **Approve** (a note is optional).

8. The status badge turns `done` and the **Verdict** panel shows **approved** with a rationale.

**Verify:** the **Verdict** panel is populated and the badge reads `done`. Click **← Reviews** —
your review is listed with status `done` and its risk level.

**If it fails:** a red banner at the top of the page names the error. See
[`troubleshooting.md`](troubleshooting.md); the most common causes are a missing
`ANTHROPIC_API_KEY` and, for a GitHub PR URL, a missing or under-scoped `GATE_GITHUB_TOKEN`.

## What you just exercised

- The **risk gate** — this PR scored above the threshold, so specialist agents ran.
- The **investigator gate** — the security specialist could not confirm safety from the diff
  alone, so an autonomous sub-agent read the repository under a hard budget. Its tool-call count
  and estimated cost are in the **Investigation** panel.
- The **human gate** — the review paused for you, and your note became a lead for the second
  investigation.

For a review that costs almost nothing, start `001_docs_typo`: it scores below the threshold and
finishes at `auto_approved` without ever invoking a specialist.

## Next

- [`how-to/HT-01-review-a-pull-request.md`](how-to/HT-01-review-a-pull-request.md) — every panel
  explained.
- [`how-to/HT-02-decide-at-the-human-gate.md`](how-to/HT-02-decide-at-the-human-gate.md) — choosing
  between the three decisions.
