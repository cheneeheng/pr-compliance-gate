"""The semgrep investigator tool (ITER_04_v2 §04).

Every degradation path is a branch under ``fail_under = 100``, so each one is exercised against a
**stubbed subprocess** rather than covered by the ``llm`` run, which does not count. That also
means these tests pass on an interpreter or platform where the pinned semgrep is not installed —
which is the point of keeping it an optional extra.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from gate.agents.tools import semgrep as semgrep_mod
from gate.agents.tools.semgrep import render, rules_dir, run_semgrep
from gate.config import Settings
from gate.graph.state import Severity, StaticFinding

_RESULTS = {
    "results": [
        {
            "check_id": "rules.gate-data-03-pii-reaching-a-logger",
            "path": "src/acme/audit.py",
            "start": {"line": 22},
            "extra": {"severity": "ERROR", "message": "DATA-03: PII reaching a logger"},
        },
        {
            "check_id": "rules.gate-sec-01-new-network-egress",
            "path": "src/acme/audit.py",
            "start": {"line": 23},
            "extra": {"severity": "WARNING", "message": "SEC-01: new outbound network call"},
        },
        {
            "check_id": "rules.unknown-severity",
            "path": "src/acme/audit.py",
            "start": {"line": 1},
            "extra": {"severity": "EXPERIMENTAL", "message": "something new"},
        },
    ]
}


class _Proc:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 1) -> None:
        self._out, self._err, self.returncode = stdout, stderr, returncode
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out, self._err

    def kill(self) -> None:
        self.killed = True


def _stub(monkeypatch: pytest.MonkeyPatch, proc: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def _exec(*argv: str, **kwargs: Any) -> Any:
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        if isinstance(proc, Exception):
            raise proc
        return proc

    monkeypatch.setattr(semgrep_mod.asyncio, "create_subprocess_exec", _exec)
    return captured


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src" / "acme").mkdir(parents=True)
    (tmp_path / "src" / "acme" / "audit.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "gate-policy.yaml").write_text("rules: []\n", encoding="utf-8")
    return Settings(gate_semgrep_rules_dir=rules, gate_semgrep_timeout_s=30)


# ---- invocation ---------------------------------------------------------------------------------


async def test_the_ruleset_is_passed_as_a_resolved_absolute_path(
    workspace: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative `--config rules/` re-introduces the CWD assumption the asset resolver kills.
    `--config auto` is never used: it fetches rules over the network at run time, which breaks the
    no-network sandbox and makes results non-deterministic."""
    captured = _stub(monkeypatch, _Proc(json.dumps(_RESULTS).encode()))

    await run_semgrep(workspace, workspace, settings)

    argv = captured["argv"]
    config = argv[argv.index("--config") + 1]
    assert Path(config).is_absolute() and Path(config) == rules_dir(settings).resolve()
    assert "auto" not in argv
    assert {"--json", "--quiet", "--metrics=off", "--disable-version-check"} <= set(argv)
    assert "shell" not in captured["kwargs"]  # fixed argv, never a shell


async def test_results_are_shaped_down_to_what_the_agent_needs(
    workspace: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """semgrep JSON is large and mostly metadata; feeding it raw would spend the turn budget on
    noise."""
    _stub(monkeypatch, _Proc(json.dumps(_RESULTS).encode()))

    findings, reason = await run_semgrep(workspace, workspace, settings)

    assert reason is None
    assert [f.severity for f in findings] == [Severity.block, Severity.warn, Severity.info]
    assert findings[0].rule_id == "rules.gate-data-03-pii-reaching-a-logger"
    assert findings[0].line == 22
    assert findings[0].path == "src/acme/audit.py"


async def test_a_path_outside_the_workspace_is_reported_verbatim(
    workspace: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outside = dict(_RESULTS)
    outside = {
        "results": [{**_RESULTS["results"][0], "path": str(tmp_path.parent / "elsewhere.py")}]
    }
    _stub(monkeypatch, _Proc(json.dumps(outside).encode()))

    findings, _ = await run_semgrep(workspace, workspace, settings)

    assert findings[0].path.endswith("elsewhere.py")


async def test_a_clean_scan_returns_no_findings(
    workspace: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, _Proc(b'{"results": []}', returncode=0))  # exit 0 = ran, found nothing

    findings, reason = await run_semgrep(workspace, workspace, settings)

    assert findings == [] and reason is None


# ---- degradation paths --------------------------------------------------------------------------


async def test_a_missing_binary_degrades_instead_of_raising(
    workspace: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """semgrep is an optional extra, so "not installed" is an ordinary state, not a crash."""
    _stub(monkeypatch, FileNotFoundError("semgrep"))

    findings, reason = await run_semgrep(workspace, workspace, settings)

    assert findings == [] and reason is not None and "binary not available" in reason


async def test_a_timeout_kills_the_process_and_names_the_budget(
    workspace: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _Proc(b"")
    _stub(monkeypatch, proc)

    async def _wait_for(awaitable: Any, timeout: float) -> Any:
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(semgrep_mod.asyncio, "wait_for", _wait_for)

    findings, reason = await run_semgrep(workspace, workspace, settings)

    assert proc.killed and findings == []
    assert reason is not None and "timed out after 30s" in reason


async def test_a_non_zero_exit_surfaces_the_last_stderr_line(
    workspace: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, _Proc(b"", stderr=b"noise\nrule parse error", returncode=2))

    _, reason = await run_semgrep(workspace, workspace, settings)

    assert reason is not None and "exit 2: rule parse error" in reason


async def test_a_non_zero_exit_with_no_stderr_still_reports_something(
    workspace: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, _Proc(b"", stderr=b"", returncode=7))

    _, reason = await run_semgrep(workspace, workspace, settings)

    assert reason is not None and "exit 7: no output" in reason


async def test_unparseable_output_degrades_gracefully(
    workspace: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, _Proc(b"not json at all"))

    _, reason = await run_semgrep(workspace, workspace, settings)

    assert reason == "unparseable JSON output"


async def test_an_unsupported_event_loop_is_named_not_guessed_at(
    workspace: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Third `create_subprocess_exec` call site, and it fails the same way as the other two."""
    monkeypatch.setattr(semgrep_mod, "loop_supports_subprocess", lambda: False)

    _, reason = await run_semgrep(workspace, workspace, settings)

    assert reason is not None and "--reload" in reason


async def test_a_missing_ruleset_is_named_rather_than_silently_finding_nothing(
    workspace: Path, tmp_path: Path
) -> None:
    """An installed wheel without `rules/` has a scanner with no rules — which otherwise fails
    silently as "no static findings"."""
    settings = Settings(gate_semgrep_rules_dir=tmp_path / "absent")

    _, reason = await run_semgrep(workspace, workspace, settings)

    assert reason is not None and "no ruleset at" in reason


def test_the_rules_directory_falls_back_to_the_bundled_asset() -> None:
    from gate.paths import RULES_DIR

    assert rules_dir(Settings()) == RULES_DIR
    assert (RULES_DIR / "gate-policy.yaml").is_file()  # the shipped ruleset exists


# ---- rendering ----------------------------------------------------------------------------------


def _finding(index: int) -> StaticFinding:
    return StaticFinding(
        rule_id=f"rule-{index}", severity=Severity.warn, path="a.py", line=index, message="m"
    )


def test_rendering_caps_results_and_says_how_many_it_dropped() -> None:
    text = render([_finding(i) for i in range(5)], max_results=2)

    assert text.count("\n") == 2  # two findings plus the marker
    assert "3 further results omitted" in text


def test_rendering_an_empty_scan_says_so_rather_than_returning_nothing() -> None:
    assert render([], max_results=50) == "semgrep found no matches."


def test_rendering_inside_the_cap_adds_no_marker() -> None:
    assert "omitted" not in render([_finding(1)], max_results=50)


def test_asyncio_is_used_directly_so_the_stubs_above_are_honest() -> None:
    """Guards the test doubles: patching `semgrep_mod.asyncio` only works while the module calls
    through the module object rather than a from-import."""
    assert semgrep_mod.asyncio is asyncio
