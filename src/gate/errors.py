"""Domain exception hierarchy. The API layer maps these to HTTP status codes."""

from __future__ import annotations


class GateError(Exception):
    """Base class for all domain errors."""


class FixtureError(GateError):
    """A sample-PR fixture is missing or malformed. Maps to HTTP 422."""


class ReviewNotFoundError(GateError):
    """No review exists for the given thread_id. Maps to HTTP 404."""


class NoPendingDecisionError(GateError):
    """The thread is not interrupted at the human gate. Maps to HTTP 409."""


class PolicyIndexError(GateError):
    """The policy index or corpus is missing, malformed, or has an unlabelled heading.

    Raised at startup by ``validate_policy_index``, so it fails the process rather than a review.
    """


class StateVersionError(GateError):
    """The checkpoint database was written by an incompatible ``GATE_STATE_VERSION``."""
