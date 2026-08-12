"""The shared subprocess-capability preflight (ITER_03_v2 §04).

Three call sites depend on this — the Agent SDK, the workspace clone, and semgrep — and all three
fail identically on a ``SelectorEventLoop``: a bare ``NotImplementedError`` with an empty message.
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from gate import runtime


def test_only_windows_selector_loops_are_treated_as_subprocess_incapable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSIX SelectorEventLoops spawn subprocesses fine — uvicorn uses one by default there, so
    the check must key on the platform too, not the loop class alone."""
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    assert runtime.loop_supports_subprocess() is True


def test_windows_selector_loops_are_treated_as_subprocess_incapable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The win32 branch never executes on Linux CI, so fake the platform rather than let coverage
    of the loop check depend on which OS ran the suite."""
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(
        runtime.asyncio, "get_running_loop", lambda: Mock(spec=asyncio.SelectorEventLoop)
    )
    assert runtime.loop_supports_subprocess() is False


def test_the_named_reason_points_at_the_actual_cause() -> None:
    """`--reload` is the way a developer hits this; the message has to say so."""
    assert "--reload" in runtime.UNSUPPORTED_LOOP_REASON
