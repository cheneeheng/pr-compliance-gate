"""Investigator helpers that shape prompts and read the SDK's message surface (ITER_03 §04/§06).

The message/cost extraction is duck-typed on purpose — the SDK is young and its surface moves —
so these tests pin the shapes the harness must tolerate.
"""

from __future__ import annotations

from gate.agents.investigator import (
    _build_prompt,
    _extract_cost,
    _message_text,
    _open_questions,
    _system_prompt,
    _truncate,
)
from gate.config import Settings
from gate.graph.state import (
    DecisionAction,
    Finding,
    HumanDecision,
    ReviewState,
    Severity,
    Specialty,
)

_SETTINGS = Settings(
    gate_model_investigator="claude-sonnet-4-6",
    gate_investigator_max_turns=8,
    gate_investigator_max_tool_calls=15,
)


def _question(description: str, *, needs_context: bool = True) -> Finding:
    return Finding(
        specialist=Specialty.security,
        severity=Severity.warn,
        policy_id="SEC-03",
        description=description,
        confidence=0.5,
        needs_more_context=needs_context,
    )


def test_truncate_keeps_both_ends() -> None:
    assert _truncate("abc", 10) == "abc"
    out = _truncate("A" * 10 + "B" * 10, 10)
    assert out.startswith("AAAAA") and out.endswith("BBBBB")
    assert "[... truncated 10 chars ...]" in out


def test_open_questions_takes_only_unanswered_findings() -> None:
    state = ReviewState(
        pr_id="t",
        specialist_findings=[
            _question("is the signature checked?"),
            _question("", needs_context=True),  # no question text to chase
            _question("already settled", needs_context=False),
        ],
    )
    assert _open_questions(state) == ["is the signature checked?"]


def test_a_human_request_for_info_becomes_an_investigation_lead() -> None:
    """ITER_04 §06: the reviewer's note is a first-class question, not just a comment."""
    state = ReviewState(
        pr_id="t",
        specialist_findings=[_question("machine question")],
        human_decision=HumanDecision(action=DecisionAction.request_info, note="human question"),
    )
    assert _open_questions(state) == ["machine question", "human question"]


def test_other_human_actions_are_not_leads() -> None:
    state = ReviewState(
        pr_id="t",
        human_decision=HumanDecision(action=DecisionAction.approve, note="looks fine"),
    )
    assert _open_questions(state) == []


def test_build_prompt_lists_questions_and_changed_files() -> None:
    state = ReviewState(pr_id="t", files_changed=["src/acme/auth.py"])
    prompt = _build_prompt(state, ["q1", "q2"])
    assert "- q1\n- q2" in prompt
    assert "src/acme/auth.py" in prompt


def test_build_prompt_stays_usable_with_nothing_to_go_on() -> None:
    prompt = _build_prompt(ReviewState(pr_id="t"), [])
    assert "(no explicit questions; summarize risk)" in prompt
    assert "(none)" in prompt


def test_system_prompt_states_the_enforced_budgets() -> None:
    """ITER_03 §06: the prompt shapes behavior, the harness guarantees it — so the numbers the
    harness enforces must be the numbers the model is told."""
    prompt = _system_prompt(_SETTINGS)
    assert "8 turns" in prompt and "15 tool calls" in prompt


class _Result:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


def test_extract_cost_prefers_the_sdk_reported_total() -> None:
    assert _extract_cost(_Result(total_cost_usd=0.25), _SETTINGS) == 0.25


def test_extract_cost_falls_back_to_the_local_price_table() -> None:
    message = _Result(usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    assert _extract_cost(message, _SETTINGS) == 18.0  # sonnet: 3.00 in + 15.00 out


def test_extract_cost_is_zero_when_the_sdk_reports_nothing() -> None:
    assert _extract_cost(_Result(), _SETTINGS) == 0.0


def test_message_text_reads_both_known_shapes() -> None:
    assert _message_text(_Result(result="final answer")) == "final answer"
    assert _message_text(_Result(content=[_Block("a"), _Block(""), _Block("b")])) == "a\nb"
    assert _message_text(_Result()) == ""
