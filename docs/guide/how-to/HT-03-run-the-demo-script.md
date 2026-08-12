# HT-03 — How to run the demo script

[← Guide index](../index.md)

- **When:** you want to see the demo reviews end to end, narrated, without clicking through the
  UI — for a walkthrough, a recording, or a smoke test of a real configuration.
- **Prerequisites:** a shell in the repository root; `uv sync` done; `ANTHROPIC_API_KEY` set in
  `.env`. The fourth beat additionally needs `GATE_GITHUB_TOKEN`; without one it is skipped and
  the three fixture beats still run. See
  [`../operations/OP-01-install.md`](../operations/OP-01-install.md).
- **Time / impact:** a few minutes. **This makes real Anthropic API calls and costs money.** It
  needs no server — it drives the pipeline in-process with an in-memory checkpoint store, and the
  human decisions are scripted.

## Steps

1. Run every beat:

   ```bash
   uv run python examples/demo.py
   ```

2. Or run a single beat by name:

   ```bash
   uv run python examples/demo.py 003_auth_change
   ```

3. Or run the live beat against a pull request of your own:

   ```bash
   uv run python examples/demo.py live --pr-url https://github.com/owner/repo/pull/123
   ```

4. Read the output. Each beat prints its classification, the risk score and gate decision, the
   specialist findings, any investigation with its tool-call count and cost, the scripted human
   decisions, and the final verdict.

**Verify:** the beats end at these verdicts:

| Beat | What it demonstrates | Verdict |
|---|---|---|
| `001_docs_typo` | The risk gate — scores low, no specialist ever runs | `auto_approved` |
| `002_new_dependency` | A GPL-3.0 dependency trips the license specialist; scripted **Reject** | `rejected` |
| `003_auth_change` | Autonomous investigation, scripted **Request more info** then **Approve** | `approved` |
| `live` | Live GitHub intake — URL resolved through the MCP server, repo shallow-cloned at `head_sha`; scripted **Approve** | depends on the pull request |

**If it fails:** an authentication error means `ANTHROPIC_API_KEY` is unset or invalid; a run with
no policy citations points at the change-type mapping in `data/policies/index.yaml`. Both are
covered in [`../operations/OP-05-incident-runbook.md`](../operations/OP-05-incident-runbook.md).

> The live beat defaults to a known public pull request. It is skipped without
> `GATE_GITHUB_TOKEN`, so the script still runs offline against the three fixtures. To review a
> URL interactively instead of on the command line, use the UI's second input mode — see
> [`HT-01-review-a-pull-request.md`](HT-01-review-a-pull-request.md).

## Related

The same three fixtures can be driven through the UI instead — start at
[`HT-01-review-a-pull-request.md`](HT-01-review-a-pull-request.md). The UI path lets you make the
human decisions yourself; the demo script scripts them.

---

[← HT-02 Decide at the human gate](HT-02-decide-at-the-human-gate.md) · [Guide index](../index.md) · [HT-04 Read a verdict →](HT-04-read-a-verdict.md)
