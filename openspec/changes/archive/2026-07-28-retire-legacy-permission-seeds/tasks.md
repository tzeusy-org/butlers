## 1. Guarded migration and regression coverage

- [x] 1.1 Add focused migrated-PostgreSQL regressions that demonstrate the
      exact legacy-row deletion, explicit grant/revoke preservation,
      absent-table safety, idempotence, and no-op downgrade.
- [x] 1.2 Add an API regression against the migrated state proving the deleted
      legacy row serializes as an inherited default while surviving explicit
      rows serialize as explicit.
- [x] 1.3 Add the forward-only guarded `core_180` migration after `core_179`
      with an exact reason predicate and intentional no-op downgrade.
- [x] 1.4 Update the full core-chain permission regression so a fresh final
      head proves inherited defaults rather than relying on seeded rows.

## 2. Verification and handoff

- [x] 2.1 Run focused migration and permissions API tests, including the
      red-green proof for each new behavior.
- [x] 2.2 Run required formatting, lint, migration-chain, and final
      quality-gate checks; validate the OpenSpec change with `--strict`.
- [x] 2.3 Review the final diff against the narrow scope and record the
      migration/API evidence for PR handoff.
