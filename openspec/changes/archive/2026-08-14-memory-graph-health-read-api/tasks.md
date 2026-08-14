## 1. Contract-first coverage tests

- [x] 1.1 Add focused failing `GET /api/memory/stats` tests for complete,
  incomplete, and unknown `meta.graph_health` coverage, including null metrics
  for unknown pools and the no-completed-source fail-closed case.
- [x] 1.2 Extend the nearest memory-stats compatibility tests to prove every
  established `retention_*` data/meta field retains its current value and
  shape when graph-health metadata is added.
- [x] 1.3 Add focused failing Overture tests for visible complete coverage,
  named incomplete coverage, and unknown coverage with no healthy/zero
  fallback or repair control.

## 2. Read-only graph-health API

- [x] 2.1 Add backend Pydantic graph-health coverage/pool models and matching
  frontend TypeScript types with exact enum values and nullability.
- [x] 2.2 Derive `meta.graph_health` from the existing retention fan-out:
  reuse `REAPABLE_EXPIRED_EPISODE_SQL` and `expires_at IS NOT NULL`, preserve
  legacy retention output, represent failed relevant pools as unknown rows,
  and introduce no write or authorization path.
- [x] 2.3 Run the focused backend tests through red then green, including
  grace-window, zero-denominator, partial-failure, empty-completed-source, and
  established consumer compatibility assertions.

## 3. Overture presentation

- [x] 3.1 Render the new graph-health coverage state in `MemoryOverture` using
  the existing read-only/degraded-note language: calm coverage-only completion
  copy, named incomplete/unknown copy, and retry only for the same failed read.
- [x] 3.2 Run focused frontend component/type tests through red then green and
  verify existing retention, ordinary-pool, and catalog-drift notes remain
  independently visible.

## 4. Documentation and verification

- [x] 4.1 Update the frontend/backend API contract documentation with the
  additive graph-health shape, exact numerator/denominator, and fail-closed
  coverage semantics.
- [x] 4.2 Run strict OpenSpec validation, focused backend/frontend suites,
  relevant lint/format/type/build checks, and a scoped diff review confirming
  the explicit no-write/no-job/no-migration/no-repair fences.
