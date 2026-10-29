## Why

The 2026-10-21 simplicity audit found that backend CI sharding is computed by about 2,000 lines of home-grown tooling: a fresh inventory job, a weighted whole-file partitioner, opaque per-item execution receipts reconciled in preflight, a scheduled weight observer and a 5,561-line weights file. The same Python setup block is also repeated in every ci.yml job. Shard count, not orchestration, sets lane latency. Owner decision bu-w72da2 = A (2026-10-21) chose the standard pipeline; bead bu-29wkwj.2 specifies its design.

## What Changes

- Every Python job in ci.yml uses the `.github/actions/ci-python-environment` composite action. No inline uv setup remains.
- The unit (5) and integration (6) lanes run `pytest-split --splits N --group k` over a committed `.test_durations`, through `scripts/ci_lanes.py`. Job names and required contexts are unchanged.
- Each group records its collected count. check-preflight sums them per lane and compares the sum with one fresh `--collect-only` count of the same selection. That count also feeds the per-lane test budget. This "no group drops or duplicates tests" check replaces the inventory job, the partition and the opaque receipt reconciliation.
- The nightly covered groups also measure per-test durations. A `durations-refresh` job merges them, uploads the file, and opens a PR on Monday's scheduled run or a manual dispatch on main. It never pushes to main.
- The browser (Playwright) job builds its own bundle and no longer waits for guards.
- Retired: the `inventory` and `coverage-inventory` jobs, `scripts/ci_weight_candidates.py`, `scripts/ci_preflight_reconcile.py`, `scripts/ci_frontend_evidence.py` and `.github/workflows/ci-shard-weights.yml`. `make check` and pre-push run the per-lane budget instead of the fresh partition check.
- Kept for the manual watchdog diagnostic only (owned by change `capture-ci-shard-watchdog-diagnostics`): `scripts/ci_partition.py`, `scripts/check_ci_test_shards.py`, `scripts/ci_inventory_collector.py`, `scripts/ci_shard_observer.py` and `.github/ci-test-weights.json`. No required or nightly job reads them. A follow-up retires them with that diagnostic.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ci-shard-assurance`: membership comes from pytest-split groups over the fresh selection; exact-once is a per-lane count total; durations are a committed, PR-refreshed balancing hint; the browser job builds its own bundle.
- `testing`: shard ordering follows pytest-split durations; the local pre-push collection predicate is the per-lane budget.

## Impact

ci.yml, nightly.yml, `scripts/ci_lanes.py`, `scripts/ci_group_count.py`, `scripts/ci_evidence.py`, coverage metadata (schema `ci-coverage.v2`), the Makefile, `scripts/pre_push.py`, contract tests and CI docs. Test selection, markers, workers, timeouts and required check names are unchanged.
