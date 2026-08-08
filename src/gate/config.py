"""Env-driven application settings.

Accessed only through :func:`get_settings`, which is ``lru_cache``d so the ``.env`` file and
process environment are read exactly once. ``tests/conftest.py`` calls
``get_settings.cache_clear()`` before any settings load so test env vars are honored.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Reasoning effort, a closed set the API accepts. Each model tier carries its own, paired with
# the model id it applies to. Sonnet/Opus/Fable take it; Haiku has no effort control, so
# :func:`gate.llm.models.effort_for` withholds it there.
ModelEffort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    """All runtime configuration for the compliance gate."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Secrets
    anthropic_api_key: str = ""

    # Storage
    gate_db_path: Path = Path("checkpoints.db")
    # Schema generation of `ReviewState` as checkpointed. Bumped only by a *narrowing* change (a
    # removed or retyped field); purely additive fields with defaults still deserialize. The
    # lifespan refuses to start against a `checkpoints.db` recorded at a different version.
    gate_state_version: int = 2
    # Overrides the asset-resolved default (`gate.paths.POLICY_INDEX`) rather than replacing the
    # resolution, so an installed wheel still finds its bundled copy when this is unset.
    gate_policy_index: Path | None = None

    # Model tiers
    gate_model_classifier: str = "claude-haiku-4-5"
    gate_model_classifier_effort: ModelEffort = "high"
    gate_model_specialist: str = "claude-sonnet-5"
    gate_model_specialist_effort: ModelEffort = "high"
    gate_model_investigator: str = "claude-sonnet-5"
    gate_model_investigator_effort: ModelEffort = "high"

    # Risk gate + orchestration budgets
    gate_risk_threshold: int = 40
    gate_risk_high_bound: int = 75
    gate_max_cycles: int = 2
    gate_specialist_timeout_s: float = 60.0
    gate_max_diff_chars: int = 20_000

    # Tracing. `Settings` has no `env_prefix` — each field reads the env var matching its own
    # name — so the two `otel_*` fields read `OTEL_EXPORTER_OTLP_ENDPOINT` and
    # `OTEL_EXPORTER_OTLP_HEADERS` unprefixed, which is the OTel SDK's own convention. Anyone who
    # has configured OTel before sets those without reading this repo's docs; getting it wrong is
    # silent (the exporter falls back to localhost:4318, usually right in local dev and wrong
    # everywhere else), so it is worth keeping them on the standard names.
    gate_otel_enabled: bool = True
    gate_otel_service_name: str = "pr-compliance-gate"
    otel_exporter_otlp_endpoint: str = "http://localhost:3000/api/public/otel"
    otel_exporter_otlp_headers: str = ""
    # Used only to build the UI's trace deep-link; server config, never review state.
    gate_langfuse_ui_base: str = "http://localhost:3000"

    # Investigator sub-agent budgets
    gate_investigator_max_turns: int = 8
    gate_investigator_max_tool_calls: int = 15
    gate_investigator_max_file_chars: int = 15_000


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached; cleared in tests)."""
    return Settings()
