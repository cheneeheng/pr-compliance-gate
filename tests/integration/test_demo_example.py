"""The runnable demo drives the same adapter seam the API does (ITER_04_v2 §04).

``examples/`` sits outside the coverage source, so a regression here is invisible to
``fail_under = 100`` — and one shipped: when ITER_03_v2 moved loading ahead of ``ainvoke``, the
``intake`` node stopped fetching anything, but the demo kept calling
``initial_state(pr_id, thread_id)`` and quietly reviewed an empty diff. These tests pin the
seeding, which is the part a reader of the script would otherwise have to trust.

The module is loaded by path because ``examples/`` is a script directory, not a package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_DEMO_PATH = Path(__file__).resolve().parents[2] / "examples" / "demo.py"


@pytest.fixture(scope="module")
def demo() -> Any:
    spec = importlib.util.spec_from_file_location("gate_demo_example", _DEMO_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Snapshot:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values
        self.tasks: tuple[Any, ...] = ()


class _RecordingGraph:
    """Records the state the demo hands to ``ainvoke``, then reports a finished review."""

    def __init__(self) -> None:
        self.seeded: Any = None

    async def ainvoke(self, state: Any, _config: dict[str, Any]) -> dict[str, Any]:
        self.seeded = state
        return {}

    async def aget_state(self, _config: dict[str, Any]) -> _Snapshot:
        return _Snapshot({"verdict": {"outcome": "auto_approved"}})


async def test_a_fixture_beat_is_seeded_with_its_diff_before_the_graph_runs(demo: Any) -> None:
    """`intake` fetches nothing, so an unseeded run reviews an empty diff — silently."""
    graph = _RecordingGraph()
    beat = next(b for b in demo.BEATS if b.name == "003_auth_change")

    title, values = await demo._drive(graph, beat)

    seeded = graph.seeded
    assert "diff --git" in seeded.diff
    assert seeded.files_changed == ["src/acme/auth.py", "src/acme/audit.py"]
    assert seeded.source == "fixture"
    # The investigator's sandbox root is injected, never derived from `pr_id`.
    assert Path(seeded.workspace_path).is_dir()
    assert seeded.thread_id  # the run span and `traced_node` are keyed on it
    assert title == "Refactor session validation in auth middleware"
    assert values["verdict"]["outcome"] == "auto_approved"


def test_only_the_live_beat_takes_the_pr_url_override(demo: Any) -> None:
    beats = demo._select(None, "https://github.com/acme/widgets/pull/7")
    live = [b for b in beats if b.live]
    assert [b.ref for b in live] == ["https://github.com/acme/widgets/pull/7"]
    assert all(b.ref == b.name for b in beats if not b.live)  # fixture beats untouched


def test_an_unknown_beat_name_is_rejected_rather_than_running_everything(demo: Any) -> None:
    assert demo._select("004_does_not_exist", None) is None
