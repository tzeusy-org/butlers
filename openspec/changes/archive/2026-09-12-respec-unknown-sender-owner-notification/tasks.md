## 1. Entity-First Contract

- [x] 1.1 Add the owner-notification requirement to the `entity-identity` delta using transitory entities rather than retired contact rows.
- [x] 1.2 Replace the active Switchboard unknown-sender reference to archived `contacts-identity` with the `entity-identity` flow.

## 2. Runtime-Gap Documentation

- [x] 2.1 Document the helper-versus-fleet activation gap and the bounded follow-up required to activate owner delivery.

## 3. Verification

- [x] 3.1 Sync the delta into the authoritative specifications and inspect the scoped diff for table-centric regressions.
- [x] 3.2 Run focused identity contract tests, strict OpenSpec validation, documentation/integrity checks, and session-link guards.
