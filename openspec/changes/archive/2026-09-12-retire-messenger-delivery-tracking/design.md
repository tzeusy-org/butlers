## Context

The Messenger tracking module owns four tables but the live `route.execute`
path invokes approved channel adapters directly. The tracking module and its
REST/UI readers therefore cannot attest to live delivery and instead fabricate
empty operational health.

## Goals / Non-Goals

**Goals:**

- Remove every unwired tracking, retry, dead-letter, and fabricated-health
  surface.
- Fail closed before destructive DDL when legacy tracking data exists.
- Preserve the established egress, approval, routing, deferred-notification,
  and attention-ledger paths.

**Non-Goals:**

- Wire a replacement delivery engine, migrate historical tracking rows, or
  change provider adapter retries and outcomes.

## Decisions

- `msg_003` acquires one transaction-scoped `ACCESS EXCLUSIVE` lock across every
  existing legacy table before checking any row or issuing any `DROP TABLE`.
  A concurrent writer therefore commits before the guarded emptiness check (and
  blocks retirement) or waits until the migration completes; no check-to-drop
  window can lose a row. A separate owner-authorized retention decision is
  required for non-empty data.
- On an empty schema, it drops dependent tables before `delivery_requests`.
  Downgrade recreates the old empty schema solely for Alembic reversibility;
  it cannot restore dropped rows.
- Delete the whole roster-local module, its tools, REST router/models, hook,
  bespoke tab, and cache registrations. Absence is truthful; returning zeroes
  would preserve the fabricated surface.
- Delete `docs/butlers/messenger-flows.svg`: it diagrams the retired pending
  delivery queue, request/attempt/receipt/dead-letter tables, and receipt
  recording, rather than the live direct adapter path.
- Keep core Messenger temporal preference tools and all channel modules. They
  are independently wired and govern deferred notifications and egress.

## Risks / Trade-offs

- [Existing tracking data blocks upgrade] → the exception names the table and
  occurs before DDL, preserving data for explicit retention handling.
- [A removal accidentally touches live delivery] → route-level tests assert
  adapter egress and no legacy table reference.
- [Downgrade is mistaken for data restoration] → migration docstring and tests
  explicitly describe empty-schema-only restoration.

## Migration Plan

1. Deploy the code removal and `msg_003` together.
2. Run the migration only through the normal migration runner; do not execute it
   against a live database during this change.
3. If a legacy table is non-empty, stop before DDL and retain the database for
   an explicit owner decision. Empty schemas migrate by dropping all four tables.
4. A downgrade can recreate an empty compatibility schema but never recovers
   historical delivery records.
