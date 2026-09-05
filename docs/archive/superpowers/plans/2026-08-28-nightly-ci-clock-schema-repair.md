> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Test-setup-only CI clock/schema repair (zero test delta, no production change) landed in the Nightly workflow.
> **Successor:** `.github/workflows/nightly.yml`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Nightly CI Clock and Schema Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the Nightly schema and faketime lanes by using the canonical migration database bootstrap, making filesystem fixtures clock-hermetic, and excluding only verified PostgreSQL-clock tests from the Python-only faketime matrix.

**Architecture:** Production behavior and workflow timeouts remain unchanged. Test setup must use the clock owned by the behavior under test: canonical privileged migration setup for schema tests, the shifted process clock for filesystem mtimes, and the existing `pg_clock` marker for tests whose semantics deliberately cross Python and PostgreSQL clocks.

**Tech Stack:** Python 3.12, pytest, testcontainers/PostgreSQL, libfaketime, Ruff, GitHub Actions.

## Global Constraints

- Work only in `.worktrees/fix-nightly-ci` on `fix/nightly-clock-and-schema-gates`.
- Do not change production clock logic, migration fail-closed behavior, Nightly filters, timeouts, retries, or watchdogs.
- Preserve the merged insight-clock fix from `496684087` and keep it selected in the faketime matrix.
- Add `pytest.mark.pg_clock` only to the 24 nodes proven to mix shifted Python time with real PostgreSQL time in run `33070433983`.
- Add no tests: net test-definition delta is zero.
- Use a temporary extracted `libfaketime` package; do not install system packages.

---

### Task 1: Use the canonical migration database bootstrap

**Files:**
- Modify: `tests/config/test_schema_matrix_migrations.py`

**Interfaces:**
- Consumes: `create_migration_db(postgres_container, db_name)` and `migration_db_name()` from `butlers.testing.migration`.
- Produces: the same one-database schema matrix after staging the trusted `core_196` prerequisite.

- [ ] Confirm RED on current main:
  `uv run pytest tests/config/test_schema_matrix_migrations.py::test_one_db_schema_table_matrix_for_core_and_enabled_modules -q --tb=short -m '(nightly or integration) and not bench and not perf' -n 0`
  Expected: `restore-drill bootstrap installer is missing or untrusted`.
- [ ] Import `create_migration_db` and `migration_db_name`.
- [ ] Replace the call to `_create_db(postgres_container, _unique_db_name())` with `create_migration_db(postgres_container, migration_db_name())`.
- [ ] Delete `_unique_db_name`, `_create_db`, and the now-unused `uuid` import. Retain `create_engine` and `text`, which `_fetch_tables_by_schema` still uses. Do not alter `core_196`.
- [ ] Re-run the exact focused command and expect PASS.

### Task 2: Make filesystem fixtures use the shifted process clock

**Files:**
- Modify: `tests/api/test_system.py`
- Modify: `tests/scripts/test_pg_dump_run_sentinel.py`

**Interfaces:**
- Consumes: `time.time()` as intercepted by libfaketime and the script's existing `BACKUP_RETAIN_DAYS` input.
- Produces: eight filesystem tests whose fixtures stay fresh under `+45d` and `+120d` without changing production retention.

- [ ] Preserve the recorded RED loop: the two backup API nodes and six sentinel cases fail under `+45d` with stale/pruned artifacts.
- [ ] In `_write_fresh_dump`, after closing the gzip file, set both atime and mtime with `shifted_now = time.time(); os.utime(dump, (shifted_now, shifted_now))`.
- [ ] In sentinel `_run`, set `env["BACKUP_RETAIN_DAYS"] = "10000"` before applying caller overrides. These tests exercise receipts and publication, not retention.
- [ ] Run the eight-node command under both `FAKETIME='+45d'` and `FAKETIME='+120d'`; expect eight passes per offset.
- [ ] Run the same nodes unshifted; expect eight passes.

### Task 3: Classify the 24 PostgreSQL-clock nodes

**Files:**
- Modify: `roster/relationship/tests/test_jobs.py`
- Modify: `tests/integration/test_commitment_escalation.py`
- Modify: `tests/config/test_education_curriculum_receipt_db.py`
- Modify: `tests/migrations/test_runtime_attention_outbox_migration.py`
- Modify: `tests/migrations/test_runtime_probe_control_receipts_migration.py`
- Modify: `tests/integration/test_dispatch_outcome_recorder.py`
- Modify: `tests/modules/memory/test_consolidation_lifecycle.py`
- Modify: `tests/modules/test_calendar_reminder_integration.py`
- Modify: `tests/integration/test_decomposition_flow.py`
- Modify: `tests/api/test_runtime_probe_control_receipts_db.py`
- Modify: `tests/integration/test_infra_state_condition_suppression_roundtrip.py`
- Modify: `tests/integration/test_pipeline_ingress_partition_durability.py`

**Interfaces:**
- Consumes: existing `pg_clock` marker declared in `pyproject.toml` and already excluded by `.github/workflows/nightly.yml`.
- Produces: ordinary CI still executes every test; Python-only faketime excludes only tests whose pass/fail depends on the unshifted PostgreSQL clock.

- [ ] Add function-level `@pytest.mark.pg_clock` to exactly these nodes:
  - relationship: `test_insight_scan_contact_candidates_include_entity_and_event_metadata`
  - commitment: `TestDeadlineShortensGrace::test_req_commitment_lifecycle_005_deadline_inside_grace_surfaces_before_it`
  - education: `test_sweep_settles_abandoned_receipt_and_releases_guard`, `test_sweep_is_idempotent`, `test_correlate_finds_map_created_after_trigger`, `test_correlate_reports_calibration_ready_from_flow_state`
  - runtime attention: `test_concurrent_half_open_failures_emit_one_deterministic_edge`
  - runtime probe migration: `test_a_receipt_past_its_retention_bound_is_removable`
  - dispatch outcomes: `test_fifth_failure_opens_once_and_success_closes`, `test_equal_timestamp_concurrent_failures_have_one_deterministic_edge`, `test_concurrent_failed_half_open_probes_create_one_reopening_episode`
  - memory lifecycle: `test_scheduled_run_claims_pending_and_only_retry_eligible_failed_episodes`, `test_private_memory_claim_path_does_not_retry_failed_episodes`, `test_registered_relationship_admin_dry_run_leaves_due_failed_retry_for_scheduler`, `test_due_failed_claim_is_race_safe_between_scheduler_runs`, `test_failure_transition_is_fenced_sanitized_and_retryable`, `test_expired_claim_cannot_persist_a_stale_failure`, `test_replaced_claim_cannot_persist_artifacts_or_terminal_lifecycle`
  - calendar: `test_native_refresh_retains_and_dispatches_overdue_unnotified_instance`
  - decomposition: `test_real_route_boundary_persists_anchored_facts_and_rejects_missing_anchor`
  - runtime probe API: `test_the_retention_bound_is_expiry_plus_five_seconds`, `test_purging_an_expired_receipt_does_not_free_a_live_one`
  - infra state: `TestPausedConnectorNeverEntersLedger::test_paused_connector_creates_no_condition`
  - partition durability: `test_ensure_partition_survives_dedupe_transaction_rollback`
- [ ] Run `pytest --collect-only` with `-m pg_clock` over the 12 files and assert these 24 nodes are selected.
- [ ] Run all 24 nodes unshifted; they must pass and remain ordinary-CI coverage.
- [ ] Under both faketime offsets, collect with the workflow marker expression and assert none of these nodes is selected.

### Task 4: Consolidated verification and publication

- [ ] Remove the sole direct `from conftest import` consumer exposed by the merged fixture-topology cleanup: define `docker_available` locally in `tests/modules/memory/test_consolidation_lifecycle.py` using the repository's standard `shutil.which("docker")` pattern. Verify the file collects without `PYTHONPATH`.
- [ ] Run the merged insight test under both offsets; it must pass and remain selected.
- [ ] Run the exact Nightly schema command.
- [ ] Run the exact faketime workflow command at `+45d` and `+120d` when practical; otherwise dispatch the Nightly workflow on the pushed exact head and wait for both legs.
- [ ] Run Ruff check/format on all changed Python files, `git diff --check`, and a zero-delta anchored test-definition count.
- [ ] Grep for untagged debug output and verify no production/workflow timeout changes.
- [ ] Commit the plan and implementation in reviewable commits, push one branch, open one PR, and require independent review plus terminal hosted CI. Do not merge without separate authorization.
