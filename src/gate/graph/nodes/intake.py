"""intake node — load the sample PR fixture into state."""

from __future__ import annotations

from gate.graph.state import ReviewState
from gate.intake.loader import load_pr
from gate.logging import get_logger

log = get_logger(__name__)


async def intake(state: ReviewState) -> dict[str, object]:
    fixture = load_pr(state.pr_id)  # FixtureError -> surfaced as HTTP 422 by the API
    log.debug("intake_loaded", pr_id=state.pr_id, files=len(fixture.files_changed))
    return {"diff": fixture.diff, "files_changed": fixture.files_changed}
