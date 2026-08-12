# HT-02 — How to decide at the human gate

[← Guide index](../index.md)

- **When:** a review's status badge reads `awaiting_human` and a **Decision** panel is showing.
- **Prerequisites:** you have read the Findings, Investigation, and Escalation panels for that
  review.
- **Time:** as long as you need — the review waits indefinitely and survives a server restart.

## Why the review stopped

A review reaches you for one of these reasons; the panels tell you which:

| Signal on the page | Why it stopped |
|---|---|
| A `block` finding in **Findings** | A specialist judged the change to violate policy outright. |
| **Escalation** reason `timeout` | A specialist did not answer in time; nothing was concluded. |
| **Escalation** reason `max_cycles` | Open questions remain and the investigation budget is spent. |
| **Escalation** reason `disagreement` | The specialists rated the same policy differently. |
| **Escalation** reason `pr_too_large` | The pull request changes more files than the configured cap. Nothing was reviewed. |
| **Escalation** reason `source_unavailable` | The pull request could not be fetched or cloned. Nothing was reviewed. |

The last two stop the review **before** any model runs, so **Findings** and **Investigation** are
empty and there is nothing to weigh. **Request more info** does not help — there is no diff and no
repository snapshot for an investigation to read. Fix the cause and start a new review instead;
your operator has the diagnosis in
[`../operations/OP-05-incident-runbook.md`](../operations/OP-05-incident-runbook.md).

## Choose an action

1. Read the **Findings** panel, then the **Investigation** summary if one is present.
2. Optionally type a note in the text box. The note is recorded with your decision.
3. Click one button:

   - **Approve** — the change may merge. Verdict becomes `approved`.
   - **Reject** — the change may not merge. Verdict becomes `rejected`.
   - **Request more info** — sends the review back for another autonomous investigation. **Your
     note becomes the investigation lead**, so write a specific question here, for example
     `confirm the signature check is enforced`. A vague or empty note wastes a cycle.

**Verify:** for **Approve** or **Reject**, the badge turns `done` and the **Verdict** panel
fills in. For **Request more info**, the badge returns to `running` and the **Investigation**
panel is replaced with the new round's result before the review comes back to you.

**If it fails:**

- Banner `Already decided — refreshing.` — the decision was already submitted (a double-click, or
  another tab). The page reloads the current state; no action needed.
- Banner `Decision failed: …` — the buttons re-enable so you can retry. If it keeps failing, see
  [`../troubleshooting.md`](../troubleshooting.md).

## Notes on the investigation budget

**Request more info** and the pipeline's own automatic investigations draw from **one shared
budget** (two cycles by default). A **Request more info** always runs one more investigation, but
it spends from that budget: once the budget is gone, the specialists' remaining open questions can
no longer trigger a further round, and the review comes back to you escalated with reason
`max_cycles`. At that point, decide on what you have. Ask your operator if you need a larger
budget; it is the `GATE_MAX_CYCLES` setting.

---

[← HT-01 Review a pull request](HT-01-review-a-pull-request.md) · [Guide index](../index.md) · [HT-03 Run the demo script →](HT-03-run-the-demo-script.md)
