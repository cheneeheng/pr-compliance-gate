# Troubleshooting (reviewer)

[← Guide index](index.md)

Symptoms you can see in the browser, and what to do about them. Anything requiring a shell belongs
to your operator — see [`operations/OP-05-incident-runbook.md`](operations/OP-05-incident-runbook.md).

| Symptom | Likely cause | What to do |
|---|---|---|
| Banner: `Could not load reviews: …` | The server is not running or not reachable. | Reload the page. If it persists, the server is down — tell your operator. |
| The PR dropdown is empty | No fixture directory is readable, or every fixture is malformed. | Operator issue: fixtures under `data/sample_prs/`. |
| A PR you expect is missing from the dropdown | That fixture failed to parse and was skipped silently. | Operator issue: the fixture needs `meta.json`, `diff.patch`, and at least one `diff --git` header. |
| Banner: `Failed to start review: fixture …` | The fixture is incomplete or malformed. | Operator issue; the message names the defect. |
| Banner: `Failed to start review: no sample PR fixture named '…'` | The fixture was renamed or removed after the page loaded. | Reload the page to refresh the dropdown. |
| Banner: `Failed to start review: invalid sample PR id '…'` | The id is not a plain directory name — it contains a path separator or other rejected character. Cannot happen from the dropdown; you get it by calling the API by hand. | Use the id exactly as the dropdown supplies it. If a fixture on disk is named this way, it is an operator fix. |
| Banner: `Review not found.` and the page stops updating | The review is not in the checkpoint store — usually a stale link, or the checkpoint database was reset. | Click **← Reviews** and start the review again. Polling has stopped, so reloading alone will not revive the page. |
| Banner: `Connection issue: …` on an open review | A poll failed — the server restarted, or the network dropped. | Nothing to do; polling continues and the banner clears on the next successful poll. If it persists, the server is down — tell your operator. |
| Status stays `running` for more than a few minutes | The pipeline is slow, or the background run failed. | Reload once. If still `running`, ask your operator to check the log for `graph_run_error`. |
| Banner: `Already decided — refreshing.` | The decision was already submitted — a double-click or a second tab. | Nothing to do; the page refreshes to the current state. |
| Banner: `Decision failed: …` | The submission did not reach the server. | The buttons re-enable. Retry once. If it fails again, report the message. |
| Decision buttons never appear despite `awaiting_human` | The page missed a poll. | Click **← Reviews**, then reopen the review. |
| Findings cite no policy id, or none at all | The change's `change_type` maps to no policy file, or the corpus is thin for it. | Operator issue: check `data/policies/index.yaml`. |
| Escalation panel reads `pr_too_large` | The pull request has more changed files than `GATE_GH_MAX_FILES`. | Deliberate: the gate refuses rather than reviewing a subset. Split the PR, or ask your operator to raise the cap. |
| Escalation panel reads `source_unavailable` | GitHub rate-limited the fetch, or the repository could not be cloned. | Retry the review later. If it repeats, it is an operator issue. |
| Investigation shows `—` under Static analysis | Semgrep never ran on this review. | Not a failure. `no static findings` would mean it ran and found nothing. |
| No **View trace** link on a review | Tracing is disabled, or the review predates it. | Expected when `GATE_OTEL_ENABLED=false`. |
| Investigation summary reads `investigation inconclusive: …` | The sub-agent hit a budget ceiling or errored. | Not a crash — decide on the evidence you have, or use **Request more info** with a sharper question if budget remains. |
| Escalation panel reads `max_cycles` | The investigation budget is spent with questions still open. | **Request more info** will not buy another useful round. Approve or reject on what you have. |
| Escalation panel reads `timeout` | A specialist did not finish in time; its input is missing. | Decide cautiously, or ask your operator to re-run the review with a longer specialist timeout. |
| Escalation panel reads `disagreement` | The two specialists rated the same policy differently. | Read both findings and make the call yourself — that is what the gate is for. |
| Your reviews vanished after the server restarted | The reviews list is per-session; the reviews themselves are still checkpointed on disk. | Ask your operator to look the review up by its thread id. |
