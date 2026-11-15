## 1. Declared horizon (slice 1)

- [x] 1.1 Migration `chronicler_028`: single-row `location_retention_policy` (days 1..30, default 30, CAS version).
- [x] 1.2 `butlers.chronicler.location_retention`: typed read and compare-and-set setter that fail closed.
- [x] 1.3 `GET`/`PUT /api/chronicler/location-retention`, with the PUT owner-only, strict, versioned and audited.

## 2. Causal coverage and raw expiry (later slices)

- [x] 2.1 Immutable per-row retention clock on `connectors.owntracks_points`: `core_340`
  `retention_at` (trigger-derived from `ts`/`recorded_at` by the declared raw-age basis,
  caller value ignored, `ts`/`recorded_at`/`retention_at` updates refused, legacy rows
  backfilled).
- [ ] 2.2 Per-row projection coverage committed with projection output. Partial:
  `chronicler_032` coverage and open-carry tables plus `butlers.chronicler.projection_coverage`;
  `owntracks.points` writes coverage in its projection savepoint; `chronicler_033` multi-key
  open carries; `owntracks.place_cluster` and `owntracks.ssid_presence` adopt the same ledger
  (`evaluated_no_output` for rows they deliberately emit nothing for). Remaining: the source
  revision/digest and SSID mapping revision on each record, coverage invalidation on a mapping
  change or owner correction, a durable failure-attempt record, anti-join discovery of
  uncovered rows, and role readbacks.
- [ ] 2.3 Prepared grant, then a connector-owned raw delete with a committed receipt. Lag and failure conditions.

## 3. Summaries and UI (later slices)

- [ ] 3.1 Cumulative movement `path_m`, ~150 m spatial coarsening, tombstones and the forgotten-row receipt.
- [ ] 3.2 Map plaque and source-strip states. Raw-copy cohort coverage.
