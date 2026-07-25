"""Shared test fixtures.

The settings singleton is ``lru_cache``d, so we clear it before any test triggers a load and
point storage at throwaway paths — otherwise a stale cache from another test run would win.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from gate.config import get_settings


@pytest.fixture(scope="session", autouse=True)
def _test_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    tmp = tmp_path_factory.mktemp("gate")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
    os.environ["GATE_DB_PATH"] = str(tmp / "checkpoints.db")
    os.environ["GATE_CHROMA_DIR"] = str(tmp / "chroma")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
