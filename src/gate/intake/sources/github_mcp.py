"""Live pull-request intake through the GitHub MCP server.

Failure containment follows the same principle as the investigator harness: **failure lands on a
human, never in a stack trace** — but *where* it lands depends on which side of the
request/background seam it happens on.

- ``resolve`` runs in the handler. Auth failure, missing repo, or a private repo without scope is
  a ``403``/``404`` *before* a thread is created. No half-started reviews.
- ``load`` runs in the background task. Rate limiting, a clone failure, or an oversized PR
  becomes an ``EscalationRecord``; the intake short-circuit routes it to the human gate, and the
  review sits there showing why — rather than a 500 nobody is waiting for.

MCP surface note: tool names and response shapes are the one genuine unknown in this iteration.
They are confined to this module on purpose — if the server disagrees with what is assumed here,
this is the only class that changes.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from gate.config import Settings, get_settings
from gate.errors import (
    GateError,
    InvalidPRRefError,
    SourceForbiddenError,
    SourceNotFoundError,
)
from gate.graph.state import EscalationRecord, PRRef
from gate.intake.sources.base import PRPayload
from gate.intake.workspace import WorkspaceError, materialize
from gate.llm.models import truncate_diff_by_file
from gate.logging import get_logger
from gate.telemetry.tracing import tracer

log = get_logger(__name__)

# Strict, and deliberately so: no guessing at shorthand like `owner/repo#12`.
_PR_URL = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)/?$")
_URL_FORM = "expected https://github.com/{owner}/{repo}/pull/{number}"

# The GitHub files endpoint pages at 30 by default. A single unpaginated call silently reviews a
# subset of a large PR, and a compliance gate that misses files is worse than one that refuses.
_PAGE_SIZE = 100
_MAX_PAGES = 50  # hard stop; GATE_GH_MAX_FILES escalates long before this

_RETRY_ATTEMPTS = 3
_RETRY_BASE_S = 1.0


class RateLimitedError(Exception):
    """The server reported a 429. Retried with backoff, then escalated."""


def parse_pr_url(url: str) -> tuple[str, str, int]:
    match = _PR_URL.fullmatch(url.strip())
    if match is None:
        raise InvalidPRRefError(f"unrecognized pull-request URL {url!r}; {_URL_FORM}")
    return match.group(1), match.group(2), int(match.group(3))


def display_id(ref: PRRef) -> str:
    """`pr_id` for a live PR. A display and lookup key only — never a path component, so the
    `/` and `#` are safe here in a way they would not be for a fixture id."""
    return f"{ref.owner}/{ref.repo}#{ref.number}"


@asynccontextmanager
async def _session(settings: Settings) -> AsyncIterator[ClientSession]:
    """One short-lived stdio session.

    Opened per method rather than held across ``resolve`` and ``load``: a session pinned open
    across the handler/background boundary would outlive the request that created it and need its
    own lifecycle. Two short sessions cost one extra subprocess spawn and remove that entirely.
    """
    command, *args = shlex.split(settings.gate_gh_mcp_command)
    params = StdioServerParameters(
        command=command,
        args=args,
        # The subprocess inherits nothing from pydantic-settings, so the token is passed
        # explicitly — the same trap the Agent SDK hit.
        env={
            "GITHUB_PERSONAL_ACCESS_TOKEN": settings.gate_github_token,
            "GITHUB_TOKEN": settings.gate_github_token,
        },
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def _raise_for_tool_error(tool: str, text: str) -> None:
    """Map an MCP tool error onto the gate's exception vocabulary."""
    lowered = text.lower()
    if "429" in lowered or "rate limit" in lowered:
        raise RateLimitedError(text)
    if "403" in lowered or "forbidden" in lowered or "permission" in lowered:
        raise SourceForbiddenError(f"{tool}: token lacks scope for this pull request")
    if "404" in lowered or "not found" in lowered:
        raise SourceNotFoundError(f"{tool}: pull request or repository not found")
    raise SourceNotFoundError(f"{tool} failed: {text}")


async def _call(session: ClientSession, tool: str, args: dict[str, Any]) -> Any:
    """Call one MCP tool and decode its text payload."""
    with tracer().start_as_current_span(
        f"gate.mcp.{tool}", attributes={"mcp.tool": tool, "gate.pr.source": "github"}
    ):
        result = await session.call_tool(tool, args)
        text = "\n".join(getattr(block, "text", "") for block in result.content).strip()
        if getattr(result, "isError", False):
            _raise_for_tool_error(tool, text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text  # the diff tool returns a patch, not JSON


async def _call_with_retry(session: ClientSession, tool: str, args: dict[str, Any]) -> Any:
    """Bounded exponential backoff with jitter on 429, then give up.

    A thread already exists by the time this runs, so the final failure is an escalation the
    reviewer sees — not a 500 nobody is waiting on.
    """
    for attempt in range(_RETRY_ATTEMPTS - 1):
        try:
            return await _call(session, tool, args)
        except RateLimitedError:
            delay = _RETRY_BASE_S * (2**attempt) * (1 + random.random())  # noqa: S311 — jitter
            log.info("github_rate_limited", tool=tool, attempt=attempt + 1, sleep_s=delay)
            await asyncio.sleep(delay)
    # The last attempt is outside the loop so its RateLimitedError simply propagates — no
    # unreachable `raise` at the end of the function to satisfy a type checker.
    return await _call(session, tool, args)


async def _all_changed_files(session: ClientSession, ref: PRRef) -> list[str]:
    """Every changed path, following pagination until a short page comes back."""
    paths: list[str] = []
    for page in range(1, _MAX_PAGES + 1):
        batch = await _call_with_retry(
            session,
            "get_pull_request_files",
            {
                "owner": ref.owner,
                "repo": ref.repo,
                "pullNumber": ref.number,
                "page": page,
                "perPage": _PAGE_SIZE,
            },
        )
        paths.extend(str(item["filename"]) for item in batch)
        if len(batch) < _PAGE_SIZE:
            break
    return paths


class GitHubMCPSource:
    source_name: Literal["fixture", "github"] = "github"

    async def resolve(self, ref_input: str) -> PRRef:
        """Parse the URL, then make the single metadata round trip that proves the repo exists
        and the token can see it. Bounded — one call, no clone — which is what makes a bad URL a
        422 and a private repo a 403 *before* a review row exists."""
        owner, repo, number = parse_pr_url(ref_input)
        settings = get_settings()
        async with _session(settings) as session:
            data = await _call(
                session, "get_pull_request", {"owner": owner, "repo": repo, "pullNumber": number}
            )
        return PRRef(
            owner=owner,
            repo=repo,
            number=number,
            head_sha=str(data.get("head", {}).get("sha", "")),
            base_sha=str(data.get("base", {}).get("sha", "")),
            html_url=str(data.get("html_url", ref_input)),
            title=str(data.get("title", "")),
        )

    async def load(self, ref_input: str, ref: PRRef | None) -> PRPayload:
        settings = get_settings()
        resolved = ref if ref is not None else await self.resolve(ref_input)
        pr_id = display_id(resolved)
        try:
            async with _session(settings) as session:
                files = await _all_changed_files(session, resolved)
                if len(files) > settings.gate_gh_max_files:
                    return _escalate(
                        resolved,
                        pr_id,
                        "pr_too_large",
                        f"{len(files)} changed files exceeds GATE_GH_MAX_FILES="
                        f"{settings.gate_gh_max_files}",
                    )
                raw_diff = await _call_with_retry(
                    session,
                    "get_pull_request_diff",
                    {
                        "owner": resolved.owner,
                        "repo": resolved.repo,
                        "pullNumber": resolved.number,
                    },
                )
            workspace = await materialize(
                f"https://github.com/{resolved.owner}/{resolved.repo}.git",
                resolved.head_sha,
                settings,
            )
        except (RateLimitedError, WorkspaceError, GateError, OSError) as exc:
            log.info("github_load_failed", pr_id=pr_id, error=str(exc))
            return _escalate(resolved, pr_id, "source_unavailable", str(exc))

        return PRPayload(
            ref=resolved,
            pr_id=pr_id,
            title=resolved.title,
            diff=truncate_diff_by_file(str(raw_diff), settings.gate_max_diff_chars),
            files_changed=files,
            workspace_path=str(workspace),
        )


def _escalate(ref: PRRef, pr_id: str, reason: str, detail: str) -> PRPayload:
    return PRPayload(
        ref=ref,
        pr_id=pr_id,
        title=ref.title,
        escalation=EscalationRecord(reason=reason, detail=detail),
    )
