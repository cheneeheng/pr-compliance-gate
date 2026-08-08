# OP-05 — Incident runbook

One entry per failure mode: detection, diagnosis, remediation, verification.

Design invariant worth remembering under pressure: **failures land on a human, not in a stack
trace.** A specialist timeout, an investigator budget exhaustion, or an SDK error all escalate the
review to the human gate rather than crashing it. A review that is genuinely stuck is the
exception, and it is always accompanied by a `graph_run_error` log line.

---

## Every review fails immediately

**Detection:** reviews never leave `running`; the log shows `graph_run_error` with an
authentication or credit message shortly after each start.

**Diagnosis:**

1. Check the key is loaded:

   ```bash
   grep ANTHROPIC_API_KEY .env
   ```

2. Confirm the server was started from the directory containing that `.env` — settings are read
   relative to the working directory.
3. Read the `error` field of the `graph_run_error` line. Authentication, credit-balance, and
   unknown-model errors are all reported there verbatim.

**Remediation:** set a valid `ANTHROPIC_API_KEY` in `.env`, or correct the `GATE_MODEL_*` id, then
restart the server. Configuration is cached per process; editing `.env` alone changes nothing.

**Verify:** a review of `001_docs_typo` reaches verdict `auto_approved`.

---

## A review is stuck on `running`

**Detection:** the status badge stays `running` well past a couple of minutes.

**Diagnosis:** search the log for the PR id:

```bash
grep 003_auth_change gate.log
```

- A `graph_run_error` line: the background run raised and will not resume. **Nothing retries it.**
- No error and no `verdict`: the run is genuinely still in flight — model calls plus an
  investigation can take a while.
- The server was restarted since the review started: the background run died with the process.

**Remediation:** there is no resume-a-crashed-run control. Start the review again from the UI; the
old thread stays in the list as a dead entry until the process restarts. If the underlying cause
is a slow specialist, raise `GATE_SPECIALIST_TIMEOUT_S` so it escalates cleanly instead of
appearing to hang.

**Verify:** the new review reaches `awaiting_human` or `done`.

---

## Reviews escalate with reason `timeout`

**Detection:** `specialist_timeout` log lines; the UI Escalation panel reads `timeout`.

**Diagnosis:** one specialist did not answer within `GATE_SPECIALIST_TIMEOUT_S` (default 60s).
Usual causes: Anthropic API latency or rate limiting, or an over-large diff — check whether the
affected fixtures are near `GATE_MAX_DIFF_CHARS`.

**Remediation:** raise `GATE_SPECIALIST_TIMEOUT_S` and restart. If it correlates with rate
limiting, run fewer reviews concurrently.

**Verify:** a re-run of the same fixture produces specialist findings with no escalation.

---

## Every investigation is inconclusive

**Detection:** Investigation summaries start with `investigation inconclusive:`.

**Diagnosis:** read the rest of the summary.

| Summary | Cause |
|---|---|
| `no repo snapshot for <pr_id>` | The fixture has no `repo/` directory. The sub-agent has nothing to read. |
| `no answer produced` | The tool loop ran but never produced a final structured answer — usually a turn budget too small for the question. |
| `Claude Code not found` | The Agent SDK could not locate the `claude` CLI. It runs the investigator as a subprocess, so the CLI must be installed and on the `PATH` of the account running the server — not merely on your interactive shell's `PATH`. |
| `Failed to start Claude Code: …` | The CLI was found but the subprocess did not come up. The trailing text is the underlying error; an authentication failure here means the key never reached the subprocess. |
| anything else | An SDK or API error; the text is the underlying exception. Cross-check `investigation_error` in the log. |

**Remediation:** add the `repo/` snapshot (see
[`OP-03-routine-operations.md`](OP-03-routine-operations.md)); or raise
`GATE_INVESTIGATOR_MAX_TURNS` / `GATE_INVESTIGATOR_MAX_TOOL_CALLS`; or address the API error.
Restart after any `.env` change.

**Verify:** an investigation on `003_auth_change` lists files under **Files examined** and its
summary does not begin `investigation inconclusive:`.

---

## Findings cite no policy, or the wrong one

**Detection:** findings carry empty or implausible policy ids; risk reasons never mention policy.

**Diagnosis:** the change's `change_type` maps to the wrong files, or to none. Retrieval is a
deterministic lookup, so this is a config question, not a ranking one — read
`data/policies/index.yaml` and check what that change type resolves to. A corpus that is *broken*
cannot produce this symptom: the server would have refused to start.

**Remediation:** fix the mapping in `index.yaml` and restart. Keyword overrides match whole,
case-folded tokens against the classifier's `touched_areas`, so `auth` matches "auth module" but
deliberately not "author metadata".

**Verify:** a fresh review of `002_new_dependency` produces a license finding citing a `LIC-` id.

---

## The server refuses to start

**Detection:** the process exits before `app_started`, with one of:

| Message | Cause | Remediation |
|---|---|---|
| `policy index has no entry for change_type(s): …` | A change type is unmapped in `index.yaml`. | Add an entry. An empty list is valid; an absent key is not. |
| `policy file 'x.md' is missing or unreadable` | `index.yaml` names a file that does not exist. | Fix the name or add the file. |
| `heading '…' has no policy id` | A `##` heading does not start with an `XX-00` id. | Fix the heading; specialists cite that id. |
| `… was written at GATE_STATE_VERSION=N but this build is version M` | The checkpoint schema changed under an existing database. | No migration exists for local demo data: back up `checkpoints.db` if the history matters, then delete it. |

These are deliberate: a config error fails the *process*, not a review halfway through.

---

## A live pull request escalates instead of being reviewed

**Detection:** the review sits at the human gate with every panel empty except **Escalation**.

**Diagnosis:** read the reason.

- `pr_too_large` — the PR has more changed files than `GATE_GH_MAX_FILES`. The gate refuses
  rather than reviewing a subset; a compliance gate that silently misses files is worse than one
  that stops.
- `source_unavailable` — GitHub rate-limited the fetch after three jittered retries, or the
  shallow clone failed. The detail text carries the underlying message.

**Remediation:** for `pr_too_large`, split the PR or raise the cap deliberately. For
`source_unavailable`, retry later; if it repeats, check the token and that `git` is on `PATH`.

**Verify:** a re-run of the same URL reaches `classify` rather than short-circuiting.

---

## Traces do not appear in Langfuse

**Detection:** reviews complete normally, but Langfuse is empty or the **View trace** link is
missing.

**Diagnosis:** in order —

1. `GATE_OTEL_ENABLED=false` disables tracing entirely and omits the link by design.
2. `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_EXPORTER_OTLP_HEADERS` carry **no** `GATE_` prefix. A
   misspelling is silent: the exporter falls back to `localhost:4318`, which is often right
   locally and wrong everywhere else.
3. Traces that end mid-graph mean spans were dropped on shutdown. The app drains its background
   runs before flushing the exporter, so this points at a hard kill (`SIGKILL`) rather than a
   clean stop.

**Remediation:** correct the variable names, restart, and stop the server gracefully.

**Verify:** a fresh review of `001_docs_typo` produces a `gate.review.run` trace whose child spans
include `gate.node.classify` and `gate.node.verdict`.

---

## Investigations report `semgrep unavailable`

**Detection:** the investigation summary or static-analysis panel carries
`semgrep unavailable: <reason>`.

**Diagnosis:** the reason names it. `semgrep binary not available` means the optional extra is not
installed. `unsupported event loop` means the server was started with `--reload`/`--workers`.
`no ruleset at …` means `rules/` did not resolve — most likely a wheel built without it.

**Remediation:** `uv sync --extra semgrep`; drop `--reload`; rebuild the wheel with `rules/` in the
`force-include` map.

**Verify:** a review of `003_auth_change` shows a non-empty **Static analysis** panel.

> Not an outage. This degrades the investigator's evidence, it does not fail the review — which is
> why it appears as a tool result rather than an error.

---

## `422` when starting a review

**Detection:** the UI banner reads `Failed to start review: …`; the log has a `gate_error` line
with `status=422`.

**Diagnosis:** the message names the defect exactly:

| Message | Cause |
|---|---|
| `fixture <id> missing meta.json or diff.patch` | One of the two required files is absent. |
| `fixture <id> has invalid meta.json: …` | The JSON does not parse; the decoder's own message follows. A *missing* `pr_id` or `title` does not raise — both fall back to the directory name. |
| `fixture <id> diff.patch has no parseable file headers` | The patch contains no `diff --git a/… b/…` line. |
| `invalid sample PR id '…'` | The id is not a plain directory name. The loader rejects path separators and `..` before touching the filesystem, so a fixture can never be read from outside `data/sample_prs/`. |
| `no sample PR fixture named '…'` | The id is well-formed but no such directory exists — usually renamed or deleted after the page loaded. |

**Remediation:** repair the fixture directory. A fixture with no `diff --git a/… b/…` header in
its patch is always rejected — that is where the changed-file list comes from. An
`invalid sample PR id` is not a fixture defect: rename the directory to a plain name matching
`[A-Za-z0-9_-]+` and start the review by that name.

**Verify:** the fixture appears in `curl http://127.0.0.1:8000/api/prs` and starts without a
banner. Note that a malformed fixture is silently omitted from that list, so its absence is itself
the symptom.

---

## The Reviews list is empty after a restart

**Detection:** reviews you ran before the restart are gone from the list.

**Diagnosis:** expected behaviour, not data loss. The list is backed by an in-memory map that is
session-scoped; the checkpoints themselves persist in `GATE_DB_PATH`.

**Remediation:** reach a specific review directly by thread id:

```bash
curl http://127.0.0.1:8000/api/reviews/<thread_id>
```

A review that was paused at the human gate can still be resumed by posting to
`/api/reviews/<thread_id>/decision`.

**Verify:** the endpoint returns state with a `status` field rather than a 404.

---

## Rollback and recovery

- **Bad configuration change:** restore the previous `.env` values and restart. No persisted state
  is affected by a configuration change.
- **Corrupt or oversized clone cache:** delete `data/workspaces/`. It holds only derived data and
  is re-cloned on demand.
- **Corrupt or unwanted checkpoint database:** stop the server, move `checkpoints.db` aside (do
  not delete it until you are sure), restart. See the warning in
  [`OP-03-routine-operations.md`](OP-03-routine-operations.md) — the blast radius is all review history,
  including reviews paused at the human gate.
- **Bad code deployment:** stop the server, `git checkout` the last known-good revision,
  `uv sync`, restart. Checkpoints written by a prior revision may not deserialize under an
  incompatible state schema — if startup or a state read fails after a revert, move
  `checkpoints.db` aside and start clean.

## Escalation

This is a single-user local demo with no on-call rotation, no hosted deployment, and no support
channel. When the runbook runs out: capture the failing log lines (filtered by `pr_id`) and the
full state from `/api/reviews/{thread_id}`, then file an issue on the repository.
