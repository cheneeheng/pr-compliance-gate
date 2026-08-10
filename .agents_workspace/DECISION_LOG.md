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

### Entry 16

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-08-07T00:00:00Z
**Task:** Green the CI on PR #3 (release v0.1.2), which failed the 100% coverage gate.

**Context:** `uv run pytest --cov` passes at 100% on Windows but reports 99.80% on Linux CI. The
single missed line is `agents/investigator.py:254`, the `isinstance(loop, SelectorEventLoop)`
check inside `_loop_can_spawn_subprocess`, which sits behind an early `sys.platform != "win32"`
return. On Windows the line is reached incidentally by the harness tests; on Linux nothing can
reach it. Coverage was therefore host-dependent, and the gate only noticed once CI actually ran.

**Decision:** Add a unit test that fakes the platform (`monkeypatch` on `investigator.sys.platform`
= `"win32"`) and stubs `asyncio.get_running_loop` with a `Mock(spec=asyncio.SelectorEventLoop)`, so
the win32 branch executes on every host. Rejected `# pragma: no cover` and a coverage exclusion:
both hide a real branch of a documented platform workaround, and the existing sibling test already
pins the POSIX direction the same way — this makes the pair symmetric.

**Impact / Risk:** Test-only change; `src/` untouched. The stub asserts on type identity rather
than real loop behaviour, so it pins the contract of the check, not that a real Windows
`SelectorEventLoop` fails to spawn — that remains covered only by the comment and the upstream
uvicorn source cited in the docstring.

**Outcome:** 114 passed, 100% statement and branch coverage locally; ruff check and format clean.
Not yet confirmed on Linux CI — the fix is uncommitted at the time of writing.

### Entry 17

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-08-08T00:00:00Z
**Task:** Implement the v2 plan family (ITER_01_v2 … ITER_04_v2).

**Context:** ITER_01_v2 §04 states "`intake/store.py` is unaffected. It is the session-scoped
thread registry backing `GET /api/reviews` — naming coincidence only." That is wrong about the
shipped code: `intake/store.py` is the Chroma vector-store accessor, and the thread registry is
`app.state.reviews` (an `OrderedDict` created in the lifespan). Following the plan literally would
have left a module importing `langchain_chroma` after its only dependency was removed.

**Decision:** Deleted `intake/store.py` along with `intake/ingest.py`. The registry the plan meant
is `app.state.reviews`, which was extended in ITER_03_v2 with a `Registration` model carrying
`source` and `pr_ref` — the behaviour the plan asked for, in the place it actually lives.

**Impact / Risk:** None beyond the intended Chroma removal. The plan's `store.py` paragraph is now
inaccurate as written; the code is what it describes in intent.

**Outcome:** `uv run pytest --cov` green at 100% statement and branch coverage; ruff and
`mypy --strict` clean.

### Entry 18

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-08-08T00:00:00Z
**Task:** Shape the `PRSource` protocol (ITER_03_v2 §04).

**Context:** The plan specifies `resolve(ref_input) -> PRRef | None` and `load(ref) -> PRPayload`.
`FixturePRSource.load` has no `PRRef` to work from — fixtures return `None` from `resolve` — so a
literal `load(ref)` would need the adapter instance to carry the fixture id across the
handler-to-background-task boundary as hidden state.

**Decision:** `load(ref_input, ref)` — both methods take the original reference input. The
two-method split the plan is actually arguing for (cheap/HTTP-mapped vs. slow/escalating) is
unchanged; the adapters just stay stateless, so nothing half-resolved has to survive the hop.

**Impact / Risk:** Deviates from the plan's exact signature. `GitHubMCPSource.load` re-resolves
when handed `ref=None`, which costs one extra MCP round trip in a path the API never takes.

**Outcome:** Both adapters satisfy the protocol; `mypy --strict` clean.

### Entry 19

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-08-08T00:00:00Z
**Task:** Install the tracer provider (ITER_02_v2 §04).

**Context:** `GATE_OTEL_ENABLED=false` was implemented as `trace.set_tracer_provider(NoOp...)`, per
the plan's "installs a no-op provider". A test caught that this silently does nothing: OTel guards
that setter with a process-wide `Once`, so the second call in a process — the kill switch after any
enabled run, or a second app instance — is refused with a log warning and no effect.

**Decision:** `gate.telemetry.tracing` holds its own `_provider` and `tracer()` reads it, falling
back to the OTel global only when unset. `trace.set_tracer_provider` is still called for
third-party instrumentation that reads the global, but nothing here depends on it succeeding.

**Impact / Risk:** The gate's spans no longer follow a provider installed by unrelated code in the
same process. That is the intended trade: the kill switch has to actually work.

**Outcome:** `test_the_kill_switch_installs_a_no_op_provider` passes; the whole suite runs in ~12s
instead of ~67s once `conftest.py` also disabled tracing by default.

### Entry 20

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-08-08T00:00:00Z
**Task:** Package semgrep (ITER_04_v2 §03).

**Context:** The plan calls for an exact pin and says to move it to an optional `[semgrep]` extra
only if the pinned version lacks a wheel for a supported interpreter. semgrep 1.172.0 does publish
a `win_amd64` wheel and supports Python ≥3.10, so the stated fallback condition does not fire.

**Decision:** Shipped it as an optional extra anyway (`uv sync --extra semgrep`). It is a large
install, and the harness already degrades cleanly without it — the same code path that covers a
missing binary. `uv sync` therefore stays light and every semgrep branch is unit-tested against a
stubbed subprocess, so no test depends on the extra being installed.

**Impact / Risk:** A default install has an investigator whose fourth tool reports
`semgrep unavailable`. Documented in the README install section and OP-01.

**Outcome:** Rules verified to fire on fixture 003 with the extra installed; the full default suite
passes with or without it.

### Entry 21

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-08-09T00:00:00Z
**Task:** Plan-compliance audit of the v2 family (ITER_01_v2 .. ITER_04_v2); interpret "rule count"
on the semgrep tool span (ITER_04_v2 §04).

**Context:** Three ambiguities surfaced while closing audit findings.

1. ITER_04_v2 §04 says each semgrep run is "a child span ... carrying `tool.name=run_semgrep`,
   rule count and duration". "Rule count" could mean the size of the committed ruleset (a
   constant, useless on a span) or the number of hits the run returned.
2. ITER_03_v2 §06 says the per-file-aware truncation is what "the classifier and specialists"
   see. `risk_score` also feeds the diff to a model but is not named.
3. Restructuring `submit_decision`'s registration lookup from a ternary into an if/else exposed
   that the *registered* branch of that lookup has never been exercised by a test — the ternary
   form hid it from coverage.py, which measures no arc for a conditional expression.

**Decision:** (1) Recorded the hit count as `gate.semgrep.rule_hits`; duration comes free from the
span. (2) Switched only `classify` and the specialists to `truncate_diff_by_file`, leaving
`risk_score` on the blunt head+tail cut, because the spec named two nodes and minimal-diff bias
says not to widen it. (3) Kept the ternary form rather than adding an unrequested test, so the
`fail_under = 100` gate reflects the same coverage posture it did before this audit.

**Impact / Risk:** (2) leaves one inconsistency: a fixture diff over `GATE_MAX_DIFF_CHARS` would
still be sliced mid-hunk for the risk scorer only. No shipped fixture is near the budget. (3)
leaves a real test gap standing — a decision submitted for a *registered* thread never runs that
half of the lookup — and 100% coverage does not currently mean that path is proven.

**Outcome:** `ruff`, `mypy --strict` and the full default suite pass at 100% statement and branch
coverage.

---

### Entry 22

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-08-09T00:00:00Z
**Task:** Second plan-compliance audit of the v2 family, then bring `examples/demo.py` up to v2

**Context:** `src/gate` audited clean against ITER_01_v2..ITER_04_v2. The one real gap was outside
`src/`: `examples/demo.py` still invoked the graph with a bare `initial_state(pr_id, thread_id)`.
ITER_03_v2 §04 moved loading *before* `ainvoke` — the `intake` node now fetches nothing — so the
demo was reviewing an empty diff. `examples/` is excluded from the coverage source, so the default
suite could not see it. Three sub-choices had no obvious answer.

**Decision:**
1. *Beat 4 lives in the demo script, skipped without a token.* ITER_04_v2 §04 specifies four
   beats and the README claims each maps to a section of the demo's output; leaving beat 4
   UI-only would keep that claim false. It is skipped with a printed reason when
   `GATE_GITHUB_TOKEN` is unset, so the default run stays offline and deterministic — the same
   posture `tests/system/eval_demo.py` takes.
2. *A throwaway `FastAPI()` satisfies `init_tracing`.* The instrumentor needs an ASGI app. The
   alternative — making `app` optional in `telemetry/tracing.py` — adds a branch to `src` under
   `fail_under = 100` for a script's convenience. Two lines in the example beat a new untested
   branch in the library.
3. *State seeding is duplicated, not extracted.* The seven-line block that copies a `PRPayload`
   onto `ReviewState` now exists in both `api/routes.py::start_review` and the demo. Extracting a
   shared helper is the cleaner shape but is a `src` refactor the task did not ask for. The demo
   comments point at `routes.py` so the drift is at least signposted.

**Impact / Risk:** (3) is the live risk — a future change to the seeded field set must touch two
places. Small and named. (1) means a first-time reader without a GitHub token sees three beats and
a skip line, which is the honest picture.

**Outcome:** Demo verified to load a real fixture payload (1484-char diff, two changed files, a
workspace path) where the previous version passed an empty diff. Not run end to end — that spends
API credit.

---

### Entry 23

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-08-10T00:00:00Z
**Task:** Third plan-compliance audit of the v2 family (ITER_01_v2 .. ITER_04_v2).

**Context:** `src/gate` audited clean again. Two gaps sit outside it, and neither has an
implementation answer that is obviously right.

1. ITER_04_v2 §04 specifies `rules/` as "a handful of registry rules vendored locally plus two or
   three hand-written rules". Only the hand-written half shipped.
2. ITER_04_v2 §04 says each README demo beat "links a trace screenshot". None does.

**Decision:** Left both, recorded as BACKLOG B-03 and B-04 rather than closed silently.
(1) Vendoring semgrep-registry rules imports LGPL-2.1 content into an Apache-2.0 repo — a licence
decision for a human, and the same spec paragraph argues *against* the generic lint noise those
rules are. (2) A screenshot needs a live Langfuse container and a paid end-to-end run; fabricating
one would misrepresent the artifact. Fixed only the one in-scope defect found: a comment in
`static/index.html` describing the trace link's omit-when-disabled behaviour (ITER_02_v2 §05) sat
above `prTitleLink` instead of `traceLink`.

**Impact / Risk:** (1) leaves the investigator's detection limited to four hand-written rules;
fixture 003 fires two of them, so the demo beat is unaffected. (2) leaves the README's "proven,
not asserted" claim resting on the reader running the demo.

**Outcome:** `ruff`, `mypy --strict` and the default suite pass — 226 passed, 100% statement and
branch coverage of `src/gate`.

---

### Entry 24

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-08-10T00:00:00Z
**Task:** Fourth plan-compliance audit of the v2 family (ITER_01_v2 .. ITER_04_v2).

**Context:** Two items needed a call rather than an edit.

1. ITER_04_v2 §03/§04 say CI "installs the pinned `semgrep` wherever the interpreter supports
   it". `.github/workflows/ci.yml` synced only `--extra dev`, so no leg ever installed it. The
   pin (1.172.0) ships cp312/cp313/cp314 wheels, so both matrix legs support it.
2. BACKLOG B-01: ITER_03_v2 §06 introduced per-file-aware truncation naming only "the classifier
   and specialists", so `risk_score` kept the blunt head+tail cut. Two prior audits left it as
   scope creep against the spec's wording.

**Decision:** (1) Fixed — the sync installs `--extra semgrep` on both legs, with a comment
recording that a leg whose interpreter the pin has not caught up with would drop the extra and
still run the full suite. Verified safe: every semgrep path in the default suite is stubbed
against a fake subprocess, and `test_asyncio_is_used_directly_so_the_stubs_above_are_honest`
guards that. (2) Closed on the user's instruction — `risk_score` now calls
`truncate_diff_by_file`, and ITER_03_v2 §06 is amended to name every node that reads the diff,
so the plan leads the code rather than trailing it. B-03 (registry rules) stays open by the same
licence reasoning as entry 23; B-04 (trace screenshots) stays open as a human capture task.

**Impact / Risk:** (1) adds a ~70 MB wheel to each CI leg; the uv cache absorbs it after the
first run, and installing the pin is what proves the pin resolves. (2) changes what the Haiku
scorer sees only for a diff above `GATE_MAX_DIFF_CHARS`; no shipped fixture is near 20 000 chars,
so no eval expectation moves.

**Outcome:** `ruff`, `mypy --strict` and the default suite pass — 226 passed, 100% statement and
branch coverage of `src/gate`.

---

### Entry 25

**Type:** Decision
**Mode:** Autonomous
**Timestamp:** 2026-08-10T00:00:00Z
**Task:** Fifth plan-compliance audit of the v2 family (ITER_01_v2 .. ITER_04_v2).

**Context:** `src/gate`, `static/index.html`, `data/policies/`, `rules/`, `pyproject.toml`,
`.env.example` and `.github/workflows/ci.yml` audited clean against every `sections_changed`
entry (§02 §03 §04 §05 §06) of the four v2 iterations. One documentation gap and two standing
backlog items needed a call.

**Decision:** Fixed the gap — ITER_04_v2 §04 says the installed-wheel path "lists the required
variables inline" because a wheel has no `.env.example` to point at; the README named none. It
now names `ANTHROPIC_API_KEY` and `GATE_GITHUB_TOKEN` and says the rest have defaults. Left B-03
(no vendored registry rules) and B-04 (no trace screenshots) open on the reasoning already
recorded in entries 23 and 24 — nothing about either changed, and re-deciding them without new
information would only churn the backlog.

**Impact / Risk:** Documentation only; no code path moves. B-03 keeps the investigator's
detection limited to the four hand-written rules, B-04 keeps the "proven, not asserted" claim
resting on the reader running the demo.

**Outcome:** One README edit. No source, test or config file changed, so the suite was not re-run
— entry 24's green run still describes the code.
