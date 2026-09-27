## 1. Regression Coverage

- [x] 1.1 Add read-model and aggregate-route tests that retain trigger-breakdown failed sources independently from scalar `meta.sources_degraded`.
- [x] 1.2 Add dashboard tests proving incomplete trigger breakdowns cannot produce a trigger-cluster claim and that legacy `?butler=` detail links use the global detail lookup.

## 2. Implementation

- [x] 2.1 Return typed trigger-breakdown buckets and degraded sources from the sessions read model, then expose the additive aggregate response field.
- [x] 2.2 Extend frontend aggregate typing and gate the sessions verdict's trigger attribution while preserving its scalar failure count.

## 3. Contract and Documentation Alignment

- [x] 3.1 Sync dashboard visibility and dashboard API main specs from the validated deltas.
- [x] 3.2 Correct the frontend API contract and dashboard data-flow Excalidraw/SVG detail-route reference while documenting ignored legacy `?butler=` input.

## 4. Verification

- [x] 4.1 Run focused backend and frontend tests, formatting/lint checks, strict OpenSpec validation, and session-link guards.
