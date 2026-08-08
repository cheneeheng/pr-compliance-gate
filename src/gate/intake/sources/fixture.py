"""Fixture adapter — wraps the existing sample-PR loader with no behaviour change.

The ``pr_id`` safety check lives *here* rather than on ``ReviewState``. v1 hardened ``load_pr``
to reject anything that is not a plain directory name, because ``pr_id`` was joined onto the
fixture directory *and* became the investigator's sandbox root, so a ``..`` segment relocated the
sandbox itself. A live ``pr_id`` contains ``/`` and ``#`` and would fail that check — correctly,
and without conflict, because the check belongs to this adapter. The coupling it defended against
is severed anyway: the sandbox root is now ``workspace_path``, injected by the adapter, never
derived from ``pr_id``.
"""

from __future__ import annotations

from typing import Literal

from gate.graph.state import PRRef
from gate.intake.loader import load_pr
from gate.intake.sources.base import PRPayload
from gate.paths import repo_snapshot_dir


class FixturePRSource:
    source_name: Literal["fixture", "github"] = "fixture"

    async def resolve(self, ref_input: str) -> PRRef | None:
        """Validate the fixture id. Returns None — fixtures have no ``PRRef``."""
        load_pr(ref_input)  # FixtureError -> 422, exactly as before
        return None

    async def load(self, ref_input: str, ref: PRRef | None) -> PRPayload:
        fixture = load_pr(ref_input)
        return PRPayload(
            pr_id=fixture.pr_id,
            title=fixture.title,
            diff=fixture.diff,
            files_changed=fixture.files_changed,
            # Fixtures pass their existing `repo/` snapshot, so the fixture demo is untouched by
            # the sandbox root becoming injected rather than derived.
            workspace_path=str(repo_snapshot_dir(ref_input)),
        )
