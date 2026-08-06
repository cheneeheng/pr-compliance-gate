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

**Diagnosis:** the vector store is empty or stale.

```bash
uv run python -m gate.intake.ingest
```

A printed count of `0` means no heading matched the policy id pattern — check that every policy
heading is an `##` whose first token looks like `SEC-01`.

**Remediation:** fix the headings, re-ingest. If you changed `GATE_EMBED_MODEL`, you **must**
re-ingest: old vectors are not comparable with new ones.

**Verify:** the printed chunk count matches the number of `##` policy headings, and a fresh
review of `002_new_dependency` produces a license finding citing a `LIC-` id.

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

- **Bad configuration change:** restore the previous `.env` values and restart. Nothing persisted
  is affected by a configuration change except the vector store, which only `GATE_EMBED_MODEL` and
  `GATE_CHROMA_DIR` touch.
- **Corrupt vector store:** delete `.chroma/` and re-ingest. It holds only derived data.
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
