# HT-04 — How to read a verdict

**When:** a review's badge reads `done` and the **Verdict** panel is populated.
**Prerequisites:** none beyond having the review open.

## The three outcomes

| Outcome | Meaning | How it was reached |
|---|---|---|
| `auto_approved` | Approved automatically. No human saw it, and no specialist agent ran. | The risk score fell below the threshold, so the pipeline exited straight from the risk step. |
| `approved` | Approved by a person. | The review reached the human gate and someone clicked **Approve**. |
| `rejected` | Rejected by a person. | The review reached the human gate and someone clicked **Reject**. |

There is no automatic rejection. Every negative outcome is a human decision — the machine can
only approve cheaply or escalate.

## Work out why

Read the panels bottom-up from the verdict:

1. **Verdict → rationale.** The stated reason for the outcome.
2. **Escalation.** If populated, this is why a human was involved at all — `timeout`,
   `max_cycles`, `disagreement`, `pr_too_large`, or `source_unavailable`.
3. **Findings.** The policy citations. A `block` severity here is the usual reason a review left
   the automatic path.
4. **Investigation.** What the sub-agent actually read, and what it cost.
5. **Risk.** The score, the level, and the reasons behind them.
6. **Classification.** The change type that framed everything downstream.

## Common patterns

- **`auto_approved` with empty Findings, Investigation, Escalation panels** — normal. This is the
  cheap path working as designed.
- **`approved` with a `block` finding** — a human overrode the specialist. The decision note, if
  one was written, records why.
- **`approved` or `rejected` with an escalation of `timeout`** — the specialists never finished;
  the human decided without their input. Worth re-running the review if the outcome matters.
- **An Investigation summary starting `investigation inconclusive:`** — the sub-agent ran out of
  budget or errored, and the human decided without its answer.
- **`approved` or `rejected` with an escalation of `pr_too_large` or `source_unavailable`** — the
  pull request was never reviewed at all. Every panel above the verdict is empty, and the human
  decided with no machine input whatsoever.

If tracing is enabled, the **View trace** link opens the same sequence with timings and per-call
costs attached. For a review that paused at the human gate, that link covers the part *before* the
pause — see [`HT-01-review-a-pull-request.md`](HT-01-review-a-pull-request.md).
