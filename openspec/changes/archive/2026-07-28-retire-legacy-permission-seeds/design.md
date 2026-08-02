## Context

`core_121` created one `public.permissions` row per default permission and
butler. The current matrix API no longer needs those rows to discover its axes:
it builds a dense grid from the registry and runtime-enforced permission
vocabulary, and it already maps an absent row to the inherited system default.
The seeded rows therefore misrepresent a default as a persisted choice.

Core migrations can run against fresh, schema-scoped, or partial installations,
so a repair must not assume `public.permissions` exists. The dashboard API
must remain truthful whether a row was removed by the migration or never
existed.

## Goals / Non-Goals

**Goals:**

- Retire only rows proven to be `core_121` defaults by their exact reason.
- Preserve explicit operator grants and revokes without interpreting or
  rewriting them.
- Keep an absent row as the single representation of an inherited default.
- Provide migrated-database and HTTP API evidence for all result states.

**Non-Goals:**

- Editing `core_121`, changing `ENFORCED_PERMISSIONS` or default policy,
  redesigning the permissions UI, or changing the API payload shape.
- Restoring deleted defaults on downgrade or running operational SQL outside
  migrations/tests.

## Decisions

### Use an exact reason predicate in a guarded `DO` block

The successor migration checks `to_regclass('public.permissions')` before its
single `DELETE ... WHERE reason = 'seeded default (core_121)'`. This is narrow,
replay-safe, and compatible with partial core-only schemas.

Alternative: delete by the old roster and permission vocabulary. Rejected:
that would depend on historical registry values and could delete an operator
row whose value happens to match a default.

### Treat downgrade as intentionally non-mutating

The deleted rows were never operator choices. Recreating them would again
erase the distinction between inherited and explicit state, and could conflict
with a new explicit choice created after upgrade. The downgrade therefore has
no SQL.

### Verify at both migration and serialized API seams

Migration tests run the emitted SQL against real PostgreSQL with a minimal
permissions table, covering exact-match deletion, explicit-row preservation,
absent-table safety, idempotence, and no-op downgrade. A migrated-state API
test uses the real route serialization to prove the row absence is visible as
an inherited cell while explicit rows remain foreground.

## Risks / Trade-offs

- [An operator copied the exact legacy reason intentionally] → The task
  defines that exact reason as the sole legacy provenance marker; no broader
  heuristic is used.
- [A partial schema lacks the table] → `to_regclass` returns null and the
  transaction completes as a no-op.
- [A future default needs a persisted audit record] → That is a separate
  default-policy design decision; this migration does not introduce a second
  representation.

## Migration Plan

1. Add `core_180` after `core_179` with a guarded, exact-reason delete.
2. Run focused migration and permissions API tests, then the configured quality
   gates and strict OpenSpec validation.
3. Deploy through normal Alembic core-chain migration execution. Re-running is
   safe; downgrade intentionally leaves the corrected state intact.

## Open Questions

None.
