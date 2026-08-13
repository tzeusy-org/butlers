## Why

The memory console can already report expired-retention counts, but its
per-source evidence is split across successful source rows and a failed-pool
list. It cannot state, in one typed read model, whether an operator has
complete graph-health coverage or only lower-bound/unknown evidence. A missing
or failed pool must not become a zero, a percentage, or a healthy all-clear.

## What Changes

- Add a read-only `meta.graph_health` observation to `GET /api/memory/stats`.
  It records fleet coverage state and one typed observation for every completed
  or genuinely-unavailable memory pool.
- Reuse the owner-selected cleanup-lag population exactly: the numerator is
  the consolidation-aware reapable-expired episode predicate; the denominator
  is episodes with `expires_at IS NOT NULL`. Pending episodes within the
  cleanup grace window are not degraded.
- Render complete, incomplete, and unknown graph-health coverage distinctly in
  `MemoryOverture`, without adding a mutation, job trigger, repair affordance,
  or retention action.
- Preserve every existing `retention_*` response field and consumer unchanged.
  This is an additive coverage/read-model compatibility decision, not a
  provenance-link coverage metric or a replacement for the retention API.

## Capabilities

### New Capabilities

- `memory-graph-health`: Defines the bounded, read-only memory-pool coverage
  observation and its complete/incomplete/unknown semantics.

### Modified Capabilities

- `dashboard-api`: Extends the existing memory-stats metadata envelope with
  the typed graph-health observation while retaining existing stats semantics.
- `dashboard-domain-pages`: Requires the memory Overture to present the
  graph-health coverage state truthfully and without a write control.
- `memory-retention-policy`: Binds the graph-health cleanup-lag numerator to
  the existing consolidation-aware reap predicate and its grace window.

## Impact

- Backend read model: `src/butlers/api/models/memory.py` and
  `src/butlers/api/routers/memory.py`.
- Frontend API type and presentation: `frontend/src/api/types.ts` and
  `frontend/src/components/memory/MemoryOverture.tsx`.
- Focused backend stats, consumer-compatibility, and Overture component tests,
  plus the frontend API contract documentation. No database migration, job,
  write path, retention run, graph repair, relationship entity-fact operation,
  authorization change, or broad dashboard redesign is included.
