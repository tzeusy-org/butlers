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
Core additive migration core_263 follows actual protected core_261. Runtime adapters, Discretion, recorder, APIs and existing UI change together. All original S1-S3 remain mandatory. Genuine pinned Claude recordings are unavailable; no synthetic fixture or schema document substitutes for them. This active change is not adopted or archived pending complete evidence/review.
