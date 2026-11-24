# Serving evidence for runtime attempts

## Why
Requested routing identity currently appears without separate runtime serving observations, and Claude can exit zero with a failed terminal result. The owner needs qualified evidence without fabricated serving or cost claims.

## What Changes
- Bound adapter-owned identity/version/usage/error records and isolate each invocation.
- Atomically persist aggregate and per-model evidence with owned replay keys.
- Expose requested versus served/CLI-reported identity and cost comparisons in the dossier and Spend.
- Preserve all existing routing, authorization, retry, quota and privacy contracts.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `model-catalog`: normalized serving evidence and requested-route separation.
- `core-spawner`: failed terminal results and invocation-owned persistence.
- `dashboard-visibility`: qualified serving evidence in session details.
- `dashboard-spend-dashboard`: separate model/cost comparison evidence.
- `build-reproducibility`: fixed image version comparison.

## Impact
Core additive migration `core_290` follows `core_289`. Runtime adapters, the shared model-call kernel (Discretion), the recorder, APIs and existing UI change together. Genuine Claude Code 2.1.316 recordings (two-model success, `error_max_turns`, no `modelUsage`) back the adapter contract; their provenance lives in `tests/fixtures/claude_served_identity/README.md`. Synthetic events cover only malformed, privacy and overflow controls.
