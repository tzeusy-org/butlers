# Pytest Quality-Gate Baseline and Bottlenecks (butlers-0h6)

Date: 2026-02-13
Issue: `butlers-0h6`

## Goal

Capture an up-to-date runtime baseline for the quality-gate pytest scope and identify dominant runtime contributors.

## Baseline Command and Runtime

Command:

```bash
make test-qg
```

Recorded run:

- Timestamp (UTC): `2026-02-13T05:06:20Z`
- Log: `.tmp/test-logs/butlers-0h6-test-qg-baseline-20260213T050620Z.log`
- Result: `2211 passed, 1 skipped, 13 warnings`
- Pytest runtime: `80.30s`
- Wall clock (`time -p real`): `83.27s`

## Profiling Command and Runtime

Command:

```bash
uv run pytest tests/ -q --maxfail=1 --tb=short --ignore=tests/test_db.py --ignore=tests/test_migrations.py -n auto --durations=40 --durations-min=0.20
```

Recorded run:

- Timestamp (UTC): `2026-02-13T05:07:52Z`
- Log: `.tmp/test-logs/butlers-0h6-test-qg-profile-20260213T050752Z.log`
- Result: `2211 passed, 1 skipped, 13 warnings`
- Pytest runtime: `87.85s`
- Wall clock (`time -p real`): `89.04s`

## Profiling Output Summary (`--durations=40`)

### Top hotspots (excerpt)

- `15.45s setup` `tests/core/test_core_state.py::test_get_existing_key`
- `13.67s setup` `tests/integration/test_mailbox_module.py::TestMailboxPost::test_post_returns_uuid`
- `12.95s setup` `tests/tools/test_extraction.py::TestHandleMessageWithExtraction::test_both_fail_gracefully`
- `12.85s setup` `tests/daemon/test_heartbeat.py::test_tick_all_butlers_ticks_all_except_heartbeat`
- `12.83s setup` `tests/tools/test_extraction.py::TestExtractSignals::test_cc_failure_returns_empty`
- `12.67s setup` `tests/integration/test_mailbox_integration.py::TestMailboxPost::test_post_inserts_message_with_all_fields`
- `12.38s setup` `tests/tools/test_decomposition.py::test_multi_domain_message_produces_multiple_routes`
- `12.25s setup` `tests/tools/test_extraction.py::TestExtractionLogging::test_below_threshold_logged_with_error`
- `12.15s setup` `tests/tools/test_extraction.py::TestCustomSchemas::test_schema_rejects_unregistered_type`
- `11.91s setup` `tests/core/test_core_sessions.py::test_create_session_returns_uuid`

### Attribution totals from slowest-40 section

- `setup`: 32 entries, `309.61s`
- `teardown`: 8 entries, `34.92s`

Interpretation: runtime cost is dominated by setup/fixture work, with teardown as a secondary contributor.

### Hotspot grouping by area (slowest-40 aggregate)

- `tests/tools/*`: 11 entries, `110.34s`
- `tests/integration/*`: 11 entries, `95.23s`
- `tests/core/*`: 10 entries, `71.84s`
- `tests/daemon/*`: 2 entries, `19.77s`
- `tests/config/*`: 3 entries, `19.36s`
- `tests/test_approvals_models.py`: 2 entries, `19.35s`
- `tests/features/*`: 1 entry, `8.64s`

## Findings

1. The current quality-gate baseline is ~83s wall-clock on this run, with all tests passing.
2. The duration profile remains setup-heavy; test-body execution is not a dominant cost center.
3. Optimization work should prioritize fixture/bootstrap setup paths in `tests/tools`, `tests/integration`, and `tests/core`.

## Related Artifacts

- Prior baseline/profile reference: `docs/PYTEST_RUNTIME_PROFILE_QKX1.md`
- Alternatives/recommendation context: `docs/PYTEST_QG_ALTERNATIVES_QKX5.md`
