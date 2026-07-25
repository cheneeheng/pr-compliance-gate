# Data-Handling Policy

Controls for how the service treats user and personal data. Reviewed by the security specialist
(there is no separate data specialist).

## DATA-01 — Encrypt personal data at rest and in transit

Personal data (names, emails, addresses, government identifiers, precise location, payment
details) must be encrypted in transit (TLS) and at rest. A change that persists personal data to
a new store, file, or cache without encryption, or that transmits it over a plaintext channel,
is a `block` finding.

## DATA-03 — No PII in logs

Personally identifiable information must never be written to logs. This includes emails, full
names, tokens, session identifiers, passwords, and request bodies or query parameters that carry
user data. A change that logs a variable holding PII, or logs an entire request/response object
that may contain it, is a `block` finding. Logging opaque, non-reversible identifiers
(correlation ids, prefixed public ids) is permitted.
