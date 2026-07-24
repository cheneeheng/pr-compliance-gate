# 30 — Troubleshooting (reviewer)

Symptoms you can see in the browser, and what to do about them. Anything requiring a shell belongs
to your operator — see [`operations/24-incident-runbook.md`](operations/24-incident-runbook.md).

| Symptom | Likely cause | What to do |
|---|---|---|
| Banner: `Could not load reviews: …` | The server is not running or not reachable. | Reload the page. If it persists, the server is down — tell your operator. |
| The PR dropdown is empty | No fixture directory is readable, or every fixture is malformed. | Operator issue: fixtures under `data/sample_prs/`. |
| A PR you expect is missing from the dropdown | That fixture failed to parse and was skipped silently. | Operator issue: the fixture needs `meta.json`, `diff.patch`, and at least one `diff --git` header. |
| Banner: `Failed to start review: fixture …` | The fixture is incomplete or malformed. | Operator issue; the message names the defect. |
| Banner: `Failed to start review: no sample PR fixture named '…'` | The fixture was renamed or removed after the page loaded. | Reload the page to refresh the dropdown. |
| Status stays `running` for more than a few minutes | The pipeline is slow, or the background run failed. | Reload once. If still `running`, ask your operator to check the log for `graph_run_error`. |
| Banner: `Already decided — refreshing.` | The decision was already submitted — a double-click or a second tab. | Nothing to do; the page refreshes to the current state. |
| Banner: `Decision failed: …` | The submission did not reach the server. | The buttons re-enable. Retry once. If it fails again, report the message. |
| Decision buttons never appear despite `awaiting_human` | The page missed a poll. | Click **← Reviews**, then reopen the review. |
| Findings cite no policy id, or none at all | The policy corpus was never ingested, or is stale. | Operator issue: re-run policy ingestion. |
| Investigation summary reads `investigation inconclusive: …` | The sub-agent hit a budget ceiling or errored. | Not a crash — decide on the evidence you have, or use **Request more info** with a sharper question if budget remains. |
| Escalation panel reads `max_cycles` | The investigation budget is spent with questions still open. | **Request more info** will not buy another useful round. Approve or reject on what you have. |
| Escalation panel reads `timeout` | A specialist did not finish in time; its input is missing. | Decide cautiously, or ask your operator to re-run the review with a longer specialist timeout. |
| Escalation panel reads `disagreement` | The two specialists rated the same policy differently. | Read both findings and make the call yourself — that is what the gate is for. |
| Your reviews vanished after the server restarted | The reviews list is per-session; the reviews themselves are still checkpointed on disk. | Ask your operator to look the review up by its thread id. |
