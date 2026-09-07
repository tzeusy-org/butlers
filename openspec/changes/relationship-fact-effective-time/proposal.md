## Why

`relationship.entity_facts` records when an assertion was created and observed, but it cannot
record when the asserted relationship was true. Treating `created_at`, `observed_at`, `last_seen`,
confidence, or assertion lifecycle as a substitute would manufacture history and would make a
closed historical relationship indistinguishable from a retracted belief.

The representation also needs an occurrence identity before implementation begins. The same
subject, predicate, and object can hold during two separate periods, while a correction must replace
one specific asserted period without erasing the other. The current active-SPO uniqueness rule
cannot express that distinction.

## What Changes

- Add nullable `effective_from` and `effective_to` `TIMESTAMPTZ` bounds, independent nullable
  per-bound precision columns, and a nullable `effective_period_id UUID` occurrence key to
  `relationship.entity_facts`.
- Define a half-open `[effective_from, effective_to)` interval. A null timestamp with null precision
  means an unknown bound; a null timestamp with `unbounded` precision means an explicitly open
  bound. Both unknown bounds mean unspecified effective time, not truth for all time.
- Define `instant`, `day`, `month`, `year`, and `unbounded` as the only stored precision tokens, with
  UTC normalization for exact and coarse inputs.
- Canonicalize omitted and explicit JSON `null` temporal arguments identically. A non-null
  `corrects_fact_id` alone selects correction mode and represents a correction to wholly unknown
  bounds; correction input is a complete desired replacement packet, not a partial patch.
- Scope active uniqueness and idempotency to the effective occurrence. The null period id is the
  backward-compatible default occurrence; a stable non-null period id represents a distinct repeat
  of the same triple.
- Make temporal corrections compare-and-swap operations against one exact active fact version.
  Successful corrections preserve the occurrence id, supersede that assertion version, create a
  replacement, and carry its evidence forward. A stale competing correction fails without writes.
- Keep existing reads assertion-current through `validity = 'active'` alone. This change adds no
  implicit effective-now filter and no as-of read surface.
- Roll out schema and writer support in stages. The expand migration keeps the deployed writer's
  active-SPO conflict target, the transition writer works with both index layouts and fails closed on
  temporal intent before cutover, and a later cutover removes the legacy index only after old-writer
  absence is proven. Repeated periods become writable only after that cutover.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `relationship-facts`: the structural triple store and its central writer gain explicit effective
  time, repeated-period identity, replay, correction, approval, and concurrency semantics.

## Impact

- Specification only in this draft. No schema, data, code, runtime, provider, endpoint, archive,
  merge, or migration revision is created or reserved.
- `bu-h3b7t` remains the implementation owner after exact owner acceptance of this contract.
- `bu-4ss0u` remains the owner of predicate-cardinality and overlap enforcement over represented
  periods.
- `bu-1ypjo` remains the owner of opt-in `as_of` MCP and REST reads.
- The structural store remains `relationship.entity_facts`; per-butler memory `facts` remains the
  separate narrative store. No cross-store inference, copy, join, or synthetic backfill is added.

## Acceptance Status

This artifact resolves routine representation choices so implementation can be reviewed against an
executable contract. It is still a proposal. Drafting, validation, or CI does not constitute owner
acceptance, implementation authority, archive authority, or approval of any database effect.
