# HT-03 — How to run the demo script

[← Guide index](../index.md)

- **When:** you want to see all three demo reviews end to end, narrated, without clicking through
  the UI — for a walkthrough, a recording, or a smoke test of a real configuration.
- **Prerequisites:** a shell in the repository root; `uv sync` done; `ANTHROPIC_API_KEY` set in
  `.env`. See [`../operations/OP-01-install.md`](../operations/OP-01-install.md).
- **Time / impact:** a few minutes. **This makes real Anthropic API calls and costs money.** It
  needs no server — it drives the pipeline in-process with an in-memory checkpoint store, and the
  human decisions are scripted.

## Steps

1. Run all three beats:

   ```bash
   uv run python examples/demo.py
   ```

2. Or run a single beat by fixture id:

   ```bash
   uv run python examples/demo.py 003_auth_change
   ```

3. Read the output. Each beat prints its classification, the risk score and gate decision, the
   specialist findings, any investigation with its tool-call count and cost, the scripted human
   decisions, and the final verdict.

**Verify:** the three beats end at these verdicts:

| Fixture | What it demonstrates | Verdict |
|---|---|---|
| `001_docs_typo` | The risk gate — scores low, no specialist ever runs | `auto_approved` |
| `002_new_dependency` | A GPL-3.0 dependency trips the license specialist; scripted **Reject** | `rejected` |
| `003_auth_change` | Autonomous investigation, scripted **Request more info** then **Approve** | `approved` |

**If it fails:** an authentication error means `ANTHROPIC_API_KEY` is unset or invalid; a run with
no policy citations points at the change-type mapping in `data/policies/index.yaml`. Both are
covered in [`../operations/OP-05-incident-runbook.md`](../operations/OP-05-incident-runbook.md).

> The script covers the three fixture beats only. The fourth demo beat — a live GitHub pull
> request by URL — needs the server and the UI, since the two-mode input is where a URL is
> entered. See [`HT-01-review-a-pull-request.md`](HT-01-review-a-pull-request.md).

## Related

The same three fixtures can be driven through the UI instead — start at
[`HT-01-review-a-pull-request.md`](HT-01-review-a-pull-request.md). The UI path lets you make the
human decisions yourself; the demo script scripts them.

---

[← HT-02 Decide at the human gate](HT-02-decide-at-the-human-gate.md) · [Guide index](../index.md) · [HT-04 Read a verdict →](HT-04-read-a-verdict.md)
