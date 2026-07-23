"""classify node — cheap-tier structured classification of the diff.

Only the (truncated) diff is given to the classifier; policy text is retrieved *after* this
node, keeping the cheap gate cheap.
"""

from __future__ import annotations

from gate.config import get_settings
from gate.graph.state import Classification, ReviewState
from gate.llm.models import classifier_model, structured_call, truncate_diff
from gate.llm.prompts import CLASSIFY_SYSTEM
from gate.logging import get_logger

log = get_logger(__name__)


async def classify(state: ReviewState) -> dict[str, object]:
    settings = get_settings()
    diff = truncate_diff(state.diff, settings.gate_max_diff_chars)
    classification = await structured_call(
        classifier_model(settings), Classification, CLASSIFY_SYSTEM, f"Diff:\n{diff}"
    )
    log.debug("classified", pr_id=state.pr_id, change_type=classification.change_type)
    return {"classification": classification}
