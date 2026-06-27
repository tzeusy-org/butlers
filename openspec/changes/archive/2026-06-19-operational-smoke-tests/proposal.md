## Why

The Butlers review (`docs/archive/butlers-review-improvement-cycles.md`, Cycle 3) asks for
operational proof that the system can **start, migrate, run, recover, and expose
health** — not just that isolated units pass. Today there is rich E2E coverage
(real LLM ecosystem) and good migration coverage, but no fast, LLM-free,
CI-gated tier that proves the deterministic infrastructure boots and recovers
end to end. Non-Negotiable Rule 4 ("the daemon is deterministic infrastructure
... it must be testable, debuggable, and predictable") demands exactly this
proof, and it should run on every push without Docker-heavy E2E cost.

## What Changes

- Introduce a **`smoke` test tier/marker** in the existing `testing` capability:
  fast (no real LLM calls), deterministic operational-proof tests that gate CI.
- Add smoke-test requirements covering five operational surfaces, each grounded
  in real components:
  - **Clean-start**: dependency install + package import + `butlers` entrypoint
    (`butlers.cli:cli`) resolves, matching the `Dockerfile` ENTRYPOINT
    (`uv run --frozen --no-dev butlers`).
  - **Migration**: Alembic core chain applies cleanly from an empty DB to head
    (round-trip for the latest revision) — extends, not duplicates, the existing
    `tests/config/test_migrations.py` coverage.
  - **Daemon lifecycle**: a daemon completes `run_startup` to the
    "accepting connections" signal and `run_shutdown` releases all resources.
  - **Route-inbox recovery**: `accepted`/`processing` rows survive a restart and
    are re-dispatched by `route_inbox_recovery_sweep`.
  - **Dashboard health**: `/api/health` and `/health` return healthy and are
    reachable without authentication (in `_PUBLIC_PATHS`).
- Add a requirement that the smoke tier **runs in CI** (`.github/workflows/ci.yml`)
  as a fast gate, and that each run records **release evidence**
  (command, commit, duration, pass/fail, skipped classes).

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `testing`: add a `smoke` test tier and operational smoke-test requirements
  (clean-start, migration round-trip, daemon lifecycle, route-inbox recovery,
  dashboard health), plus CI smoke-gate and release-evidence requirements.

## Impact

- **Spec**: `openspec/specs/testing/spec.md` (delta under the change).
- **Tests** (implementation, out of scope for this change): a `smoke`-marked
  suite (e.g. under `tests/smoke/` or marked standalone files) and a small CI
  step. Existing `tests/config/test_migrations.py` and
  `tests/daemon/test_startup_guard.py` are referenced for dedup, not rewritten.
- **CI**: `.github/workflows/ci.yml` gains a fast smoke step (no E2E, no real LLM).
- **Components proven**: `src/butlers/cli.py`, `src/butlers/lifecycle.py`,
  `src/butlers/daemon.py`, `src/butlers/migrations.py`,
  `src/butlers/core/route_inbox.py`, `src/butlers/api/app.py`,
  `src/butlers/api/middleware.py`.

## Non-Goals

- **Seven-day soak runs** and **routing-accuracy operational evidence artifacts**
  from the review's Cycle 3. These are operational evidence-gathering exercises,
  not buildable spec deltas, and are deliberately excluded here.
- Replacing or restructuring the existing E2E harness or migration tests.
- Making `/api/health` report deep per-subsystem state beyond what the smoke
  test can deterministically assert; the health requirement asserts the surface
  is reachable and reflects real liveness, not a full subsystem dashboard.
