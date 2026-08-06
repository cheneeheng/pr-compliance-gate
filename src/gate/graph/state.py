"""Graph state and all domain models.

Everything the pipeline produces lives inside :class:`ReviewState`, which LangGraph
checkpoints to SQLite after every node. The sub-models double as structured-output targets for
the LLM nodes (``.with_structured_output(Model)``), so they are plain Pydantic v2 models with
closed-set ``StrEnum`` fields.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------------------------
# Closed-set enums (never accept free-form strings for these from external/LLM callers)
# --------------------------------------------------------------------------------------------


class ChangeType(StrEnum):
    code = "code"
    dependency = "dependency"
    config = "config"
    docs = "docs"
    data_handling = "data_handling"


class RiskLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class Specialty(StrEnum):
    security = "security"
    license = "license"


class Severity(StrEnum):
    info = "info"
    warn = "warn"
    block = "block"


class EscalationReason(StrEnum):
    timeout = "timeout"
    disagreement = "disagreement"
    max_cycles = "max_cycles"


class DecisionAction(StrEnum):
    approve = "approve"
    reject = "reject"
    request_info = "request_info"


class Outcome(StrEnum):
    approved = "approved"
    rejected = "rejected"
    auto_approved = "auto_approved"


# --------------------------------------------------------------------------------------------
# Domain models
# --------------------------------------------------------------------------------------------


class PolicyChunk(BaseModel):
    """A retrieved slice of a policy document."""

    text: str
    policy_id: str
    source_file: str
    distance: float = 0.0  # retrieval score; 0.0 when not from a similarity query


class Classification(BaseModel):
    """Cheap-tier classification of the diff. Structured-output target."""

    model_config = ConfigDict(extra="forbid")

    change_type: ChangeType
    summary: str
    touched_areas: list[str] = Field(default_factory=list)


class RiskAssessment(BaseModel):
    """Risk score for the change. ``level`` is derived server-side, not by the model."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)
    level: RiskLevel = RiskLevel.low
    reasons: list[str] = Field(default_factory=list)
    model_used: str = ""


class Finding(BaseModel):
    """A single specialist finding against one policy. Structured-output element."""

    model_config = ConfigDict(extra="forbid")

    specialist: Specialty
    severity: Severity
    policy_id: str
    description: str
    confidence: float = Field(ge=0.0, le=1.0)
    needs_more_context: bool = False


class InvestigationReport(BaseModel):
    """Output of the autonomous investigator sub-agent."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    findings: list[str] = Field(default_factory=list)
    files_examined: list[str] = Field(default_factory=list)
    tool_calls_count: int = 0
    cost_estimate_usd: float = 0.0


class EscalationRecord(BaseModel):
    """Why the review was routed to a human. ``detail`` is human-readable."""

    reason: EscalationReason
    detail: str


class HumanDecision(BaseModel):
    """The human gate's decision. ``decided_at`` set server-side on resume."""

    action: DecisionAction
    note: str = ""
    decided_at: datetime | None = None


class Verdict(BaseModel):
    outcome: Outcome
    rationale: str


# --------------------------------------------------------------------------------------------
# Reducer for parallel + looping specialist fan-out
# --------------------------------------------------------------------------------------------


def merge_findings(existing: list[Finding], new: list[Finding]) -> list[Finding]:
    """Merge specialist findings across the parallel fan-out *and* investigation loops.

    Plain ``operator.add`` would let round-1 findings (e.g. ``needs_more_context=True``)
    accumulate alongside round-2 findings, so reconcile could never observe a question being
    resolved — it would always burn the full loop budget then escalate. Instead we replace all
    findings from a specialty as soon as that specialty emits a fresh batch. Because the two
    specialists write disjoint ``specialist`` values in the same superstep, the merge is
    order-independent for parallel writes (satisfying the original clobber concern) while a new
    investigation round cleanly supersedes the prior one.
    """
    incoming_specialties = {f.specialist for f in new}
    kept = [f for f in existing if f.specialist not in incoming_specialties]
    return kept + new


def coalesce_escalation(
    existing: EscalationRecord | None, new: EscalationRecord | None
) -> EscalationRecord | None:
    """Keep the first escalation. Needed as a reducer because both specialists run in the same
    superstep — if both time out they both write this channel, and a reducer-less concurrent
    write would raise ``InvalidUpdateError``. Reconcile only writes escalation when none exists
    yet, so first-wins is the correct semantics everywhere."""
    return existing or new


class ReviewState(BaseModel):
    """Root graph state — checkpointed by LangGraph after every node."""

    pr_id: str
    thread_id: str = ""
    diff: str = ""
    files_changed: list[str] = Field(default_factory=list)
    policy_context: list[PolicyChunk] = Field(default_factory=list)
    classification: Classification | None = None
    risk: RiskAssessment | None = None
    specialist_findings: Annotated[list[Finding], merge_findings] = Field(default_factory=list)
    investigation: InvestigationReport | None = None
    loop_count: int = 0
    escalation: Annotated[EscalationRecord | None, coalesce_escalation] = None
    human_decision: HumanDecision | None = None
    verdict: Verdict | None = None
    # Internal: reconcile writes its routing decision here; the conditional edge reads it.
    route: str | None = None


def initial_state(pr_id: str, thread_id: str) -> ReviewState:
    """Build the starting state for a new review run."""
    return ReviewState(pr_id=pr_id, thread_id=thread_id)


# Every domain type that can reach a checkpoint, declared for LangGraph's msgpack allowlist.
# LangGraph deserializes unregistered types with a warning today and will block them in a future
# version, so each checkpointer is wrapped in `.with_allowlist(CHECKPOINT_ALLOWLIST)`. Kept
# explicit rather than derived from `vars()`: an allowlist is a security boundary and is worth
# reading at a glance. `test_state.py` fails if this drifts from the module.
CHECKPOINT_ALLOWLIST: tuple[tuple[str, str], ...] = tuple(
    (__name__, _name)
    for _name in (
        "ChangeType",
        "RiskLevel",
        "Specialty",
        "Severity",
        "EscalationReason",
        "DecisionAction",
        "Outcome",
        "PolicyChunk",
        "Classification",
        "RiskAssessment",
        "Finding",
        "InvestigationReport",
        "EscalationRecord",
        "HumanDecision",
        "Verdict",
        "ReviewState",
    )
)
