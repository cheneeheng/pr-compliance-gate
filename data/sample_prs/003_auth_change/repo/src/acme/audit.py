"""Audit trail for session verification outcomes.

Added alongside the `verify_session` refactor. This is the file the committed semgrep ruleset
fires on, so the fixture demo exercises `run_semgrep` deterministically without depending on a
live PR: the audit event ships the raw email to the logger (DATA-03) and posts it to an external
collector (SEC-01).
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger("acme.auth")

AUDIT_COLLECTOR_URL = "https://audit-collector.acme-vendor.example/v1/events"


def record_verification(user_email: str, outcome: str) -> None:
    """Emit one audit event for a session verification attempt."""
    logger.info("session_verified email=%s outcome=%s", user_email, outcome)
    requests.post(
        AUDIT_COLLECTOR_URL,
        json={"email": user_email, "outcome": outcome},
        timeout=5,
    )
