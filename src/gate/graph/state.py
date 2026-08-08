"""Graph state and all domain models.

Everything the pipeline produces lives inside :class:`ReviewState`, which LangGraph
checkpoints to SQLite after every node. The sub-models double as structured-output targets for
the LLM nodes (``.with_structured_output(Model)``), so they are plain Pydantic v2 models with
closed-set ``StrEnum`` fields.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

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
    # Raised by `PRSource.load` before the graph has a diff, and routed by the intake
    # short-circuit rather than by reconcile (which only runs after the specialists).
    pr_too_large = "pr_too_large"
    source_unavailable = "source_unavailable"


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
    """A policy document section, split on its `##` heading and labelled with its policy id.

    No similarity score: retrieval is a deterministic ``change_type`` -> file lookup, so there is
    nothing to score. ``policy_id`` is the heading's leading token and is the single citation key
    — a separate ``heading_id`` would be the same value under a second name, leaving specialists
    two candidate fields to cite.
    """

    text: str
    policy_id: str
    source_file: str


class PRRef(BaseModel):
    """Identity of a live pull request, resolved before a review thread exists."""

    owner: str
    repo: str
    number: int
    head_sha: str
    base_sha: str = ""
    html_url: str = ""
    title: str = ""


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
    # The run span's trace id, seeded by the background task *before* `ainvoke` so it is already
    # present in the very first checkpoint — including for a review that never reaches a node.
    # Seeded rather than written by the first node: `traced_node` is applied uniformly and gains
    # nothing from one node carrying a special responsibility. It is a `str`, so the checkpoint
    # allowlist is untouched and GATE_STATE_VERSION stays at 2.
    trace_id: str | None = None
    # Where the PR came from. All three of these are additive with defaults, so a checkpoint
    # written earlier in the v2 family still deserializes and GATE_STATE_VERSION stays at 2 —
    # it exists for the one *narrowing* in this family, and bumping it for purely additive
    # fields would force a pointless `checkpoints.db` delete and train readers to ignore it.
    source: Literal["fixture", "github"] = "fixture"
    pr_ref: PRRef | None = None
    # The investigator's sandbox root, injected by the adapter rather than derived from `pr_id`.
    # Null when `load` failed before materializing anything — a non-optional `str` would force an
    # empty-string sentinel that the sandbox check would then have to special-case.
    workspace_path: str | None = None
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
        "PRRef",
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
