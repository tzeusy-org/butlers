## 1. Provenance-checked scheduler recovery

- [ ] 1.1 Add focused regression coverage for the startup ordering: an active
  TOML override remains TOML-owned, while a removed TOML override is visible as
  a disabled TOML row before module-default recovery.
- [ ] 1.2 Reorder or explicitly phase lifecycle schedule synchronization so
  current TOML membership is established before module-default recovery, without
  allowing the first scheduler tick before handlers are registered.
- [ ] 1.3 Update `ensure_module_default_schedule()` and its registry callers to
  recover only eligible registered, disabled `source='toml'` rows; preserve the
  existing execution payload, leave every disabled `source='db'` row unchanged,
  and exclude an existing disabled TOML-owned `memory_episode_cleanup` row from
  automatic recovery.
- [ ] 1.4 Implement the reclaimed-row `RETURNING`/transaction boundary and
  canonical `scheduler.module_default_recovered` audit entry with only
  control-plane metadata, including non-empty owning-butler and owning-schema
  identity.
- [ ] 1.5 Add real-PostgreSQL failure and concurrency regressions proving audit
  failure rolls back recovery, two contenders yield one transition/audit, a
  restart is a no-op, same-name recoveries across two butlers remain
  attributable, and disabled TOML cleanup with expired history produces no
  recovery, dispatch, or deletion.

## 2. Complete-or-unknown expired-retention API observation

- [ ] 2.1 Centralize or otherwise prove exact reuse of the ordinary cleanup
  predicate (`expires_at < now()`) for the per-source expired-retained count and
  `expires_at IS NOT NULL` denominator; do not read episode content or IDs.
- [ ] 2.2 Extend the memory-stats fan-out, Pydantic
  `RetentionSourceObservation` model, API envelope, and frontend API types
  with additive aggregate fields, exact per-source
  `source_butler`/`source_schema`/`expired_retained_episodes`/
  `retention_eligible_episodes`/`expired_retained_ratio` observations,
  retention status, and the independent `retention_pools_failed` tracker.
- [ ] 2.3 Preserve valid ordinary stats and catalog-drift values when a
  retention-only query fails, while returning null fleet retention aggregate and
  ratio plus named unknown coverage.
- [ ] 2.4 Add focused real-PostgreSQL/API tests for complete healthy,
  complete-degraded, zero-denominator, absent-memory-schema, and
  retention-only-failure cases.
- [ ] 2.5 Extend the degraded-envelope contract so a failed retention source
  cannot silently become a zero or healthy fleet result.

## 3. Owner-facing retention honesty

- [ ] 3.1 Update `MemoryOverture` to consume the exact
  `RetentionSourceObservation` field names without deriving health from
  missing/partial values and without adding mutation controls.
- [ ] 3.2 Add focused component tests for healthy, named degraded, and named
  unknown/incomplete coverage states, constructing the exact per-source wire
  shape and including coexistence with ordinary and catalog degraded-source
  notes.

## 4. Scope, review, and verification gates

- [ ] 4.1 Review the final diff for the explicit fences: no migration,
  historical drain, provenance/evidence mutation, cleanup recovery or
  invocation, schedule toggle, notification, or `source='db'` re-enable is
  introduced.
- [ ] 4.2 Run the targeted scheduler, memory API, degraded-envelope, and
  frontend Overture/type suites; run the repository's required final quality
  gate once after targeted failures are resolved.
- [ ] 4.3 Run `openspec validate harden-memory-retention-schedule-recovery --strict`
  and reconcile the implementation with every scenario in this change.
- [ ] 4.4 Obtain review that specifically verifies atomic audit behavior and
  owning-butler/schema attribution, active-versus-removed TOML provenance,
  cleanup-recovery exclusion, partial-fan-out honesty, and the continued
  separate owner/provenance gates for any historical operation.
