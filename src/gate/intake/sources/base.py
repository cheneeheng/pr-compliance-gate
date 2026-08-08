"""The ``PRSource`` protocol — the seam that keeps live intake out of the graph.

**Two methods, not one, is the load-bearing choice.** They split exactly where the
request/background boundary falls:

- ``resolve`` is cheap, runs in the HTTP handler, and raises the HTTP-mapped errors. A typo'd URL
  is a ``422`` *before* a review thread exists.
- ``load`` is slow, runs in the background task, and fails by *escalation* rather than exception.
  A thread already exists by then to carry the failure, and nobody is waiting on a response.

A single ``load()`` cannot express "a bad URL is a 422 but a clone failure is an escalation": it
would force either the whole live path into the handler (a POST that blocks on a clone, which the
polling UI exists to avoid) or every failure into an escalation (a typo'd URL creating a review
row). Two implementations satisfy both methods, and everything downstream of ``intake`` stays
unaware of where the PR came from.

The protocol is also the seam that absorbs the one genuine unknown in this version: the GitHub
MCP server's exact tool names and response shapes. If they differ from expectation, one class
changes.

Both methods take the original ``ref_input`` so the adapters stay stateless — no instance has to
survive the handler-to-background-task hop carrying half-resolved state.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from gate.graph.state import EscalationRecord, PRRef


class PRPayload(BaseModel):
    """What an adapter hands the background task. Never persisted, so deliberately *not* on
    ``CHECKPOINT_ALLOWLIST`` — listing it would imply a persistence contract that does not exist.
    """

    ref: PRRef | None = None
    pr_id: str
    title: str = ""
    diff: str = ""
    files_changed: list[str] = Field(default_factory=list)
    workspace_path: str | None = None
    # Set instead of a diff when load failed. The background task seeds it into the initial
    # state and the intake short-circuit does the routing; `load` itself cannot route.
    escalation: EscalationRecord | None = None


class PRSource(Protocol):
    """Where a pull request comes from."""

    source_name: Literal["fixture", "github"]

    async def resolve(self, ref_input: str) -> PRRef | None:
        """Validate the reference cheaply. Raises ``GateError`` subclasses mapped to HTTP."""

    async def load(self, ref_input: str, ref: PRRef | None) -> PRPayload:
        """Fetch the diff, file list and workspace. Never raises — fails by escalation."""
