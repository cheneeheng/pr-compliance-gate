# PR Compliance Gate — User & Operator Guide

PR Compliance Gate reviews a pull request against your organization's security, licensing, and
data-handling policy before merge. It classifies the change, scores its risk, runs specialist
agents on anything risky enough to warrant the spend, can launch an autonomous investigation into
the repository, and stops at a human approval gate when the decision should not be automatic.

Pull requests come from either source: a **live GitHub pull request** you paste by URL, or a
**fixture** directory on disk. Both run through the identical pipeline.

This build is a **single-user local demo**: it has no authentication, and its GitHub token is
read-only by design — the gate never comments, labels, or merges. Run it on your own machine.
Every review can be traced end to end if you point it at a self-hosted Langfuse; that is optional,
and the app runs normally without one.

## Who should read what

| You are | Read |
|---|---|
| A **reviewer** — you start reviews and decide their outcome in the web UI | `getting-started.md`, then `how-to/HT-01`–`HT-04`, then `troubleshooting.md` |
| An **operator** — you install, configure, run, and recover the service | `operations/OP-01`–`OP-05`, plus `reference.md` |

Within a folder, read in `HT-`/`OP-` order; each page assumes only what came before it.

## Contents

### This page's folder (reviewer)

- [Getting started](getting-started.md) — read this when the server is running and you have never
  used the gate before.
- [Troubleshooting](troubleshooting.md) — read this when something in the browser looks wrong and
  you want the symptom-to-fix table.
- [Reference](reference.md) — read this when you need the HTTP API, an exact enum value, a span
  name, or the file layout.

### `how-to/` (reviewer)

- [HT-01 — How to review a pull request](how-to/HT-01-review-a-pull-request.md) — read this when
  you want to start a review and understand every panel of the result.
- [HT-02 — How to decide at the human gate](how-to/HT-02-decide-at-the-human-gate.md) — read this
  when a review is waiting on your **Approve**, **Reject**, or **Request more info**.
- [HT-03 — How to run the demo script](how-to/HT-03-run-the-demo-script.md) — read this when you
  want all three demo reviews narrated in a terminal instead of clicked through the UI.
- [HT-04 — How to read a verdict](how-to/HT-04-read-a-verdict.md) — read this when a review has
  finished and you want to know what drove the outcome.

### `operations/` (operator)

- [OP-01 — Installation](operations/OP-01-install.md) — read this when you are standing the
  service up for the first time.
- [OP-02 — Configuration](operations/OP-02-configure.md) — read this when you need to know what a
  setting does, its default, and its effect.
- [OP-03 — Routine operations](operations/OP-03-routine-operations.md) — read this when you are
  starting or stopping the service, editing the policy corpus, adding a sample PR, managing the
  clone cache, or running the tests.
- [OP-04 — Monitoring](operations/OP-04-monitoring.md) — read this when you want to know whether
  the system is healthy, and where the time and money went.
- [OP-05 — Incident runbook](operations/OP-05-incident-runbook.md) — read this when something has
  broken and you need detection, diagnosis, remediation, and verification.

### Deeper reading (engineering notes)

These sit outside the guide. They explain *why* a subsystem is built the way it is — read them
when the operator pages are not enough.

- [`../tracing-design.md`](../tracing-design.md) — the span tree, the three traps the tracing code
  is designed around, and what it still does not do.
- [`../semgrep-rules.md`](../semgrep-rules.md) — what semgrep matches that grep cannot, how the
  gate's four rules are built, and how to add one.

## Glossary

| Term | Meaning |
|---|---|
| **Fixture** | A pull request on disk under `data/sample_prs/<pr_id>/`. One of the two sources; the other is a live GitHub pull request. |
| **Source** | Where a review's pull request came from — `fixture` or `github`. Shown as a badge on the reviews list and the detail page. |
| **Workspace** | The file tree the investigator may read: a fixture's `repo/` snapshot, or a live pull request's shallow clone under `data/workspaces/<head_sha>/`. |
| **Classification** | The first pipeline step: labels the change `code`, `dependency`, `config`, `docs`, or `data_handling`. |
| **Risk gate** | The first cost gate. A review scoring below `GATE_RISK_THRESHOLD` ends immediately at `auto_approved`, and no expensive model is ever invoked. |
| **Specialist** | One of the two reviewer agents — **security** and **license** — that run in parallel above the risk threshold and produce findings. |
| **Finding** | One specialist observation: a severity (`info`, `warn`, `block`), the policy id it cites, and a description. |
| **Investigation** | An autonomous sub-agent that reads the repository snapshot under hard budgets when a specialist cannot decide from the diff alone. The second cost gate. |
| **Reconcile** | The routing step. Plain application code, no model: it reads the findings and the loop budget and decides investigate, human gate, or verdict. |
| **Static analysis** | Semgrep results the investigation collected, using a committed local ruleset. Each hit names a rule, a file and line, and the policy it maps to. |
| **Escalation** | The recorded reason a review needed a human — `timeout`, `disagreement`, `max_cycles`, `pr_too_large`, or `source_unavailable`. |
| **Human gate** | The pause where a review waits for your **Approve**, **Reject**, or **Request more info**. |
| **Verdict** | The terminal outcome — `approved`, `rejected`, or `auto_approved`. |
| **Checkpoint** | The saved state written after every pipeline step, which is why a review waiting at the human gate survives a restart. |
| **Thread id** | The identifier for one review, used in every `/api/reviews/{thread_id}` URL. |
| **Policy corpus** | The Markdown policies in `data/policies/`. `index.yaml` maps each change type to the files that govern it, so retrieval is a deterministic file lookup — there is no vector store and no ingestion step. |
| **Trace** | The record of one review's execution, exported to Langfuse when tracing is enabled. The detail page links to it as **View trace**. |

## How the pipeline works (one paragraph)

Every review runs as a checkpointed state machine. `intake` records the pull request that was
loaded — and sends it straight to the human gate if loading it failed, so a PR that is too large
or a repository that could not be reached never pays for a single model call. Otherwise `classify`
labels the change with a cheap model, `retrieve_policy` looks up the policy files that govern that
change type, and `risk_score` assigns a 0–100 score. Below the risk threshold the review ends
immediately at an
`auto_approved` verdict — no expensive model is ever invoked. Above it, the **security** and
**license** specialists run in parallel, and `reconcile` (pure Python, no model) decides what
happens next: investigate further, stop at the human gate, or issue a verdict. Because state is
checkpointed to SQLite after every step, a review waiting at the human gate survives a restart.
