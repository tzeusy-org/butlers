# Pytest Acceleration Benchmark (butlers-vrs)

Date: 2026-02-13
Issue: `butlers-vrs`

## Objective

Benchmark at least two concrete pytest acceleration strategies for quality-gate workflows, including one explicit parallel path and one non-parallel optimization.

## Baseline (Before)

Reference full-scope serial baseline from `docs/PYTEST_RUNTIME_PROFILE_QKX1.md`:

- Command: `uv run pytest tests/ -q --maxfail=1 --tb=short --ignore=tests/test_db.py --ignore=tests/test_migrations.py`
- Result: `2105 passed, 1 skipped, 11 warnings`
- Wall clock: `216.58s`

## Experiment Evidence (this worktree)

- `.tmp/test-logs/pytest-vrs-exp2-unit-serial-20260213-145441.log`
- `.tmp/test-logs/pytest-vrs-exp2-unit-parallel-n4-20260213-145642.log`
- `.tmp/test-logs/pytest-vrs-strategy1-parallel-n4-20260213-144750.log`
- `.tmp/test-logs/make-test-qg-vrs-attempt1-20260213-150128.log`

## Strategy 1 (Non-Parallel): Marker-Focused Unit Run

Command:

```bash
.venv/bin/pytest tests/ -m unit -q --maxfail=1 --tb=short \
  --ignore=tests/test_db.py --ignore=tests/test_migrations.py
```

Before/After:

- Before: `216.58s` wall (full-scope serial baseline)
- After: `114.87s` wall
- Delta: `-101.71s` (~46.96% faster)

Observed output:

- `1854 passed, 358 deselected, 11 warnings in 109.19s`

Tradeoffs:

- Pros: fast local feedback loop without parallel execution complexity.
- Cons: reduced coverage (`358 deselected`), so this is not a full quality-gate replacement.

## Strategy 2 (Explicit Parallel): Same Unit Scope with xdist

Command:

```bash
.venv/bin/pytest tests/ -m unit -q --maxfail=1 --tb=short \
  --ignore=tests/test_db.py --ignore=tests/test_migrations.py -n 4
```

Before/After:

- Before: `114.87s` wall (Strategy 1 serial unit run)
- After: `56.12s` wall
- Delta: `-58.75s` (~51.14% faster)

Observed output:

- `1854 passed, 13 warnings in 55.16s`

Tradeoffs:

- Pros: strongest measured speedup while preserving unit-scope coverage.
- Cons: xdist adds scheduling overhead/complexity and can increase nondeterminism risk for poorly isolated tests.

## Full Quality-Gate Parallel Check (Required Gate Context)

Required gate command (`make test-qg`) was also executed successfully in this worktree:

- Log: `.tmp/test-logs/make-test-qg-vrs-attempt1-20260213-150128.log`
- Result: `2211 passed, 1 skipped, 15 warnings`
- Wall clock: `129.15s`

Additional earlier full-scope parallel attempts captured instability noise during contention:

- `.tmp/test-logs/pytest-vrs-strategy1-parallel-n4-20260213-144750.log`
  - `1302 passed, 1 skipped, 1 error`
  - Docker teardown APIError (`did not receive an exit event`)

These align with the known teardown flake tracked in `butlers-kle`.

## Summary Recommendation

1. Use unit-scope marker runs as a non-parallel fast lane for iteration.
2. Use explicit parallelism (`-n` workers) for further acceleration on stable scopes.
3. Keep full quality-gate execution in CI/release checks, with awareness of known Docker teardown flake risk on DB-backed tests.
