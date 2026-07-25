"""investigate node — runs the autonomous investigator sub-agent behind the cost gate.

Delegates the tool loop to the Agent SDK harness in ``agents.investigator``. This is the sole
place ``loop_count`` is incremented (an ITER_02 invariant preserved here).
"""

from __future__ import annotations

from gate.agents.investigator import run_investigation
from gate.config import get_settings
from gate.graph.state import ReviewState
from gate.logging import get_logger

log = get_logger(__name__)


async def investigate(state: ReviewState) -> dict[str, object]:
    report = await run_investigation(state, get_settings())
    log.info(
        "investigation_done",
        pr_id=state.pr_id,
        tool_calls=report.tool_calls_count,
        cost_usd=report.cost_estimate_usd,
    )
    return {"investigation": report, "loop_count": state.loop_count + 1}
