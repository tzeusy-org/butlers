## Why

Injected memory context currently preserves fact and rule content but discards
the stable identifiers needed by the existing confirm, helpful, and harmful
feedback actions. A session can use the memory, but it cannot reliably apply
feedback to the item it actually saw.

## What Changes

- Render a bounded opaque type-and-UUID reference beside each local fact or
  rule admitted to injected context.
- Let the existing feedback tools resolve that reference under the module's
  server-held read ceiling and live-row rules.
- Make missing, stale, wrong-type, and unauthorized references
  indistinguishable content-free refusals with no mutation.
- Count references inside the existing section and total context budgets.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-memory`: Preserve actionable stable references in injected local
  fact/rule context without changing the privacy or token ceilings.

## Impact

- `src/butlers/modules/memory/tools/` context and feedback boundaries.
- Memory storage feedback mutations and their authorization predicates.
- Focused memory context, tool delegation, and real-Postgres privacy tests.
