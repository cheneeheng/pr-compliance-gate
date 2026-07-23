# Security Policy

Controls that every code change is reviewed against before merge. Specialists must cite the
policy id (e.g. `SEC-01`) in every finding.

## SEC-01 — No new network egress without review

Code changes must not introduce new outbound network connections (new hosts, domains, webhooks,
telemetry endpoints, or package indexes) without an explicit security review. This includes new
HTTP clients, sockets, DNS lookups, and any dependency that phones home on import or at runtime.

A change that adds a call to an external URL, a new base URL constant, or a client pointed at a
host not already used by the service is a violation until reviewed. Severity is `block` when the
destination is unknown or user-controlled; `warn` when it is a known internal host.

## SEC-02 — No hardcoded secrets or credentials

API keys, tokens, passwords, private keys, and connection strings with embedded credentials must
never appear in source, tests, fixtures, or configuration committed to the repository. Secrets
are loaded from the environment at runtime. Any literal that looks like a credential
(high-entropy string assigned to a name containing `key`, `token`, `secret`, `password`) is a
`block` finding.

## SEC-03 — Authentication and authorization changes require review

Any change to authentication, authorization, session handling, password/credential verification,
token issuance or validation, or access-control checks requires explicit security review. This
includes changes to login flows, middleware that enforces auth, role/permission checks, and JWT
or session-cookie handling.

Because such changes are high-blast-radius, a specialist that cannot fully determine the effect
of an auth change from the diff alone should set `needs_more_context=true` and state the specific
question (e.g. "does this bypass the existing session check for unauthenticated routes?") so an
investigator can inspect the surrounding code.
