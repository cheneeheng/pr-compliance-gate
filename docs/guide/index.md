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
| A **reviewer** — you start reviews and decide their outcome in the web UI | `getting-started.md`, then `how-to/HT-01`–`HT-04`, then `troubleshooting.md` |
| An **operator** — you install, configure, run, and recover the service | `operations/OP-01`–`OP-05`, plus `reference.md` |

Within a folder, read in `HT-`/`OP-` order; each page assumes only what came before it.

## Contents

### Getting started (reviewer)

- [`getting-started.md`](getting-started.md) — from a running server to your first completed
  review.

### How-to (reviewer)

- [`HT-01`](how-to/HT-01-review-a-pull-request.md) — start a review and read every panel of the
  result.
- [`HT-02`](how-to/HT-02-decide-at-the-human-gate.md) — approve, reject, or request more
  information.
- [`HT-03`](how-to/HT-03-run-the-demo-script.md) — drive all three demo reviews headlessly, with
  narration.
- [`HT-04`](how-to/HT-04-read-a-verdict.md) — what each outcome means and what drove it.

### Operations (operator)

- [`OP-01`](operations/OP-01-install.md) — dependencies, API key, GitHub token, health check.
- [`OP-02`](operations/OP-02-configure.md) — every setting: purpose, default, effect.
- [`OP-03`](operations/OP-03-routine-operations.md) — start/stop, edit the policy corpus, add a
  sample PR, manage the clone cache, run the test suite.
- [`OP-04`](operations/OP-04-monitoring.md) — logs, correlation IDs, cost signals, what healthy
  looks like.
- [`OP-05`](operations/OP-05-incident-runbook.md) — detection → diagnosis → remediation for each
  known failure.

### Reference

- [`troubleshooting.md`](troubleshooting.md) — reviewer-facing symptom → fix table.
- [`reference.md`](reference.md) — HTTP API, statuses, enums, file layout.

## Glossary

| Term | Meaning |
|---|---|
| **Fixture** | A pull request on disk under `data/sample_prs/<pr_id>/`. This build reviews fixtures, not live GitHub pull requests. |
| **Classification** | The first pipeline step: labels the change `code`, `dependency`, `config`, `docs`, or `data_handling`. |
| **Risk gate** | The first cost gate. A review scoring below `GATE_RISK_THRESHOLD` ends immediately at `auto_approved`, and no expensive model is ever invoked. |
| **Specialist** | One of the two reviewer agents — **security** and **license** — that run in parallel above the risk threshold and produce findings. |
| **Finding** | One specialist observation: a severity (`info`, `warn`, `block`), the policy id it cites, and a description. |
| **Investigation** | An autonomous sub-agent that reads the repository snapshot under hard budgets when a specialist cannot decide from the diff alone. The second cost gate. |
| **Reconcile** | The routing step. Plain application code, no model: it reads the findings and the loop budget and decides investigate, human gate, or verdict. |
| **Escalation** | The recorded reason a review needed a human — `timeout`, `disagreement`, or `max_cycles`. |
| **Human gate** | The pause where a review waits for your **Approve**, **Reject**, or **Request more info**. |
| **Verdict** | The terminal outcome — `approved`, `rejected`, or `auto_approved`. |
| **Checkpoint** | The saved state written after every pipeline step, which is why a review waiting at the human gate survives a restart. |
| **Thread id** | The identifier for one review, used in every `/api/reviews/{thread_id}` URL. |
| **Policy corpus** | The Markdown policies in `data/policies/`, embedded once so the pipeline can retrieve the excerpts relevant to a change. |

## How the pipeline works (one paragraph)

Every review runs as a checkpointed state machine. `intake` loads the fixture, `classify` labels
the change with a cheap model, `retrieve_policy` pulls matching policy excerpts, and `risk_score`
assigns a 0–100 score. Below the risk threshold the review ends immediately at an
`auto_approved` verdict — no expensive model is ever invoked. Above it, the **security** and
**license** specialists run in parallel, and `reconcile` (pure Python, no model) decides what
happens next: investigate further, stop at the human gate, or issue a verdict. Because state is
checkpointed to SQLite after every step, a review waiting at the human gate survives a restart.
