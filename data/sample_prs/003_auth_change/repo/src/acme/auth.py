"""Authentication middleware: session token verification.

Post-change snapshot for investigator review. The refactor to `verify_session` adds an explicit
signature check and a bounded clock-skew allowance; it does not weaken or bypass verification for
unauthenticated routes.
"""

from __future__ import annotations

import hmac
import time
from dataclasses import dataclass

# Small tolerance (seconds) for clock skew between issuer and verifier.
CLOCK_SKEW_S = 30

_SIGNING_KEY_ENV = "ACME_SESSION_SIGNING_KEY"  # loaded from env at runtime, never hardcoded


@dataclass
class Session:
    user_id: str
    scopes: list[str]


def _now() -> int:
    return int(time.time())


def _decode(token: str) -> dict | None:
    """Decode the token body. Returns None on any malformed input."""
    try:
        return _parse_claims(token)
    except (ValueError, KeyError):
        return None


def _signature_valid(token: str) -> bool:
    """Constant-time HMAC check against the env-provided signing key."""
    expected = _expected_signature(token)
    provided = _token_signature(token)
    return hmac.compare_digest(expected, provided)


def verify_session(token: str) -> Session | None:
    payload = _decode(token)
    if payload is None:
        return None
    # Reject expired tokens with a small clock-skew allowance.
    if payload["exp"] + CLOCK_SKEW_S < _now():
        return None
    if not _signature_valid(token):
        return None
    return Session(user_id=payload["sub"], scopes=payload["scopes"])


# --- helpers below are unchanged by this PR; included for the investigator's context ---


def _parse_claims(token: str) -> dict:
    raise NotImplementedError("stub for fixture; real impl parses the JWT body")


def _expected_signature(token: str) -> bytes:
    raise NotImplementedError("stub for fixture")


def _token_signature(token: str) -> bytes:
    raise NotImplementedError("stub for fixture")
