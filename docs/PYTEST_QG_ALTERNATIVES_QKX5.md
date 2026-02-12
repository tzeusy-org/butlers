# Pytest Quality-Gate Alternatives (butlers-qkx.5)

Date: 2026-02-13
Issue: `butlers-qkx.5`

## Goal

Compare all acceleration alternatives attempted in `butlers-qkx.*` and set the default quality-gate test command.

## Sources

- Baseline profiling from `docs/PYTEST_RUNTIME_PROFILE_QKX1.md`
- Parallel-path implementation from PR #12 (`butlers-qkx.2`)
- Marker split implementation from PR #13 (`butlers-qkx.3`)
- Fixture-consolidation benchmark from PR #19 (`docs/PYTEST_RUNTIME_PROFILE_QKX4.md` on `agent/butlers-qkx.4`, pending merge)
- Fresh local measurements in this worktree (`butlers-qkx.5`):
  - `.tmp/test-logs/pytest-qkx5-serial-20260213-070255.log`
  - `.tmp/test-logs/pytest-qkx5-parallel-20260213-070724.log`
  - `.tmp/test-logs/pytest-qkx5-unit-20260213-070935.log`
  - `.tmp/test-logs/pytest-qkx5-integration-20260213-071053.log`

## Before/After Benchmark Table

Baseline control for full quality-gate scope:

- Command: `uv run pytest tests/ -q --maxfail=1 --tb=short --ignore=tests/test_db.py --ignore=tests/test_migrations.py`
- Baseline (`qkx.1`): `212.49s` pytest runtime, `216.58s` wall

| Alternative | Scope / command | Before | After | Delta | Notes |
| --- | --- | --- | --- | --- | --- |
| `qkx.2` parallel xdist | Full quality-gate scope (`-n auto`) | 216.58s wall (qkx.1 serial baseline) | 126.42s wall / 125.81s pytest | **-90.16s wall** (~41.6% faster) | Same pass/skip/warning set in local run (2121 passed, 1 skipped, 11 warnings). |
| `qkx.3` marker split | Run `-m unit` + `-m integration` sequentially | 216.58s wall (qkx.1 serial baseline) | 73.00s + 439.49s = 512.49s wall | **+295.91s wall** (~136.6% slower) | Useful for targeted loops (`-m unit`), not a faster full-gate default. |
| `qkx.4` shared Postgres fixtures | Targeted DB-heavy 491-test slice | 186.00s wall (slice baseline) | 150.96s wall / 148.44s pytest | **-35.04s wall** (~18.8% faster) | Strong improvement for fixture-heavy modules; currently pending merge in PR #19. |

Control re-check in this worktree (current serial):

- Full-scope serial: `221.16s` wall / `217.66s` pytest (`2121 passed, 1 skipped, 11 warnings`)
- This remains much slower than full-scope parallel (`126.42s` wall).

## Recommendation

Set `make test-qg` to the parallel xdist path and keep an explicit serial fallback.

- **Default command**: `make test-qg` (parallel via `-n auto`)
- **Fallback/debug command**: `make test-qg-serial` (serial)
- **Compatibility alias**: `make test-qg-parallel` (same as default)

## Tradeoffs

- **Speed**: Parallel full-scope gate is the best measured improvement (~41.6% faster wall clock vs qkx.1 baseline).
- **Stability**: Serial fallback remains required for investigating order-dependent failures and for environments where xdist behavior needs to be ruled out.
- **Complexity**: Marker split adds cognitive/operational overhead and is not a faster full-coverage gate when both subsets are required; keep it as a development workflow tool, not default quality gate.
