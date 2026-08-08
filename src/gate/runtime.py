"""Runtime capability checks shared by every subprocess-spawning component.

Three parts of the gate shell out: the Agent SDK (which drives the Claude Code CLI), the
workspace clone, and semgrep. All three fail the same way on a ``SelectorEventLoop`` — a bare
``NotImplementedError`` with an *empty* message, which surfaces to the user as a truncated error
with nothing after the colon. One shared check turns that into a named cause.

``uvicorn --reload`` (and ``--workers``) sets ``use_subprocess``, which forces a
``SelectorEventLoop`` even on Windows (``uvicorn/loops/asyncio.py``). ``--reload`` is therefore
absent from every documented command in this project.
"""

from __future__ import annotations

import asyncio
import sys

UNSUPPORTED_LOOP_REASON = (
    "this event loop cannot spawn a subprocess. On Windows, `uvicorn --reload` (and `--workers`) "
    "selects a SelectorEventLoop, which has no subprocess support. Restart the server without "
    "`--reload`."
)


def loop_supports_subprocess() -> bool:
    """Whether the running loop can start a child process."""
    if sys.platform != "win32":
        return True
    return not isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
