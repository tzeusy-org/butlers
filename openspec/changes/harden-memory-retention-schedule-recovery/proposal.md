## Why

Memory maintenance promises to enforce configured retention, yet a module-default
schedule can become permanently disabled after its TOML override is removed. The
current reclaim path converts the row back to module ownership but leaves its
disabled state intact, so a historical configuration transition can impersonate
a healthy maintenance system. At the same time, `/api/memory/stats` cannot say
when TTL-expired episodes remain retained, and a failed pool could make a
partial aggregate look calm.

This change establishes a narrow, fail-closed recovery and observation contract
before any historical deletion is considered. It makes the module-default
schedule transition durable and auditable, and makes retention lag visible
without changing schedule state or deleting retained data.

## What Changes

- Define the sole automatic TOML-orphan recovery: a registered module default
  other than `memory_episode_cleanup` may reclaim and re-enable only its
  matching `source='toml'` row. A disabled `source='db'` row remains
  operator-owned and untouched, and a disabled TOML-owned cleanup row remains
  fenced from automatic recovery.
- Require the reclaim and its canonical audit entry to commit atomically, with
  exactly-once observable behavior under concurrent startup, rollback on audit
  failure, and owning-butler/schema attribution in the shared audit log. The
  recovery preserves the row's existing cadence and runtime payload rather than
  rewriting it from defaults.
- Add an expired-retained aggregate and per-source observation to
  `GET /api/memory/stats`, calculated with the same predicate as the cleanup
  job. A failed stats pool is explicitly unknown/degraded, never a zero or
  healthy result.
- Require `MemoryOverture` to name retention degradation and incomplete source
  coverage instead of rendering a clean all-clear.
- Explicitly prohibit this change from draining, deleting, or automatically
  re-enabling or dispatching a disabled TOML-owned cleanup schedule, including
  one with a stale due time and expired history. Episode provenance/evidence
  semantics and any owner-authorized switchboard drain are separately scoped
  follow-on work.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `core-scheduler`: constrain module-default TOML reclaim provenance and make
  the state transition plus audit atomic, idempotent, and concurrency-safe.
- `memory-retention-policy`: define the retention-recovery boundary, the
  cleanup-predicate-based expired-retained observation, and the no-drain
  guardrail.
- `dashboard-api`: extend the memory-stats fan-out contract with truthful
  expired-retention aggregates, per-source coverage, and degraded-source
  semantics.
- `dashboard-domain-pages`: require the Memory Overture to render named
  retention lag and incomplete coverage rather than a false healthy state.

## Impact

- Scheduler boundary: `src/butlers/core/scheduler.py::ensure_module_default_schedule`
  and its module-startup callers, with `public.audit_log` as the durable audit
  spine and `/api/audit-log` as the read surface.
- Retention/statistics boundary: memory cleanup's episode expiry predicate,
  `src/butlers/api/routers/memory.py::get_memory_stats`, API models and
  frontend types, and `frontend/src/components/memory/MemoryOverture.tsx`.
- Verification spans scheduler transition/rollback/concurrency tests,
  real-Postgres stats fan-out coverage, degraded-envelope contracts, and
  focused UI rendering tests. No migration, live database operation, schedule
  toggle, or historical cleanup is authorized by this change.
