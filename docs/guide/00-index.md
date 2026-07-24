# PR Compliance Gate — User & Operator Guide

PR Compliance Gate reviews a pull request against your organization's security, licensing, and
data-handling policy before merge. It classifies the change, scores its risk, runs specialist
agents on anything risky enough to warrant the spend, can launch an autonomous investigation into
the repository, and stops at a human approval gate when the decision should not be automatic.

This build is a **single-user local demo**: pull requests come from fixture directories on disk,
not from live GitHub, and the app has no authentication. Run it on your own machine.

## Who should read what

| You are | Read |
|---|---|
| A **reviewer** — you start reviews and decide their outcome in the web UI | Files `01`–`13`, then `30` |
| An **operator** — you install, configure, run, and recover the service | Files `20`–`24`, plus `40` |

Read the files in numeric order; each assumes only what came before it.

## Contents

### Getting started (reviewer)

1. [`01-getting-started.md`](01-getting-started.md) — from a running server to your first
   completed review.

### How-to (reviewer)

2. [`how-to/10-review-a-pull-request.md`](how-to/10-review-a-pull-request.md) — start a review and
   read every panel of the result.
3. [`how-to/11-decide-at-the-human-gate.md`](how-to/11-decide-at-the-human-gate.md) — approve,
   reject, or request more information.
4. [`how-to/12-run-the-demo-script.md`](how-to/12-run-the-demo-script.md) — drive all three demo
   reviews headlessly, with narration.
5. [`how-to/13-read-a-verdict.md`](how-to/13-read-a-verdict.md) — what each outcome means and what
   drove it.

### Operations (operator)

6. [`operations/20-install.md`](operations/20-install.md) — dependencies, API key, policy
   ingestion, health check.
7. [`operations/21-configure.md`](operations/21-configure.md) — every setting: purpose, default,
   effect.
8. [`operations/22-routine-operations.md`](operations/22-routine-operations.md) — start/stop,
   re-ingest policy, add a sample PR, run the test suite.
9. [`operations/23-monitoring.md`](operations/23-monitoring.md) — logs, correlation IDs, cost
   signals, what healthy looks like.
10. [`operations/24-incident-runbook.md`](operations/24-incident-runbook.md) — detection →
    diagnosis → remediation for each known failure.

### Reference

11. [`30-troubleshooting.md`](30-troubleshooting.md) — reviewer-facing symptom → fix table.
12. [`40-reference.md`](40-reference.md) — HTTP API, statuses, enums, file layout.

## How the pipeline works (one paragraph)

Every review runs as a checkpointed state machine. `intake` loads the fixture, `classify` labels
the change with a cheap model, `retrieve_policy` pulls matching policy excerpts, and `risk_score`
assigns a 0–100 score. Below the risk threshold the review ends immediately at an
`auto_approved` verdict — no expensive model is ever invoked. Above it, the **security** and
**license** specialists run in parallel, and `reconcile` (pure Python, no model) decides what
happens next: investigate further, stop at the human gate, or issue a verdict. Because state is
checkpointed to SQLite after every step, a review waiting at the human gate survives a restart.
