## Context

The original bu-58rlw7 audit deferred large prose corrections for owner review. The shipped
dashboard subsequently settled the disputed behavior: Health owns category slot 5, measurements
mount under Health, contacts route into the entity system, costs route into Spend, and the memory
house-ledger retains URL-backed maturity filtering plus anti-pattern attention.

## Decisions

1. Treat the five findings as stale documentation because the mature implementation and successor
   contracts agree.
2. Keep compatibility aliases explicit. A moved page is not evidence that every supporting API,
   hook, or reusable component is dead.
3. Make `/entities/index?has=contact`, `/entities/:entityId`, `/health/measurements`, and
   `/spend` the canonical destinations in current documentation.
4. Preserve Health at `--category-5` and General at `--category-4`.
5. Preserve `maturity=anti_pattern` as a working deep link from the memory attention rail.
6. Rebuild every affected MODIFIED requirement as a complete body and archive this superseding
   change so historical requirement delivery remains auditable.

## Non-goals

- No implementation change or redesign.
- No removal of compatibility routes, contacts APIs/components, overview cost components, or
  shared Spend hooks.
- No removal of the anti-pattern attention row.
- No global category-token replacement.
- No change to retry, concurrency, persistence, or schema behavior.
