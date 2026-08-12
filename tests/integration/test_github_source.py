"""Live GitHub intake against a mocked MCP client (ITER_03_v2 §04).

Explicitly a **stub suite, not record/replay**: `tests/fixtures/github/` holds payloads captured
once by hand, replayed through a fake ``ClientSession``. No network in the default run — and
because ``network``-marked tests do not count toward coverage, this is what satisfies
``fail_under = 100``, not the live smoke test.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gate.config import Settings, get_settings
from gate.errors import InvalidPRRefError, SourceForbiddenError, SourceNotFoundError
from gate.graph.state import PRRef
from gate.intake.sources import github_mcp
from gate.intake.sources.github_mcp import (
    GitHubMCPSource,
    RateLimitedError,
    display_id,
    parse_pr_url,
)
from gate.intake.workspace import WorkspaceError

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "github"
_PR = json.loads((_FIXTURES / "pull_request.json").read_text(encoding="utf-8"))
_FILE_PAGES = json.loads((_FIXTURES / "pull_request_files_pages.json").read_text(encoding="utf-8"))
_DIFF = (_FIXTURES / "pull_request_diff.patch").read_text(encoding="utf-8")
_URL = "https://github.com/acme/widgets/pull/42"
_HEAD_SHA = _PR["head"]["sha"]


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    def __init__(self, text: str, is_error: bool = False) -> None:
        self.content = [_Block(text)]
        self.isError = is_error


class _FakeSession:
    """Stands in for an MCP ClientSession, replaying the captured payloads."""

    def __init__(self, *, errors: dict[str, str] | None = None) -> None:
        self.errors = errors or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, tool: str, args: dict[str, Any]) -> _Result:
        self.calls.append((tool, args))
        if tool in self.errors:
            return _Result(self.errors[tool], is_error=True)
        if tool == "get_pull_request":
            return _Result(json.dumps(_PR))
        if tool == "get_pull_request_files":
            page = int(args["page"])
            batch = _FILE_PAGES[page - 1] if page <= len(_FILE_PAGES) else []
            return _Result(json.dumps(batch))
        return _Result(_DIFF)  # get_pull_request_diff returns a patch, not JSON


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    """Wire the fake session in place of a real stdio MCP subprocess."""
    fake = _FakeSession()
    _install(monkeypatch, fake)
    return fake


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeSession) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_session(_settings: Settings) -> Any:
        yield fake

    monkeypatch.setattr(github_mcp, "_session", _fake_session)


@pytest.fixture(autouse=True)
def no_clone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The clone itself is exercised against a local bare repo in test_workspace.py."""

    async def _materialize(_url: str, head_sha: str, _settings: Settings) -> Path:
        return tmp_path / head_sha

    monkeypatch.setattr(github_mcp, "materialize", _materialize)


# ---- URL parsing ------------------------------------------------------------------------------


def test_a_well_formed_pull_request_url_parses() -> None:
    assert parse_pr_url(_URL) == ("acme", "widgets", 42)
    assert parse_pr_url(_URL + "/") == ("acme", "widgets", 42)


@pytest.mark.parametrize(
    "bad",
    [
        "acme/widgets#42",  # no guessing at shorthand
        "https://github.com/acme/widgets/pulls/42",
        "https://github.com/acme/widgets/pull/",
        "https://gitlab.com/acme/widgets/pull/42",
        "http://github.com/acme/widgets/pull/42",  # scheme must be https
        "https://github.com/acme/widgets/pull/42/files",
    ],
)
def test_malformed_references_are_a_422_with_the_expected_form(bad: str) -> None:
    with pytest.raises(InvalidPRRefError, match="expected https://github.com"):
        parse_pr_url(bad)


def test_the_live_pr_id_is_a_display_key_not_a_path() -> None:
    ref = PRRef(owner="acme", repo="widgets", number=42, head_sha=_HEAD_SHA)
    assert display_id(ref) == "acme/widgets#42"


# ---- resolve: the synchronous preflight ---------------------------------------------------------


async def test_resolve_makes_one_round_trip_and_returns_the_ref(session: _FakeSession) -> None:
    ref = await GitHubMCPSource().resolve(_URL)

    assert [tool for tool, _ in session.calls] == ["get_pull_request"]
    assert (ref.owner, ref.repo, ref.number) == ("acme", "widgets", 42)
    assert ref.head_sha == _HEAD_SHA
    assert ref.title == _PR["title"]


async def test_a_missing_repo_is_a_404_before_a_thread_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _FakeSession(errors={"get_pull_request": "404 Not Found"}))
    with pytest.raises(SourceNotFoundError):
        await GitHubMCPSource().resolve(_URL)


async def test_a_token_without_scope_is_a_403_before_a_thread_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _FakeSession(errors={"get_pull_request": "403 Forbidden"}))
    with pytest.raises(SourceForbiddenError):
        await GitHubMCPSource().resolve(_URL)


async def test_an_unclassifiable_tool_error_still_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _FakeSession(errors={"get_pull_request": "the server exploded"}))
    with pytest.raises(SourceNotFoundError, match="exploded"):
        await GitHubMCPSource().resolve(_URL)


# ---- load: the background half ------------------------------------------------------------------


def _ref() -> PRRef:
    return PRRef(owner="acme", repo="widgets", number=42, head_sha=_HEAD_SHA, title="t")


async def test_load_follows_pagination_to_the_end_of_the_file_list(
    session: _FakeSession,
) -> None:
    """A single unpaginated call silently reviews a subset of a large PR, and a compliance gate
    that misses files is worse than one that refuses."""
    payload = await GitHubMCPSource().load(_URL, _ref())

    pages = [args["page"] for tool, args in session.calls if tool == "get_pull_request_files"]
    assert pages == [1, 2, 3]  # stopped on the short third page
    assert len(payload.files_changed) == 212
    assert payload.escalation is None
    assert payload.workspace_path is not None
    assert payload.pr_id == "acme/widgets#42"


async def test_pagination_stops_at_the_hard_page_cap(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backstop below GATE_GH_MAX_FILES: a server that never returns a short page must not
    loop forever."""
    monkeypatch.setattr(github_mcp, "_MAX_PAGES", 2)

    payload = await GitHubMCPSource().load(_URL, _ref())

    pages = [args["page"] for tool, args in session.calls if tool == "get_pull_request_files"]
    assert pages == [1, 2]
    assert len(payload.files_changed) == 200


async def test_load_resolves_the_ref_itself_when_it_was_not_given_one(
    session: _FakeSession,
) -> None:
    payload = await GitHubMCPSource().load(_URL, None)
    assert payload.ref is not None and payload.ref.head_sha == _HEAD_SHA


async def test_too_many_changed_files_escalates_rather_than_reviewing_a_subset(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(github_mcp, "get_settings", lambda: Settings(gate_gh_max_files=10))

    payload = await GitHubMCPSource().load(_URL, _ref())

    assert payload.escalation is not None
    assert payload.escalation.reason == "pr_too_large"
    assert payload.diff == ""  # no diff fetched: the outcome is already decided


async def test_rate_limiting_backs_off_then_escalates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The thread already exists by now, so the final failure is an escalation the reviewer sees
    — not a 500 nobody is waiting on."""
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    _install(monkeypatch, _FakeSession(errors={"get_pull_request_files": "429 rate limit"}))
    monkeypatch.setattr(github_mcp.asyncio, "sleep", _sleep)

    payload = await GitHubMCPSource().load(_URL, _ref())

    assert len(slept) == github_mcp._RETRY_ATTEMPTS - 1  # tried, then gave up
    assert slept[1] > slept[0]  # exponential, jittered
    assert payload.escalation is not None
    assert payload.escalation.reason == "source_unavailable"


async def test_a_transient_rate_limit_is_retried_and_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FlakySession(_FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.first = True

        async def call_tool(self, tool: str, args: dict[str, Any]) -> _Result:
            if tool == "get_pull_request_files" and self.first:
                self.first = False
                return _Result("429 rate limit", is_error=True)
            return await super().call_tool(tool, args)

    async def _sleep(_seconds: float) -> None:
        return None

    _install(monkeypatch, _FlakySession())
    monkeypatch.setattr(github_mcp.asyncio, "sleep", _sleep)

    payload = await GitHubMCPSource().load(_URL, _ref())

    assert payload.escalation is None and len(payload.files_changed) == 212


async def test_a_clone_failure_escalates_rather_than_raising(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(_url: str, _sha: str, _settings: Settings) -> Path:
        raise WorkspaceError("git clone failed: repository not found")

    monkeypatch.setattr(github_mcp, "materialize", _boom)

    payload = await GitHubMCPSource().load(_URL, _ref())

    assert payload.escalation is not None
    assert payload.escalation.reason == "source_unavailable"
    assert "git clone failed" in payload.escalation.detail


async def test_the_fetched_diff_is_held_to_the_configured_budget(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real PRs are larger than fixtures, so the cap actually bites. The file-boundary behaviour
    itself is pinned in test_models.py; what matters here is that load applies it at all."""
    monkeypatch.setattr(github_mcp, "get_settings", lambda: Settings(gate_max_diff_chars=60))

    payload = await GitHubMCPSource().load(_URL, _ref())

    assert len(payload.diff) < len(_DIFF)
    assert "truncated" in payload.diff


async def test_the_retry_helper_raises_when_every_attempt_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(github_mcp.asyncio, "sleep", _sleep)
    fake = _FakeSession(errors={"get_pull_request": "429 too many requests"})

    with pytest.raises(RateLimitedError):
        await github_mcp._call_with_retry(fake, "get_pull_request", {})  # type: ignore[arg-type]


# ---- session construction -----------------------------------------------------------------------


async def test_the_mcp_subprocess_is_given_the_token_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The subprocess inherits nothing from pydantic-settings — the same trap the Agent SDK hit."""
    captured: dict[str, Any] = {}

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_stdio(params: Any) -> Any:
        captured["params"] = params
        yield (object(), object())

    class _FakeClientSession:
        def __init__(self, _read: Any, _write: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return SimpleNamespace(initialize=_noop)

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    async def _noop() -> None:
        return None

    monkeypatch.setattr(github_mcp, "stdio_client", _fake_stdio)
    monkeypatch.setattr(github_mcp, "ClientSession", _FakeClientSession)

    settings = get_settings().model_copy(
        update={"gate_github_token": "ghp_x", "gate_gh_mcp_command": "npx -y server-github"}
    )
    async with github_mcp._session(settings):
        pass

    params = captured["params"]
    assert params.command == "npx" and params.args == ["-y", "server-github"]
    assert params.env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_x"
