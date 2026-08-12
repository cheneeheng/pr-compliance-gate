"""Live GitHub intake against a real public PR (ITER_03_v2 §06).

Marked ``network`` **and** ``llm``: it needs a `GATE_GITHUB_TOKEN`, the GitHub MCP server on
PATH, and a live Anthropic key. Excluded from the default run so a green default suite never
depends on GitHub being up.

It asserts **liveness, not correctness** — there is no ground truth for someone else's pull
request, so the claim is only that a real PR flows through intake, classification and a verdict
without error. The deterministic route assertions stay on fixtures (`eval_routes.py`), which is
exactly why fixtures remain the eval substrate: live PRs move.

Because ``network``-marked tests do not count toward coverage, nothing here is load-bearing for
``fail_under = 100`` — the mocked suite in ``tests/integration/test_github_source.py`` is.
"""

from __future__ import annotations

import os

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from gate.config import get_settings
from gate.graph.build import compile_graph
from gate.graph.checkpoint import use_state_allowlist
from gate.graph.state import initial_state
from gate.intake.sources.github_mcp import GitHubMCPSource

# A small, long-lived public PR. If it ever disappears, point this at another one — the assertion
# is on liveness, so any real PR does.
_PUBLIC_PR = os.environ.get("GATE_EVAL_PR_URL", "https://github.com/psf/requests/pull/6710")

pytestmark = [pytest.mark.network, pytest.mark.llm]


async def test_a_real_public_pull_request_reaches_a_verdict() -> None:
    settings = get_settings()
    if not settings.gate_github_token:
        pytest.skip("GATE_GITHUB_TOKEN is not set")

    source = GitHubMCPSource()
    ref = await source.resolve(_PUBLIC_PR)
    payload = await source.load(_PUBLIC_PR, ref)
    assert payload.escalation is None, payload.escalation
    assert payload.files_changed and payload.diff
    assert payload.workspace_path is not None

    graph = compile_graph(use_state_allowlist(InMemorySaver()))
    thread_id = "eval-live-pr"
    state = initial_state(payload.pr_id, thread_id)
    state.source = "github"
    state.pr_ref = payload.ref
    state.workspace_path = payload.workspace_path
    state.diff = payload.diff
    state.files_changed = payload.files_changed

    values = await graph.ainvoke(state, {"configurable": {"thread_id": thread_id}})

    assert values["classification"] is not None
    assert values["risk"] is not None
