## 1. Declared horizon (slice 1)

- [x] 1.1 Migration `chronicler_028`: single-row `location_retention_policy` (days 1..30, default 30, CAS version).
- [x] 1.2 `butlers.chronicler.location_retention`: typed read and compare-and-set setter that fail closed.
- [x] 1.3 `GET`/`PUT /api/chronicler/location-retention`, with the PUT owner-only, strict, versioned and audited.

## 2. Causal coverage and raw expiry (later slices)

- [ ] 2.1 Immutable per-row retention clock on `connectors.owntracks_points`.
- [ ] 2.2 Per-row projection coverage committed with projection output.
- [ ] 2.3 Prepared grant, then a connector-owned raw delete with a committed receipt. Lag and failure conditions.

## 3. Summaries and UI (later slices)

- [ ] 3.1 Cumulative movement `path_m`, ~150 m spatial coarsening, tombstones and the forgotten-row receipt.
- [ ] 3.2 Map plaque and source-strip states. Raw-copy cohort coverage.
