"""Env-driven application settings.

Accessed only through :func:`get_settings`, which is ``lru_cache``d so the ``.env`` file and
process environment are read exactly once. ``tests/conftest.py`` calls
``get_settings.cache_clear()`` before any settings load so test env vars are honored.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration for the compliance gate."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Secrets
    anthropic_api_key: str = ""

    # Storage
    gate_db_path: Path = Path("checkpoints.db")
    gate_chroma_dir: Path = Path(".chroma")

    # Model tiers
    gate_model_classifier: str = "claude-haiku-4-5"
    gate_model_specialist: str = "claude-sonnet-4-6"
    gate_model_investigator: str = "claude-sonnet-4-6"
    gate_embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Risk gate + orchestration budgets
    gate_risk_threshold: int = 40
    gate_risk_high_bound: int = 75
    gate_max_cycles: int = 2
    gate_specialist_timeout_s: float = 60.0
    gate_max_diff_chars: int = 20_000

    # Investigator sub-agent budgets
    gate_investigator_max_turns: int = 8
    gate_investigator_max_tool_calls: int = 15
    gate_investigator_max_file_chars: int = 15_000

    # Chroma collection name for policy chunks
    policy_collection: str = "policies"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached; cleared in tests)."""
    return Settings()
