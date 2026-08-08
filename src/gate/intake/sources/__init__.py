"""PR source adapters. See ``base.py`` for the protocol and why it has two methods."""

from __future__ import annotations

from gate.errors import InvalidPRRefError
from gate.intake.sources.base import PRPayload, PRSource
from gate.intake.sources.fixture import FixturePRSource
from gate.intake.sources.github_mcp import GitHubMCPSource

__all__ = ["PRPayload", "PRSource", "FixturePRSource", "GitHubMCPSource", "select_source"]


def select_source(pr_id: str | None, pr_url: str | None) -> tuple[PRSource, str]:
    """Pick the adapter for a start-review request and return it with its reference input.

    Exactly one of the two must be given: both or neither is a request error, not a review that
    fails a minute later.
    """
    if bool(pr_id) == bool(pr_url):
        raise InvalidPRRefError("provide exactly one of pr_id, pr_url")
    if pr_id:
        return FixturePRSource(), pr_id
    return GitHubMCPSource(), str(pr_url)
