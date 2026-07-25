# Licensing Policy

Governs the licenses of third-party runtime dependencies added to the project. The license
specialist reviews dependency and manifest changes against these rules.

## LIC-01 — New runtime dependencies must use OSI-approved permissive licenses

New runtime dependencies must be distributed under an OSI-approved permissive license: MIT,
BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, or the Python Software Foundation license. A new
dependency whose license is unknown, unstated, or not on this list is a `warn` finding pending
confirmation, and the specialist should set `needs_more_context=true` if the license cannot be
determined from the diff.

## LIC-02 — No copyleft licenses in runtime dependencies

Runtime dependencies under a strong copyleft license — GPL-2.0, GPL-3.0, AGPL-3.0, or LGPL when
statically linked — must not be added. Copyleft obligations are incompatible with the project's
distribution model. Adding such a dependency (in `pyproject.toml`, `requirements.txt`, a
lockfile, or any package manifest) is a `block` finding. Development-only dependencies are out of
scope for this rule.
