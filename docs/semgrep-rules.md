# Semgrep ruleset — design note

What semgrep is, how the rules in [`rules/gate-policy.yaml`](../rules/gate-policy.yaml) are built,
and how to add one without breaking the investigator's guarantees. Written after implementing
ITER_04_v2.

This is an engineering note, not part of the user/operator guide in [`guide/`](guide/index.md).
Operator-facing semgrep settings live in
[`guide/operations/OP-02-configure.md`](guide/operations/OP-02-configure.md); the sandbox and
budget contract is in [`guide/reference.md`](guide/reference.md).

---

## 1. What semgrep is

**Grep that understands code.** You give it a pattern; it returns the places that match. The
difference is that grep compares characters and semgrep compares *syntax trees* — it parses the
file first, so a comment is a comment and a string is a string, and neither can match a code
pattern.

The gate's investigator already had three text-level tools (`read_file`, `grep_repo`, `list_dir`).
For a **security and licensing** gate, findings that are only as good as a guessed regex are thin
evidence. Semgrep is the structural tool that replaces the guess.

Concretely, on a file containing a commented-out call, a docstring mentioning the call, and two
real calls (one spanning five lines):

| | `grep -n "requests\.post"` | `semgrep -e 'requests.post(...)'` |
|---|---|---|
| The comment | matched | ignored |
| The string literal | matched | ignored |
| The one-line call | matched | matched |
| The five-line call | matched **line 1 only** | matched, all five lines |

Four hits, two of them false and one of them a fragment — against two complete, correct matches.

## 2. Pattern syntax

**The pattern is written in the target language.** That is the whole idea. To find
`requests.post("https://a.example")`, the pattern is that line. Two wildcards make it general:

| Syntax | Means | Example |
|---|---|---|
| *(literal code)* | match exactly this | `requests.post("https://a.example")` |
| `...` | anything here | `requests.post(...)` — any arguments, any line count |
| `$NAME` | capture whatever is here | `requests.$METHOD(...)` — matches `.post`, `.get`, … |

A metavariable is `$` plus uppercase. It matches one slot and remembers what it matched, so a
later clause can constrain it.

Explore ad hoc with `-e` before committing anything to a rule file:

```bash
uv run semgrep --lang python -e 'requests.$M(...)' path/to/file.py
```

## 3. Rule anatomy

A rule file is YAML. Five fields make a complete rule:

```yaml
rules:
  - id: gate-sec-01-new-network-egress   # unique; appears as `check_id` in JSON output
    languages: [python]                  # which parser to use
    severity: WARNING                    # ERROR | WARNING | INFO
    message: >-                          # what a human reads; leads with the policy id
      SEC-01: new outbound network call. ...
    pattern: requests.$METHOD(...)       # or `patterns:` / `pattern-either:` — see below
```

Two conventions in this repo, neither of which semgrep imposes:

- **`message` leads with the policy id** (`SEC-01: …`). This is what makes a hit *citable* rather
  than generic lint noise — a specialist can point at the policy document the rule stands for.
- **`metadata.policy_id`** carries the same id as a structured field. Semgrep passes `metadata`
  through untouched; it exists for machine use, the message prefix for the model and the reviewer.

`severity` uses semgrep's vocabulary, which
[`agents/tools/semgrep.py`](../src/gate/agents/tools/semgrep.py) maps onto the gate's:
`ERROR → block`, `WARNING → warn`, `INFO → info`. Anything unrecognized becomes `info` rather than
being dropped — a hit the reviewer cannot see is worse than a mis-ranked one.

> The CLI's `❰❰ Blocking ❱❱` label is a semgrep-CI concept and is **not** the gate's `block`
> severity. `_shape()` reads `extra.severity` from the JSON and ignores the CLI label entirely.

## 4. Combining patterns

One pattern is rarely specific enough. Four combinators narrow it:

| Key | Means |
|---|---|
| `pattern:` | a single pattern |
| `patterns:` | **AND** — every item must hold |
| `pattern-either:` | **OR** — any item may hold |
| `pattern-not:` | **subtract** these matches |
| `metavariable-regex:` | apply a regex to *one captured metavariable* |

The last one is the key move: **semgrep finds the structure, a regex filters the captured piece.**
You are not regexing the file — you are regexing one identifier that semgrep already proved sits
in the position you care about.

### Worked example: narrowing 4 matches to 1

Against a file containing `DOCS = "…"`, `API_KEY = "sk-live-…"`, `API_KEY_ENV = "MY_APP_API_KEY"`
and `password = ""`:

**Step 1 — shape only.** 4 matches: all four assignments.

```yaml
pattern: $NAME = "..."
```

**Step 2 — constrain the captured name.** 3 matches: `DOCS` drops out.

```yaml
patterns:
  - pattern: $NAME = "..."
  - metavariable-regex:
      metavariable: $NAME
      regex: (?i).*(api_?key|secret|password|token).*
```

**Step 3 — subtract the false positives.** 1 match: only the real leaked credential.

```yaml
patterns:
  - pattern: $NAME = "..."
  - metavariable-regex:
      metavariable: $NAME
      regex: (?i).*(api_?key|secret|password|token).*
  - pattern-not: $NAME = ""                # an empty string is not a secret
  - metavariable-regex:
      metavariable: $NAME
      regex: ^(?!_?[A-Z_]*_ENV$).*         # API_KEY_ENV holds a var *name*, not a value
```

That 4 → 3 → 1 progression is what rule authoring actually is: start with the shape, add filters,
subtract the conventions your codebase uses legitimately.

## 5. The gate's four rules, read out loud

| Rule id | Policy | Severity → gate | What it means |
|---|---|---|---|
| `gate-sec-01-new-network-egress` | SEC-01 | `WARNING` → `warn` | a call on `requests`, `httpx`, `urlopen`, or `socket.create_connection` |
| `gate-sec-02-hardcoded-secret` | SEC-02 | `ERROR` → `block` | a credential-named variable assigned a string literal, excluding `""` and the `*_ENV` convention |
| `gate-sec-03-auth-check-removed` | SEC-03 | `ERROR` → `block` | a function whose name suggests auth and whose body is `return True` |
| `gate-data-03-pii-reaching-a-logger` | DATA-03 | `ERROR` → `block` | a call on a logger-named object where an argument is named like PII |

`gate-sec-03` is the one worth studying — it shows a *multi-line* pattern matching a whole function
definition, which no regex can express:

```yaml
    patterns:
      - pattern-either:
          - pattern: |
              def $FUNC(...) -> bool:
                  return True
      - metavariable-regex:
          metavariable: $FUNC
          regex: (?i).*(auth|verify|validate|signature|permission|allowed).*
```

Read it as: *a function returning `bool` whose entire body is `return True`, whose name mentions
auth/verify/validate/…* — that is "someone disabled the access-control check", stated
structurally.

## 6. Verified example: fixture 003

`data/sample_prs/003_auth_change/` exists partly so the ruleset fires deterministically without
needing a live PR. Its `repo/src/acme/audit.py` ships the raw email to a logger and posts it to an
external collector.

```bash
uv run semgrep --config rules/ --json --quiet --metrics=off --disable-version-check \
  data/sample_prs/003_auth_change/repo
```

Two hits, both in `audit.py` (verified against semgrep 1.172.0):

| Rule | Line | Semgrep severity | Gate severity |
|---|---|---|---|
| `gate-data-03-pii-reaching-a-logger` | 22 | `ERROR` | `block` |
| `gate-sec-01-new-network-egress` | 23–27 | `WARNING` | `warn` |

The second match spans five lines — the `requests.post(...)` call and all its arguments — which is
the multi-line behaviour a line-based grep cannot reproduce.

## 7. How a hit reaches the model

```
argv → subprocess → exit code → json.loads → _shape() → render() → tool result
```

1. **Fixed argv**, never `shell=True`, with `--config <absolute resolved rules dir>`.
2. **Exit code check.** `_OK_RETURN_CODES = (0, 1)`. Semgrep exits **1 when it finds something**
   and 0 when it does not; both are successful runs. Treating 1 as failure is the classic mistake.
3. **`_shape()`** keeps five fields per hit — `check_id`, `severity`, `path` (relative to the
   workspace root), `start.line`, `message` — and discards the rest. Raw semgrep JSON is large and
   mostly profiling and fingerprint metadata; feeding it in whole would spend the agent's turn
   budget on noise.
4. **`render()`** produces the text the model actually reads, capped at `GATE_SEMGREP_MAX_RESULTS`
   with an explicit `[... N further results omitted ...]` line. The caps *are* the context
   strategy — there is no summarizer:

```
block gate-data-03-pii-reaching-a-logger src/acme/audit.py:22 — DATA-03: a PII-shaped value is passed to a logger. …
warn gate-sec-01-new-network-egress src/acme/audit.py:23 — SEC-01: new outbound network call. …
```

Shaped hits also land in `InvestigationReport.static_analysis`, and `semgrep_runs` records that a
scan happened at all — `static_analysis == []` cannot distinguish "scanned, clean" from "never
scanned", and that distinction is exactly what a reviewer needs.

## 8. Constraints that are deliberate

- **Never `--config auto`.** It fetches rules over the network at run time, which breaks the
  harness's no-network sandbox *and* makes results non-deterministic between runs. The resolved
  local directory is the only permitted config.
- **The config path is absolute**, resolved through [`paths.py`](../src/gate/paths.py). A relative
  `rules/` re-introduces the CWD assumption the asset resolver exists to kill.
- **A tool inside the agent loop, not a graph node.** LangGraph still sees one `investigate` node;
  the Agent SDK owns the inner loop. A fifth graph node would have blurred that.
- **Semgrep is not exempt from the tool-call budget** just because it is deterministic
  (`tracker.tool_calls += 1` before the scan runs).
- **A failed run still counts** (`tracker.semgrep_runs += 1` before the scan). Otherwise a broken
  scanner is free to retry, and the agent — told the tool is available — burns its turn budget on
  it instead of falling back to `read_file`.
- **The order of the guardrails is itself the contract**
  ([`agents/investigator.py:197`](../src/gate/agents/investigator.py)): budget check,
  `tool_calls += 1`, run cap, sandbox path check, `semgrep_runs += 1`, scan. Three properties come
  from the order alone — a *refused* call is not counted, so `tool_calls` can never exceed its cap;
  an over-budget scan attempt still costs a tool call; and a path outside the sandbox costs a tool
  call but not a scan.
- **Every failure is a tool *result*, never an exception.** `semgrep unavailable: <reason>` covers
  a missing binary, an unsupported event loop, a missing ruleset directory, a timeout, a non-zero
  exit, and unparseable output. Nothing raises into the graph; failure lands on a human at the
  gate.

## 9. Adding a rule

1. Explore the shape with `-e` against a file you know should match.
2. Narrow it: add `metavariable-regex` filters, then `pattern-not` for conventions your corpus
   uses legitimately (see §4).
3. Give it an `id` prefixed `gate-`, a `severity` from the ERROR/WARNING/INFO set, and a `message`
   **leading with the policy id**, plus `metadata.policy_id`.
4. Confirm the policy id exists as a `##` heading in `data/policies/` — an id no policy document
   defines gives the specialist nothing to cite.
5. Run it against `data/sample_prs/003_auth_change/repo` and at least one file that should *not*
   match. A rule with no negative case is a rule you have not tested.
6. Rules are committed and pinned; there is no runtime rule fetch to update.

## 10. Limitations

- **Python only.** Every rule declares `languages: [python]`. A JavaScript or Go PR scans clean —
  not because it is clean, but because no rule applies to it. Semgrep supports those languages;
  this ruleset does not yet.
- **Name-shaped heuristics, not dataflow.** `gate-data-03` fires on an *argument named* like PII,
  not on a value proven to hold PII. Semgrep has taint mode for real dataflow; none of these rules
  use it. Expect both false positives (a variable called `user_name_hash`) and false negatives (PII
  in a variable called `v`).
- **Four rules.** The ruleset covers three SEC policies and one DATA policy. It is a demonstration
  that the tool is wired correctly and produces citable output, not a comprehensive corpus.
- **`GATE_SEMGREP_MAX_RUNS = 2`.** An investigation cannot scan more than twice, so a very large
  workspace cannot be swept directory by directory.

## 11. The maintenance cost, stated plainly

A hand-written ruleset is a **maintained asset**, not a one-time addition. It goes stale in four
ways: a new policy has no rule, a rewritten policy leaves its rule citing the old wording, a new
HTTP client (`aiohttp`, a vendored wrapper) is invisible to `gate-sec-01`, and a naming convention
the codebase adopts starts producing false positives nobody prunes.

That cost is accepted here for a specific reason: **the rules are the citable half of the
investigator's output.** A model asked to spot problems produces prose; a rule produces
`SEC-01` at `audit.py:23`, which maps to a policy heading a specialist can cite and a reviewer can
check. Determinism is the point — the same PR scans the same way today and next month, which no
prompt guarantees.

The honest framing for this repo's scope: four rules covering four policies, deliberately small,
demonstrating that structural analysis is wired into the agent loop correctly. Scaling that to a
real policy corpus is a staffing question, not a code question. Two ways to reduce the burden if
this were more than a demo:

- **Adopt a maintained registry** (`p/python`, `p/security-audit`) *alongside* the local ruleset
  for generic classes of bug, keeping hand-written rules only for org-specific policy. The cost is
  that registry rules carry no `policy_id`, so their hits are evidence rather than citations — and
  fetching them is a network dependency the sandbox currently forbids, so they would need to be
  vendored and pinned, not fetched at run time.
- **Treat §9's step 5 as a test.** A committed pair of should-match / should-not-match fixtures per
  rule turns silent staleness into a failing check.
