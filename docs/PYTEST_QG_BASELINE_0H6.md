# Pytest Quality-Gate Baseline and Bottlenecks (butlers-0h6)

Date: 2026-02-13
Issue: `butlers-0h6`

## Goal

Capture an up-to-date runtime baseline for the quality-gate pytest scope and identify dominant runtime contributors.

## Baseline Command and Runtime

Command:

```bash
uv run pytest tests/ -q --tb=short --ignore=tests/test_db.py --ignore=tests/test_migrations.py -n auto --durations=40
```

Recorded run:

- Timestamp (UTC): `2026-02-13T03:51:56Z`
- Log: `.tmp/test-logs/pytest-qg-durations40-xdist-butlers-0h6-20260213-035156.log`
- Result: `2211 passed, 1 skipped, 13 warnings, 2 errors`
- Pytest runtime: `241.64s`
- Wall clock (`time -p real`): `242.95s`

Run stability note:

- Both errors were teardown failures from Docker/Testcontainers container removal (`docker.errors.APIError` 500: "could not kill ... did not receive an exit event").

## Profiling Output Summary (`--durations=40`)

### Top hotspots (excerpt)

- `60.33s setup` `tests/integration/test_integration.py::TestButlerStartupIntegration::test_all_subsystems_share_pool`
- `60.31s setup` `tests/integration/test_mailbox_integration.py::TestPostMail::test_post_mail_preserves_sender_identity`
- `59.43s setup` `tests/integration/test_mailbox_module.py::TestMailboxPost::test_post_returns_uuid`
- `59.33s setup` `tests/daemon/test_heartbeat.py::test_tick_all_butlers_ticks_all_except_heartbeat`
- `55.69s setup` `tests/integration/test_idempotent_ingestion.py::test_interaction_log_first_call_inserts`
- `55.15s setup` `tests/integration/test_mailbox_integration.py::TestMailboxPost::test_post_default_values`
- `53.10s setup` `tests/daemon/test_heartbeat.py::test_tick_all_with_real_switchboard_list_butlers`
- `44.72s setup` `tests/core/test_active_session_detection.py::TestSessionsActive::test_newly_created_session_is_active`
- `44.58s setup` `tests/features/test_vcard.py::test_export_single_contact_basic`
- `43.89s setup` `tests/core/test_core_scheduler.py::test_sync_disables_removed_tasks`

### Attribution totals from slowest-40 section

- `setup`: 29 entries, `1160.07s`
- `teardown`: 11 entries, `202.41s`
- `call`: 0 entries

Interpretation: runtime cost is heavily skewed toward setup (fixture/bootstrap) and secondarily teardown, not test function body execution.

### Hotspot grouping by area (slowest-40 aggregate)

- `tests/integration/*`: 11 entries, `428.81s`
- `tests/core/*`: 10 entries, `324.40s`
- `tests/tools/*`: 6 entries, `187.30s`
- `tests/daemon/*`: 4 entries, `144.60s`
- `tests/config/*`: 2 entries, `54.39s`
- `tests/features/*`: 1 entry, `44.58s`
- `tests/test_approvals_models.py`: 6 entries, `178.40s`

## Findings

1. Dominant cost is environment/setup work (container/database/bootstrap fixture paths), not test-body execution.
2. Teardown is a secondary but meaningful contributor and currently unstable due Docker container removal failures.
3. Integration/core-heavy suites dominate the slowest bucket and remain primary optimization targets for quality-gate runtime work.

## Related Artifacts

- Prior baseline/profile reference: `docs/PYTEST_RUNTIME_PROFILE_QKX1.md`
- Alternatives/recommendation context: `docs/PYTEST_QG_ALTERNATIVES_QKX5.md`
