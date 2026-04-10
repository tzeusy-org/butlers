# Security and Secrets

Butlers is a single-user system, but that is not a license for sloppy
boundaries. Sensitive data and privileged operations still need discipline.

## Core Rules

- Never commit secrets.
- Respect the project's credential storage contracts; do not add casual env-var
  fallbacks where the repo intentionally uses DB-backed or structured storage.
- Preserve least privilege at the DB, runtime, and connector layers.
- Do not log raw secrets, refresh tokens, or other high-sensitivity material.
- Do not widen transport or cross-butler boundaries for convenience.

## Secret Handling

- Use the existing credential store and storage split already defined by the
  repo's docs and contracts.
- If a new secret type is introduced, define where it lives, who can read it,
  and how operators provision it.
- Documentation for secret handling must avoid embedding actual secret values or
  one-off local shortcuts.

## Privilege and Boundary Discipline

- Schema isolation is meaningful; do not bypass it casually.
- Connector code owns transport details. Butlers should continue to operate on
  normalized requests, not raw transport-specific auth logic.
- Approval gates, manual triggers, and admin routes should remain explicit about
  who can do what and what gets recorded.

## Security Review Triggers

Treat a change as security-sensitive when it touches:

- OAuth or credential resolution
- contact identity or personally sensitive records
- DB role grants and schema permissions
- notify, approvals, or delivery surfaces
- external ingress or egress behavior

Those changes need a more careful review than normal style or correctness work.
