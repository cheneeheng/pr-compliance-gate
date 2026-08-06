# Decision Log — PR Compliance Gate

### Entry 1

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-23
**Task:** Implement all v1 plans (SKELETON + ITER_01..04) into a greenfield repo.

**Context:** The implement-from-plan flow describes SKELETON (scaffold + canned nodes) then
each iteration replacing canned output with real code. The repo is greenfield (no tracked
files). Replaying every iteration literally would mean writing canned stubs and rewriting
them up to four times.
**Decision:** Build each module directly to its final resolved state (through the ITER_04
`mvp: true` terminator), using the iteration specs to determine final behavior. Section scope
and the `depends_on` resolution order are still honored to decide what each file must contain.
**Impact / Risk:** Skips the intermediate "canned but wired" milestone. Low risk — the final
graph structure is identical; only the throwaway stubs are omitted.
**Outcome:** In progress.

### Entry 2

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-23
**Task:** Model IDs and observability defaults.

**Context:** Plan specifies `claude-haiku-4-5` (classifier/risk) and `claude-sonnet-4-6`
(specialists/investigator) as env-driven defaults. Project invariants mandate structlog +
correlation_id, which the plan does not mention but does not contradict (plan keeps "stdout
logging" in MVP scope).
**Decision:** Use the plan's model strings as `.env.example` defaults (env-overridable, per
plan). Add a minimal structlog config and a correlation-id middleware to satisfy the
non-negotiable observability invariant without exceeding plan scope.
**Impact / Risk:** Model ID strings must be verified against the live Anthropic API before a
real run; flagged for the user. Logging addition is small and additive.
**Outcome:** In progress.

### Entry 3

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-23
**Task:** Reducer for `ReviewState.specialist_findings`.

**Context:** ITER_02 §04 specifies `Annotated[list, operator.add]` to merge the parallel
security/license fan-out. But specialists also re-run after each investigation loop. With plain
`operator.add`, round-1 findings (including `needs_more_context=True`) accumulate next to
round-2 findings, so reconcile's rule 3 would keep firing until the loop budget drains and
rule 4 escalates — the "investigation resolved the question → clean verdict" path (rule 6)
becomes unreachable.
**Decision:** Implement `merge_findings(existing, new)` that replaces all findings from a
specialty when that specialty emits a fresh batch. Order-independent across the two parallel
specialists (satisfies the original clobber concern), and a new investigation round cleanly
supersedes the prior one.
**Impact / Risk:** Deviates from the literal `operator.add` in the plan; strictly more correct
for the looping case. No downside for the single-pass case.
**Outcome:** Implemented in `graph/state.py`.

### Entry 4

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-23
**Task:** Which specialist reviews DATA-* (data-handling) policies.

**Context:** The plan defines two specialists (security, license) and three policy areas
(security, licensing, data-handling). ITER_02 §04 maps only `SEC-*`/`LIC-*` slices explicitly;
`DATA-*` is unassigned.
**Decision:** Route `DATA-*` chunks to the security specialist (`security = (SEC, DATA)`,
`license = (LIC,)`). PII/data-handling is a security concern and there is no third specialist.
**Impact / Risk:** Data-handling policy would otherwise go unreviewed. Low risk.
**Outcome:** Implemented in `graph/nodes/specialists.py`.

### Entry 5

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-23
**Task:** Listing reviews + non-blocking graph execution.

**Context:** (a) `GET /api/reviews` must list reviews, but LangGraph's checkpointer has no
first-class "list all threads" call. (b) The SKELETON stub `await graph.ainvoke(...)` inside
`POST /api/reviews` is synchronous, but ITER_04 §05 introduces 2s polling — which only means
anything if the POST returns before the run finishes.
**Decision:** (a) Keep a session-scoped in-memory `OrderedDict` registry (thread_id -> pr_id)
in `app.state.reviews` to back the list view; per-review state/status/risk are read live from
the checkpoint. (b) Launch each graph run (and each resume) as a background `asyncio` task and
return `{thread_id}` / `{status:"resumed"}` immediately, so polling reflects progress.
**Impact / Risk:** The list view is session-scoped (reviews created before a restart won't
appear in the list, though each is still reachable by `thread_id` via its persisted
checkpoint). Background-task exceptions are logged, not surfaced to the client. Both acceptable
for a single-user local demo; flagged for the user.
**Outcome:** Implemented in `api/app.py` and `api/routes.py`.

### Entry 6

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-23
**Task:** Concurrent-write safety on `ReviewState.escalation`.

**Context:** Both specialists run in the same LangGraph superstep. On a double timeout both write
`escalation`; a reducer-less channel raises `InvalidUpdateError` on concurrent writes. The plan
did not mark this field with a reducer.
**Decision:** Add a `coalesce_escalation` first-wins reducer (`Annotated[..., coalesce_escalation]`).
Reconcile only writes escalation when none exists yet, so first-wins is correct everywhere.
**Impact / Risk:** Prevents a latent crash on the double-timeout path. No behavioral change on
the single-write paths.
**Outcome:** Implemented in `graph/state.py`.

### Entry 7

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-24
**Task:** Plan-compliance audit of the v1 family (SKELETON + ITER_01..04) — where to place the
missing ITER_04 §04 test and whether to add the missing ITER_02 §06 `eval_routes.py`.

**Context:** ITER_04 §04 specifies three decision-endpoint tests "(mocked graph)"; the third —
`request_info` at an exhausted loop budget re-interrupts with `max_cycles` — was absent. Written
against the existing `_FakeGraph` it would assert nothing real (the fake never routes). ITER_02
§06 specifies `tests/eval_routes.py`; the repo instead folds route coverage into `eval_demo.py`.
**Decision:** (a) Cover the ITER_04 case in `test_decision_endpoint.py` but against the real
routing functions (`_route_after_gate` -> `investigate` -> `reconcile`) with `run_investigation`
monkeypatched, so the assertion is about the actual resume path, not the fake. (b) Do not add
`eval_routes.py`: `eval_demo.py` runs the same three fixtures end-to-end and asserts terminal
outcomes, which imply the routes; the substitution is already documented in its module docstring.
`expected_route` in each `meta.json` is consequently unread — kept as fixture documentation.
**Impact / Risk:** (a) The test is no longer purely endpoint-level, so an endpoint regression that
bypasses those routing functions would not be caught by it (the 409/404/Command tests still cover
the endpoint itself). (b) One planned eval filename does not exist; route regressions surface as
outcome mismatches instead.
**Outcome:** (a) implemented. (b) **superseded by entry 8** — the user asked for the remaining
open items to be closed, so `eval_routes.py` was written after all.

### Entry 8

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-24
**Task:** Close the two items left open by the v1 plan-compliance audit — reconcile's shadowed
rule 5, and the missing `tests/eval_routes.py`.

**Context:** ITER_02 §04 words rule 5 (disagreement) as severities spanning `info/warn` vs
`block` between specialists on one policy, ordered after rule 2 (any block -> human_gate). Those
two statements are inconsistent: rule 2 escalates every finding set containing a block, so the
rule-5 condition can never be reached, making the branch dead and `EscalationReason.disagreement`
unusable. Either the ordering or the condition has to give.
**Decision:** Keep the plan's ordering (it is stated as deliberate) and widen the condition to
what survives rule 2: two specialists citing the same `policy_id` at *different* severities.
The plan docs are left untouched as historical artifacts; this entry records the divergence.
Also wrote the planned `eval_routes.py` (reversing entry 7b), which asserts each fixture's
`expected_route` at its first resting point — the complement of `eval_demo`'s terminal outcomes.
**Impact / Risk:** More reviews can now escalate as `disagreement`. In practice near-zero change:
the specialists own disjoint policy-id prefixes (`SEC-*`/`DATA-*` vs `LIC-*`), so both citing one
policy is currently unreachable — the rule is there for when the prefix split loosens. Widening
also means a benign info-vs-warn split now stops at a human; that is the intended fail-safe
direction. `eval_routes` adds one more `llm`-marked eval to the paid run.
**Outcome:** Implemented; default suite 27 passed, ruff and mypy strict clean. The `llm` evals
were not run (API spend).

### Entry 9

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-24
**Task:** Split the suite into unit/integration/system and reach 100% coverage of `src/gate`.

**Context:** The plan names test files but not a layout or a coverage bar. Two things blocked a
literal 100%: `intake/retrieval.py` had an unreachable empty-query fallback (`change_type` is a
non-empty enum value, so the query is never empty), and `ingest.py`'s `__main__` block cannot run
under pytest. Reaching the bar also required stubbing the LLM/SDK boundary everywhere.
**Decision:** Delete the dead fallback rather than pragma around it; exclude `if __name__ ==
"__main__":` and `if TYPE_CHECKING:` via `[tool.coverage.report] exclude_also` (they are not
runtime paths). Put the coverage gate on the default suite only (`fail_under = 100`), with the
`llm`-marked `system/` evals excluded — paid tests must never be load-bearing for coverage.
Classify by what a test touches: `unit` = no I/O, no graph; `integration` = real components with
only the LLM/SDK boundary faked; `system` = the real pipeline against the real API.
**Impact / Risk:** A new branch in `src/gate` now fails CI until it is tested, which is the point
but does add friction. Coverage of the SDK loop comes from a stubbed `query`, so it proves the
harness contract (metrics, failure containment), not the SDK's real behavior — `eval_investigation`
remains the only thing that exercises that.
**Outcome:** 97 tests, 100% statement and branch coverage, ruff and mypy strict clean. Found and
fixed a real bug on the way: `langchain-text-splitters` was imported by `intake/ingest.py` but
never declared, so `uv run python -m gate.intake.ingest` failed on a clean install.

### Entry 10

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-26
**Task:** Make the repo's public text discoverable (README first screen, package metadata, GitHub repo description and topics).

**Context:** Three surfaces carried three different pitches: an empty GitHub repo description, a
generic `project.description` ("Agentic pipeline that reviews pull requests against organizational
policy before merge"), and a README opening paragraph that named neither LangGraph nor the Claude
Agent SDK — the two terms someone would actually search. Packaging is explicitly out of MVP scope,
which makes it ambiguous how much PyPI metadata is warranted.
**Decision:** Wrote one canonical one-liner and used it verbatim on all three surfaces. Added
`project.keywords`, but deliberately no trove classifiers — classifiers only pay off on a published
package, and this one is not published. Folded the `## Setup` / `## Run` sections into the first
screen so the install command appears above the fold instead of duplicating it.
**Impact / Risk:** Renaming/removing the `Setup` and `Run` headings breaks any external anchor
links to `#setup` / `#run`; no in-repo links pointed at them.
**Outcome:** README and `pyproject.toml` updated. The `gh repo edit` call for the description and
topics failed with HTTP 403 (the PAT lacks repo-admin scope); the user applied that metadata by
hand. Branch named `chore/prep-pypi-release` — the user reframed this work as the first step
toward an actual PyPI release, which reopens the classifiers decision above whenever that release
is cut.

### Entry 11

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-26
**Task:** Make the repo actually publishable to PyPI (metadata, license, installed-package correctness, workflows).

**Context:** Entry 10 left packaging half-declared: keywords but no classifiers, no license of any
kind, and README text asserting the project is not published. Four things were genuinely
unresolved. (1) Licensing — a public, hard-to-reverse legal choice, so this one was asked rather
than assumed; the user chose Apache-2.0. (2) `gate.paths` anchored every asset directory at
`Path(__file__).parents[2]`, which is the repo root in a checkout and the directory above
`site-packages` in a wheel — a `pip install` of this package could not find `data/` or `static/`,
so ingest, the fixtures, and the UI would all fail. (3) Whether to add a console-script entry
point. (4) Whether CI should enforce `ruff format`.
**Decision:** (1) Apache-2.0 via PEP 639 (`license = "Apache-2.0"` + `license-files`), which
requires `hatchling>=1.27`, and no `License ::` trove classifier — PEP 639 supersedes it.
(2) Bundle `data/` and `static/` into the wheel via hatch `force-include` to `gate/_bundled/`, and
have `paths.py` prefer that copy, falling back to the repo root; `REPO_ROOT` renamed `ASSET_ROOT`
because it is no longer a repo root in the installed case. Writable state (`checkpoints.db`,
`.chroma/`) stays CWD-relative — an installed package must not write into `site-packages`.
(3) No console script: `python -m uvicorn gate.api.app:app` already works, and a new module would
need new tests to hold the 100% coverage gate for no functional gain. (4) CI first shipped without
`ruff format --check` — ruff 0.16 reformats 9 existing files, and an unrequested formatting sweep
was out of scope. The user then asked for a pre-commit hook running exactly that, which makes the
sweep necessary (a hook that fails on the first commit is useless), so the 9 files were formatted
and the CI check restored. The hook is the `pre-commit` framework pinned to `ruff-pre-commit`
v0.16.0 rather than a hand-written `.git/hooks/pre-commit`, because a shell script in `.git/` is not
version-controlled and so never reaches a contributor. Note the asymmetry: the hooks match only
py/pyi/ipynb, while CI's `ruff format --check .` also covers Python blocks inside markdown.
Version: bumped to 0.2.0 first, on the reasoning that installability is a new capability; the user
overrode this to 0.1.1 since no pipeline feature changed. v0.1.0 is tagged but was never published
and would install broken, so 0.1.1 is the first PyPI artifact.

Mid-task the user supplied a `publish.yml` template from another project and asked for both PyPI and
TestPyPI. Adopted it (release-published -> PyPI, workflow_dispatch -> TestPyPI) over the
tag-triggered single-target workflow first written, fixing the template's hardcoded
`mcp_cassette-` filename in its tag-vs-version guard and adding the dependency audit.

**Impact / Risk:** `pip-audit` of the locked runtime set reports PYSEC-2026-311 / CVE-2026-45829
against every `chromadb>=1.0.0` with no fixed release: pre-auth RCE in the Chroma *server*'s
collections endpoint. This project embeds Chroma through `persist_directory` and never runs the
server, so the vulnerable endpoint does not exist here; the publish workflow ignores that one ID by
name, with the reasoning inline. Revisit when chromadb ships a fix. Publishing also depends on
one-time out-of-band setup (Trusted Publishing publishers plus `pypi`/`testpypi` GitHub
environments) that no repo file can assert.
**Outcome:** `uv build` produces both distributions; extracting the wheel and importing
`gate.paths` off it resolves the policy corpus, all three fixtures, the investigator sandbox, and
`index.html` from `gate/_bundled/`. The sdist excludes `.agents_workspace/`. 97 tests pass at 100%
statement and branch coverage; ruff check and mypy strict clean.

### Entry 12

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-27
**Task:** Pre-release audit of the whole repo, ahead of the first PyPI publish.

**Context:** The audit turned up four defects and several judgement calls the user had not
scoped. The defects: `pytest -m llm` selected zero tests (the evals are `eval_*.py`, which
pytest's default `python_files` never matches, so all four modules were uncollectable while three
documents advertised the command); `pr_id` reached the filesystem unvalidated from the request
body; three UI click targets were `<a>` without `href`, so keyboard-unreachable; and the install
guide sent a `pip install` reader to a step that copies a file no wheel ships.

**Decision:** Fixed all four. `pr_id` validation went into `loader.load_pr` rather than a
constrained field on the `StartReview` request model, because `load_pr` is also the intake node's
entry point and the value flows on to `paths.repo_snapshot_dir` as the investigator's sandbox
root — validating at the single chokepoint covers the graph path too, and reuses the existing
`FixtureError` -> 422 mapping instead of adding a second error shape. The pattern forbids `.`
outright so `..` cannot be expressed at all.

Version: folded into 0.1.1 rather than cutting 0.1.2, since v0.1.1 is prepared on this branch but
neither tagged nor published — there is no released artifact to supersede.

Deliberately **not** changed, and reported to the user instead: the `GATE_MODEL_SPECIALIST` /
`_INVESTIGATOR` default `claude-sonnet-4-6`, which is unverifiable from here and is already
flagged in Entry 2, the README, and `21-configure.md`; the absence of rate limiting and auth on
the mutation endpoints, which the plan puts out of MVP scope and the docs state explicitly; and
in-flight graph tasks not being drained at lifespan shutdown.

**Impact / Risk:** Collecting the evals means `pytest -m llm` now bills real API calls where it
previously did nothing — intended, and the default run is unaffected (`-m 'not llm'` deselects
all 10). `load_pr` rejecting non-plain ids is a behaviour change for any caller that passed a
path; no shipped fixture id contains a `.` or separator. `gate.__init__` now reads distribution
metadata at import, so `import gate` requires the package to be installed rather than merely on
`PYTHONPATH` — true of every documented install path.
**Outcome:** 103 tests pass at 100% statement and branch coverage; ruff, `ruff format --check`,
and mypy strict clean. Wheel rebuilt and re-verified from a throwaway venv: `gate.__version__`
reports 0.1.1, the bundled fixtures load, and the three traversal payloads are rejected.
`pip-audit` of the locked runtime set still reports only the known chromadb PYSEC-2026-311.

### Entry 13

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-07-28
**Task:** Hold the PyPI publication, and make the README pipeline diagram legible.

**Context:** The user decided not to publish to PyPI for now — the package should only go there
once it is usable for productive work, e.g. with live GitHub intake — and asked for the note to
land in the README, the changelog, and the open PR. Ambiguity the user did not resolve: whether
holding publication means unwinding the packaging work on `chore/prep-pypi-release` (metadata,
`publish.yml`, the 0.1.1 bump, the bundled-asset fix).

**Decision:** Kept all of it and reframed it as "release-ready, deliberately unpublished". The
asset-bundling fix is a real bug fix independent of publishing, `publish.yml` cannot fire on its
own (only `workflow_dispatch` or a published release), and the version bump documents a real
packaging change. Reverting would have to be redone verbatim when live intake lands. The hold is
recorded in three places with the unblocking condition stated, plus the operator install guide,
which told readers to run `pip install pr-compliance-gate` — a command that would now 404.

Pipeline diagram: replaced the ASCII block with a Mermaid `flowchart` colour-coded by spend
(green = Haiku/retrieval/pure-Python routing, orange = Sonnet specialists and the Agent SDK
investigator, blue = the human gate), with the two cost gates as edge labels. Mermaid over ASCII
because GitHub renders it natively; the earlier argument for ASCII — PyPI's long-description
renderer does not do Mermaid — is moot now that the README is not shipping to PyPI.

**Impact / Risk:** Docs only; no source, test, or workflow change, so the coverage gate and CI are
unaffected. Risk is that the Mermaid block renders only on surfaces that support it — GitHub does;
a plain-text reader now sees diagram source instead of an ASCII sketch.
**Outcome:** README, CHANGELOG, `docs/guide/operations/20-install.md`, and PR #2 (title and body)
updated.

**Amendment (same day):** the reasoning above — that `publish.yml` needs no change because it only
fires on `workflow_dispatch` or a published release — was not enough for the user, who asked for it
disabled outright. Done by renaming it to `publish.yml.disabled`: GitHub registers only `.yml` and
`.yaml` under `.github/workflows`, so the file is inert while staying reviewable, diffable, and one
`git mv` from working. Chosen over deleting it (the work would have to be redone), over stripping
the triggers (a workflow with no `on:` is invalid YAML for Actions), and over `gh workflow disable`
(that state lives in the GitHub UI, not the repo, so it would not survive a fork or a re-clone and
would be invisible in review).

---

### Entry 14

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-08-06T00:00:00Z
**Task:** Expose reasoning effort as an env var, withheld from Haiku; move to Sonnet 5.

**Context:** Three forks the request left open. (1) `langchain-anthropic` turns on adaptive
thinking whenever `reasoning_effort` is set on a model whose profile declares `xhigh` — Sonnet 5
does — and it passes `temperature` straight through. `_chat` pinned `temperature=0.0`, and a
non-default temperature alongside thinking is the combination the API has historically rejected.
(2) Effort could be one global knob or one per tier. (3) `ClaudeAgentOptions` also takes an
`effort` field, so the investigator's Agent SDK loop could carry the same setting.

**Decision:** (1) Send no temperature when effort is applied, letting the API use its default,
and keep `temperature=0.0` on the tiers that take no effort. Disabling thinking instead would
have preserved determinism but reduced effort to an output-token cap, defeating the point of the
setting. (2) One `GATE_MODEL_EFFORT`, keyed on the configured model id rather than on the tier,
so pointing any tier at Haiku withholds it and pointing any tier at Sonnet opts it in — a single
knob matches the request and the id test needs no table of model families. (3) Applied to the
chat-model factory only, which is what was asked; the Agent SDK loop is a different API surface
and stays untouched pending a decision.

**Impact / Risk:** Specialist and investigator wording is no longer reproducible run to run,
because those tiers now run at the API default temperature. Routing is unaffected — `reconcile`
is plain code. The effort-plus-temperature behaviour is **unverified against the live API**: the
default suite makes no API calls, so this was checked only at the point where the request payload
is built. `claude-sonnet-5` pricing in `PRICE_TABLE` was carried over from `claude-sonnet-4-6`
unverified; it matches the existing fallback, so the displayed estimate is unchanged either way.

**Outcome:** 105 tests pass at 100% coverage; `mypy --strict` and `ruff` clean. No live-API
run. Decisions (2) and (3) were **superseded on 2026-08-06 by explicit user instruction**:
effort is now per tier (`GATE_MODEL_<TIER>_EFFORT`, paired with `GATE_MODEL_<TIER>`), and
the Agent SDK tool loop carries the investigator tier's effort via `ClaudeAgentOptions`.
Decision (1), the temperature handling, still stands and is still unverified against the
live API.

---

### Entry 15

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-08-06T00:00:00Z
**Task:** Fix the specialist `ValidationError` introduced by the effort setting.

**Context:** With an effort set, `security_specialist` failed with
`_FindingList.findings: Input should be a valid list, input_value='{"findings": []}'`. Cause, from
`langchain_anthropic/chat_models.py`: an effort turns on adaptive thinking; the API rejects
`tool_choice` that forces tool use while thinking is on, so `with_structured_output`'s default
`function_calling` method drops the forcing (line 2025 and
`_get_llm_for_structured_output_when_thinking_is_enabled`) and the model is free to answer in
prose. The parser then fed the raw JSON text into the schema's single field.

**Decision:** Select the structured-output method from the effort: `json_schema` (Claude's
dedicated structured output, which thinking does not restrict) when an effort is set, and the
existing `function_calling` otherwise. Rejected two alternatives. Sending
`thinking={"type": "disabled"}` would have restored the forced tool call and temperature 0, but
reduces effort to an output-token cap and defeats the setting. Switching every tier to
`json_schema` would have changed the Haiku path too, which works today and whose structured-output
support I cannot verify without a live call.

**Impact / Risk:** Haiku tiers are byte-for-byte unchanged. `json_schema` on `claude-sonnet-5` is
**unverified against the live API** — it is the documented path for that model, but the default
suite makes no API calls, so this was checked only at the point where the method is selected. If
it fails, the fallback is to disable thinking as described above.

**Outcome:** 107 tests pass at 100% coverage; `mypy --strict` and `ruff` clean.
