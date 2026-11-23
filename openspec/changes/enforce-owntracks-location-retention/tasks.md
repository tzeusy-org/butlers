## 1. Declared horizon (slice 1)

- [x] 1.1 Migration `chronicler_028`: single-row `location_retention_policy` (days 1..30, default 30, CAS version).
- [x] 1.2 `butlers.chronicler.location_retention`: typed read and compare-and-set setter that fail closed.
- [x] 1.3 `GET`/`PUT /api/chronicler/location-retention`, with the PUT owner-only, strict, versioned and audited.

## 2. Causal coverage and raw expiry (later slices)

- [x] 2.1 Immutable per-row retention clock on `connectors.owntracks_points`: `core_340`
  `retention_at` (trigger-derived from `ts`/`recorded_at` by the declared raw-age basis,
  caller value ignored, `ts`/`recorded_at`/`retention_at` updates refused, legacy rows
  backfilled).
- [x] 2.2 Per-row projection coverage committed with projection output:
  `chronicler_032` coverage and open-carry tables plus `butlers.chronicler.projection_coverage`;
  `owntracks.points` writes coverage in its projection savepoint; `chronicler_033` multi-key
  open carries; `owntracks.place_cluster` and `owntracks.ssid_presence` adopt the same ledger
  (`evaluated_no_output` for rows they deliberately emit nothing for); `chronicler_034` content
  digest and config revision (SSID mapping included) on each record, with revision-keyed
  invalidation, voiding of every adapter's held carries when the revision saved beside them is
  not the current one (an SSID mapping change also replays), a per-adapter attempt record, and
  a bounded anti-join count of settled uncovered rows. Owner episode corrections deliberately
  do not invalidate coverage. Place and SSID coverage is read back after committing under the
  runtime role. Rows discovery finds are counted on the attempt record, not re-projected: they
  have no current coverage, so raw expiry never deletes them (fail-closed). Draining them with a
  window-scoped rebuild (rewind to the episode start, tombstone reshaped episodes, replay through
  the real adapter) is a follow-up, not part of this task.
- [ ] 2.3 Prepared grant, then a connector-owned raw delete with a committed receipt. Lag and failure conditions.
  Partial (deletes nothing): `chronicler_036` append-only grant receipts and items, and
  `location_retention_grant.prepare_retention_grant` (no caller yet), which grants only rows
  with exact current-revision, current-digest coverage from every adapter, no open member, an
  expired retention clock and a settled arrival. The place-cluster projection shares the
  point projection's resurrection floor. Remaining: holder readbacks and the ready grant, the
  connector delete and its receipt, reconciliation, and the lag and failure conditions.

## 3. Summaries and UI (later slices)

- [ ] 3.1 Cumulative movement `path_m`, ~150 m spatial coarsening, tombstones and the forgotten-row receipt.
  Partial (deletes nothing): owntracks.points records `path_m` (null when not exactly
  rebuildable); `butlers.chronicler.spatial_precision` scheme `eqcell-150m-v1`;
  `chronicler_035` append-only receipts and tombstones with `location_forgetting.record_forgetting`
  (no caller yet); the point and place-cluster projections refuse a tombstoned source. Remaining: applying the
  coarsening to place/movement summaries, carryover and caches, and the episode/evidence-chain
  readers of tombstones, all with raw expiry (2.3).
- [ ] 3.2 Map plaque and source-strip states. Raw-copy cohort coverage.
