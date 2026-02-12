# Pytest Runtime Profile (butlers-qkx.1)

Date: 2026-02-13
Issue: `butlers-qkx.1`

## Environment Assumptions

- Kernel: Linux 5.15.0-164-generic
- Python: 3.12.4
- uv: 0.7.17
- CPU cores: 12

## Baseline Command

```bash
uv run pytest tests/ --ignore=tests/test_db.py --ignore=tests/test_migrations.py -q --maxfail=1 --tb=short
```

Result:

- 2105 passed, 1 skipped, 11 warnings
- pytest-reported runtime: 212.49s
- wall clock: 216.58s
- log: `.tmp/test-logs/pytest-baseline-butlers-qkx.1-20260213-053200.log`

## Durations Command

```bash
uv run pytest tests/ --ignore=tests/test_db.py --ignore=tests/test_migrations.py -q --maxfail=1 --tb=short --durations=30
```

Result:

- 2105 passed, 1 skipped, 11 warnings
- pytest-reported runtime: 205.10s
- wall clock: 208.64s
- log: `.tmp/test-logs/pytest-durations30-butlers-qkx.1-20260213-053550.log`

## Top-30 Attribution Summary

From pytest `--durations=30` output:

- `setup`: 20 entries, 57.07s total
- `teardown`: 10 entries, 10.89s total
- `call`: 0 entries
- top-30 subtotal: 67.96s

Interpretation: hotspot time is dominated by fixture startup/teardown, not test bodies.

## Top-30 Hotspot Categories

Actionable grouping from the slowest entries:

1. Container/DB bootstrap setup
- `tests/core/test_core_sessions.py::test_create_session_returns_uuid` (setup 3.19s)
- `tests/config/test_migrations.py::test_core_migrations_create_tables` (setup 3.07s)
- `tests/core/test_db.py::test_provision_creates_database` (setup 2.97s)

2. Module-scoped integration fixture setup (mailbox/relationship/switchboard)
- `tests/integration/test_post_mail.py::...` (setup 2.92s)
- `tests/integration/test_mailbox_module.py::...` (setup 2.79s)
- `tests/integration/test_integration.py::...` (setup 2.78s)
- `tests/tools/test_relationship_types.py::...` (setup 3.03s)

3. Expensive teardown paths
- `tests/core/test_core_scheduler.py::...` (teardown 1.74s)
- `tests/tools/test_relationship_types.py::...` (teardown 1.24s)
- `tests/tools/test_tools_extraction_queue.py::...` (teardown 1.23s)

## References For Follow-On Tasks

- `butlers-qkx.2`: parallel execution evaluation should compare against baseline 216.58s wall and profile 208.64s wall.
- `butlers-qkx.3`: marker expansion should target high-setup integration groups above.
- `butlers-qkx.4`: fixture consolidation should prioritize DB/bootstrap-heavy modules in the top-30 list.
- `butlers-qkx.5`: final recommendation should include this baseline as the "before" dataset.
