## Why

Connector detail rule rows navigate to Filters without identifying their rule. Owners must search manually, and stale links cannot distinguish a removed rule from an unavailable reader.

## What Changes

- Link each connector routing-rule row to its exact URL-encoded rule id.
- Resolve the navigational target against complete non-archived and archived rule reads.
- Reveal, highlight, scroll to and accessibly focus the exact row once per target change.
- Distinguish loading, unavailable and verified missing targets without mutating routing state.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-ingestion-dispatch-console`: add Connector routing-rule deep-link fidelity alongside Connector Detail and Filters Pipeline. This additive requirement composes with `restore-ingestion-console-spec-coverage` without overwriting its requirements.

## Impact

Connector detail and Filters frontend components, focused Vitest behavior coverage and generated frontend copy inventory. No API, database or live connector action changes.
