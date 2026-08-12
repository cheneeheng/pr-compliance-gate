"""The semgrep tool as the investigator's fourth tool (ITER_04_v2 §04).

The sandbox cases from ITER_03 are re-run against the new tool: it is the same
``Path.resolve()`` + ``is_relative_to()`` check, and the only model-controlled argument is
``path``. What is new here is the run budget and the rule that a *failed* run still costs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gate.agents import investigator as investigator_mod
from gate.agents.investigator import _build_tools, _Tracker
from gate.config import Settings, get_settings
from gate.graph.state import Severity, StaticFinding


def _tracker(**kw: Any) -> _Tracker:
    defaults: dict[str, Any] = {
        "max_tool_calls": 15,
        "max_file_chars": 15_000,
        "max_semgrep_runs": 2,
        "max_semgrep_results": 50,
    }
    return _Tracker(**{**defaults, **kw})


def _tool(root: Path, tracker: _Tracker, settings: Settings | None = None) -> Any:
    tools = _build_tools(root, tracker, settings or get_settings())
    return next(t for t in tools if t.name == "run_semgrep").handler


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "audit.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "outside.py").write_text("secret = 1\n", encoding="utf-8")
    return root


def _stub_scan(
    monkeypatch: pytest.MonkeyPatch, findings: list[StaticFinding], reason: str | None = None
) -> list[Path]:
    scanned: list[Path] = []

    async def _run(target: Path, _root: Path, _settings: Settings) -> Any:
        scanned.append(target)
        return findings, reason

    monkeypatch.setattr(investigator_mod, "run_semgrep", _run)
    return scanned


def _hit(rule: str = "rules.gate-sec-01-new-network-egress") -> StaticFinding:
    return StaticFinding(
        rule_id=rule, severity=Severity.warn, path="src/audit.py", line=3, message="SEC-01"
    )


async def test_a_successful_run_records_findings_on_the_harness_not_the_model(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    _stub_scan(monkeypatch, [_hit()])

    result = await _tool(workspace, tracker)({"path": "src"})

    assert result["is_error"] is False
    assert tracker.semgrep_runs == 1 and tracker.tool_calls == 1
    assert [f.rule_id for f in tracker.static_findings] == ["rules.gate-sec-01-new-network-egress"]


async def test_a_path_outside_the_workspace_is_a_tool_error_not_an_exception(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    scanned = _stub_scan(monkeypatch, [])

    result = await _tool(workspace, tracker)({"path": "../outside.py"})

    assert result["is_error"] is True
    assert scanned == []  # never reached the scanner
    assert tracker.semgrep_runs == 0


async def test_semgrep_is_not_exempt_from_the_tool_call_budget(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deterministic does not mean free."""
    tracker = _tracker(max_tool_calls=1)
    tracker.tool_calls = 1
    _stub_scan(monkeypatch, [_hit()])

    result = await _tool(workspace, tracker)({"path": "."})

    assert result["is_error"] is True
    assert "budget exhausted" in result["content"][0]["text"]
    assert tracker.tool_calls == 1  # a refused call is not billed


async def test_the_run_budget_stops_a_rescan_of_an_unchanged_tree(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker(max_semgrep_runs=1)
    _stub_scan(monkeypatch, [_hit()])
    tool = _tool(workspace, tracker)

    first = await tool({"path": "."})
    second = await tool({"path": "."})

    assert first["is_error"] is False
    assert second["is_error"] is True
    assert "do not re-run" in second["content"][0]["text"]
    assert tracker.semgrep_runs == 1


async def test_a_failed_run_still_spends_a_run_and_a_tool_call(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise a broken scanner is free to retry, and the agent — told the tool is available —
    burns its turn budget on it instead of falling back to read_file."""
    tracker = _tracker()
    _stub_scan(monkeypatch, [], reason="semgrep binary not available (FileNotFoundError)")

    result = await _tool(workspace, tracker)({"path": "."})

    assert result["is_error"] is True
    assert "semgrep unavailable" in result["content"][0]["text"]
    assert tracker.semgrep_runs == 1 and tracker.tool_calls == 1


async def test_findings_beyond_the_result_cap_are_dropped_with_a_count(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker(max_semgrep_results=2)
    _stub_scan(monkeypatch, [_hit(f"rule-{i}") for i in range(5)])

    result = await _tool(workspace, tracker)({"path": "."})

    assert "3 further results omitted" in result["content"][0]["text"]
    assert len(tracker.static_findings) == 2


async def test_a_missing_path_is_reported_rather_than_scanned(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    _stub_scan(monkeypatch, [])

    result = await _tool(workspace, tracker)({"path": "no/such/dir"})

    assert result["is_error"] is True and "no such path" in result["content"][0]["text"]


async def test_the_tool_defaults_to_the_workspace_root(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    scanned = _stub_scan(monkeypatch, [])

    await _tool(workspace, tracker)({})

    assert scanned == [workspace.resolve()]
