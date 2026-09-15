## Context

The original bu-58rlw7 audit deferred large prose corrections for owner review. The shipped
dashboard subsequently settled the disputed behavior: Health owns category slot 5, measurements
mount under Health, contacts route into the entity system, costs route into Spend, and the memory
house-ledger retains URL-backed maturity filtering plus anti-pattern attention.

## Decisions

1. Treat the five findings as stale documentation because the mature implementation and successor
   contracts agree.
2. Keep compatibility aliases explicit. A moved page is not evidence that every imported hook or
   reusable component is dead, but import presence is not proof that its backend path remains live.
3. Make `/entities/index?has=contact`, `/entities/:entityId`, `/health/measurements`, and
   `/spend` the canonical destinations in current documentation.
4. Preserve Health at `--category-5` and General at `--category-4`.
5. Preserve `maturity=anti_pattern` as a working deep link from the memory attention rail.
6. Rebuild every affected MODIFIED requirement as a complete body and archive this superseding
   change so historical requirement delivery remains auditable.
7. Preserve the existing `Contact detail tabs` scenario heading while replacing its body with the
   compatibility-alias guarantee. OpenSpec matches scenario names during archive, so correcting
   the body without renaming the heading keeps the delta archive-safe.

## Non-goals

- No implementation change or redesign.
- No removal or remediation of imported contact hooks/components or their backend-dead readers;
  no removal of compatibility routes, overview cost components, or shared Spend hooks.
- No removal of the anti-pattern attention row.
- No global category-token replacement.
- No change to retry, concurrency, persistence, or schema behavior.
