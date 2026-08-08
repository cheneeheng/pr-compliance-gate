"""Specialist nodes — security and license review, run as a parallel fan-out.

Each specialist sees only its own policy slice (keeps prompts small, citations checkable). Each
call is timeout-wrapped: on timeout the node writes a timeout escalation and returns no
findings, so the graph proceeds instead of hanging. After an investigation, the report summary
is appended to the specialists' context.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from gate.config import Settings, get_settings
from gate.graph.state import EscalationRecord, Finding, ReviewState, Specialty
from gate.llm.models import specialist_model, structured_call, truncate_diff
from gate.llm.prompts import specialist_system
from gate.logging import get_logger

log = get_logger(__name__)

# Which policy-id prefixes each specialty reviews. Data-handling policies (DATA-*) are reviewed
# by the security specialist — the plan defines only security/license specialists, and PII/data
# controls are a security concern. (See DECISION_LOG entry 4.)
_PREFIXES: dict[Specialty, tuple[str, ...]] = {
    Specialty.security: ("SEC", "DATA"),
    Specialty.license: ("LIC",),
}


class _FindingList(BaseModel):
    """Structured-output wrapper — with_structured_output needs a single schema."""

    findings: list[Finding] = Field(default_factory=list)


def _policy_slice(state: ReviewState, specialty: Specialty) -> str:
    prefixes = _PREFIXES[specialty]
    chunks = [c for c in state.policy_context if c.policy_id.startswith(prefixes)]
    if not chunks:
        return "(no policy chunks matched this specialty)"
    return "\n\n".join(f"[{c.policy_id}] ({c.source_file})\n{c.text}" for c in chunks)


async def _run_specialist(state: ReviewState, specialty: Specialty) -> dict[str, object]:
    settings: Settings = get_settings()
    diff = truncate_diff(state.diff, settings.gate_max_diff_chars)
    user = f"Diff:\n{diff}\n\nPolicy chunks:\n{_policy_slice(state, specialty)}"
    if state.investigation is not None:
        user += f"\n\nInvestigation notes:\n{state.investigation.summary}"
        # Concrete evidence, so a specialist can cite rule/path/line in a Finding.description and
        # raise severity to `block` with something behind it. `merge_findings` is what makes the
        # raise visible: the re-run's batch *replaces* this specialist's prior findings, so the
        # pre-semgrep `needs_more_context` note does not linger beside the evidence-backed block.
        evidence = "\n".join(
            f"- {f.rule_id} {f.path}:{f.line} ({f.severity.value})"
            for f in state.investigation.static_analysis
        )
        if evidence:
            user += f"\n\nStatic analysis hits:\n{evidence}"

    try:
        result = await asyncio.wait_for(
            structured_call(
                specialist_model(settings),
                _FindingList,
                specialist_system(specialty.value),
                user,
            ),
            timeout=settings.gate_specialist_timeout_s,
        )
    except TimeoutError:
        log.info("specialist_timeout", pr_id=state.pr_id, specialist=specialty.value)
        return {
            "escalation": EscalationRecord(
                reason="timeout",
                detail=f"{specialty.value} specialist exceeded "
                f"{settings.gate_specialist_timeout_s:.0f}s and was skipped",
            )
        }

    # Trust the routing key, not the model: force the specialist field to match this node.
    findings = [f.model_copy(update={"specialist": specialty}) for f in result.findings]
    log.debug("specialist_done", pr_id=state.pr_id, specialist=specialty.value, n=len(findings))
    return {"specialist_findings": findings}


async def security_specialist(state: ReviewState) -> dict[str, object]:
    return await _run_specialist(state, Specialty.security)


async def license_specialist(state: ReviewState) -> dict[str, object]:
    return await _run_specialist(state, Specialty.license)
