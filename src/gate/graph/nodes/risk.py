"""risk_score node — cheap-tier risk score; level derived server-side from config thresholds.

The model proposes only a numeric score; the risk *level* is computed here against configured
thresholds, so tuning the gate never means re-prompting.
"""

from __future__ import annotations

from gate.config import Settings, get_settings
from gate.graph.state import ReviewState, RiskAssessment, RiskLevel
from gate.llm.models import classifier_model, structured_call, truncate_diff
from gate.llm.prompts import RISK_SYSTEM
from gate.logging import get_logger

log = get_logger(__name__)


def derive_level(score: int, settings: Settings) -> RiskLevel:
    if score < settings.gate_risk_threshold:
        return RiskLevel.low
    if score >= settings.gate_risk_high_bound:
        return RiskLevel.high
    return RiskLevel.medium


async def risk_score(state: ReviewState) -> dict[str, object]:
    settings = get_settings()
    policy_summary = "\n".join(f"[{c.policy_id}] {c.text[:300]}" for c in state.policy_context)
    diff = truncate_diff(state.diff, settings.gate_max_diff_chars)
    classification_json = state.classification.model_dump_json() if state.classification else "{}"
    user = (
        f"Classification:\n{classification_json}\n\n"
        f"Relevant policy excerpts:\n{policy_summary or '(none retrieved)'}\n\n"
        f"Diff:\n{diff}"
    )
    assessment = await structured_call(
        classifier_model(settings), RiskAssessment, RISK_SYSTEM, user
    )
    assessment.level = derive_level(assessment.score, settings)
    assessment.model_used = settings.gate_model_classifier
    log.debug("risk_scored", pr_id=state.pr_id, score=assessment.score, level=assessment.level)
    return {"risk": assessment}
