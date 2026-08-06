# OP-03 — Routine operations

Everything here assumes a shell in the repository root and a completed
[installation](OP-01-install.md).

## Start and stop the service

Start:

```bash
uv run uvicorn gate.api.app:app --reload
```

Stop: `Ctrl+C`. Log line `app_stopped` confirms clean shutdown.

**Impact of a restart:** reviews mid-flight are lost — a background graph run is not resumed
automatically after a crash or restart. Reviews **paused at the human gate** are safe: their state
is checkpointed. However, the in-memory list of reviews is session-scoped, so after a restart the
**Reviews** page starts empty even though the checkpoints remain on disk; a review can still be
reached by its thread id at `/api/reviews/{thread_id}`.

**Verify:** `curl http://127.0.0.1:8000/api/prs` returns a non-empty array.

## Re-ingest the policy corpus

**When:** after editing, adding, or deleting any file in `data/policies/`.
**Impact:** the collection is dropped and rebuilt. Retrieval returns nothing for the few seconds
the rebuild takes; in-flight reviews may cite no policy.

1. Edit the policy Markdown. Each policy must be an `##` heading whose first token is the policy
   id, exactly in the form `## SEC-01 — No new network egress without review`. Headings without a
   matching `XX-00` id token are skipped silently.
2. Rebuild:

   ```bash
   uv run python -m gate.intake.ingest
   ```

**Verify:** the printed chunk count matches the number of `##` policy headings across
`data/policies/*.md`.

**If it fails:** a count lower than expected means some headings did not match the id pattern.
Check the id format (2–6 uppercase letters, hyphen, 2–3 digits) and that it is the first token of
the heading.

## Add a sample pull request

**When:** you want a new scenario to review.

1. Create the directory:

   ```bash
   mkdir -p data/sample_prs/004_my_change/repo
   ```

2. Write `data/sample_prs/004_my_change/meta.json`:

   ```json
   {
     "pr_id": "004_my_change",
     "title": "Add rate limiting to the public API"
   }
   ```

   Only `pr_id` and `title` are read by the application; the demo fixtures carry extra
   `expected_*` keys used by the evaluation tests, which are ignored at runtime.

3. Write `data/sample_prs/004_my_change/diff.patch` as a real unified diff. **At least one
   `diff --git a/… b/…` header is required** — the changed-file list is parsed from those headers,
   and a patch with none is rejected.

4. Populate `data/sample_prs/004_my_change/repo/` with the file snapshot the investigator is
   allowed to read. This is optional: without it, any investigation returns
   `investigation inconclusive: no repo snapshot for 004_my_change`.

5. Reload the browser page. No restart is needed — fixtures are read from disk per request.

**Verify:** the new title appears in the dropdown, and

```bash
curl http://127.0.0.1:8000/api/prs
```

lists `004_my_change`.

**If it fails:** a fixture that cannot be parsed is **silently skipped** in the list view, but
returns HTTP 422 with a specific message if you start it by id. Get the message with:

```bash
curl -i -X POST http://127.0.0.1:8000/api/reviews \
  -H 'content-type: application/json' \
  -d '{"pr_id":"004_my_change"}'
```

## Run the test suite

```bash
uv run pytest          # unit + integration; no API calls, no key needed
uv run pytest --cov    # same, with coverage; fails below 100% of src/gate
uv run pytest -m llm   # behavioural evals against the real API — costs money, needs a key + ingest
```

The default run is safe to execute at any time: it injects a dummy key and redirects storage to
temporary paths, so it touches neither your `checkpoints.db` nor your `.chroma` directory.

## Reset state

> **Warning:** destructive. Deleting the checkpoint database permanently discards every review,
> including any paused at the human gate. There is no export and no undo — take a file copy first
> if the history matters.

```bash
cp checkpoints.db checkpoints.db.bak   # take a backup first
rm checkpoints.db                      # blast radius: all review history
```

Restore by moving the backup back into place while the server is stopped.

Rebuilding the vector store is safe by comparison — `.chroma/` holds only derived data:

```bash
rm -rf .chroma
uv run python -m gate.intake.ingest
```

**Verify:** the server starts, `app_started` is logged, and a fresh review of `001_docs_typo`
reaches `auto_approved`.
