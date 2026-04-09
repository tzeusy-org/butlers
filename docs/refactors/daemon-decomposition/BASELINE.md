# daemon.py Decomposition Baseline

**Date:** 2026-04-09
**Source:** `src/butlers/daemon.py` (7,090 lines as of commit 6fa28110)
**Purpose:** Pre-extraction "before" snapshot for the daemon decomposition epic (bu-m9p3p).

---

## 1. MCP Tool Surface Inventory

All tools registered by `_register_core_tools()` (lines 2953–~6700).
Tools are grouped by the `_core_tool(group)` decorator argument.
`route.execute` and messenger/switchboard-only tools are registered unconditionally via `@mcp.tool()`.

### Constant sets (line 147–207)

| Constant | Member Tools |
|---|---|
| `UNIVERSAL_CORE_TOOL_NAMES` | status, trigger, route.execute, tick, state_get, state_set, state_delete, state_list, schedule_list, schedule_create, schedule_update, schedule_delete, schedule_trigger, sessions_list, sessions_get, sessions_summary, sessions_daily, top_sessions, schedule_costs, notify, remind, get_attachment, module.states, module.set_enabled, correct |
| `MESSENGER_CORE_TOOL_NAMES` | delivery_preferences_set, delivery_preferences_get, deferred_notifications_list, deferred_notification_cancel |
| `DOMAIN_CORE_TOOL_NAMES` | deadline_create, deadline_update, deadline_list, deadline_delete, event_chain_create, event_chain_update, event_chain_list, event_chain_delete, seasonal_period_create, seasonal_period_update, seasonal_period_list, seasonal_period_delete, seasonal_period_create_preset |
| `CORE_TOOL_NAMES` | Union of all three above (backward-compat alias) |

### Tool-by-tool catalog

| Tool Name | Group | Butler Condition | Approval-Gated | Signature (key params) |
|---|---|---|---|---|
| `status` | infra | all | no | `() -> dict` |
| `trigger` | infra | all | no | `(prompt: str, context: str | None) -> dict` |
| `route.execute` | n/a (always) | all | no | `(schema_version, request_context, input, subrequest?, target?, source_metadata?, trace_context?) -> dict` |
| `ingest` | switchboard_routing | switchboard only | no | `(schema_version, source, event, sender, payload, control?) -> dict` |
| `route_to_butler` | switchboard_routing | switchboard only | no | `(butler, prompt, context?, complexity?) -> dict` |
| `connector.heartbeat` | switchboard_routing | switchboard only | no | `(schema_version, connector, status, counters, checkpoint?, capabilities?, sent_at?) -> dict` |
| `backfill.poll` | switchboard_backfill | switchboard only | no | `(connector_type, endpoint_identity) -> dict | None` |
| `backfill.progress` | switchboard_backfill | switchboard only | no | `(job_id, connector_type, endpoint_identity, rows_processed, rows_skipped, cost_spent_cents_delta, cursor?, status?, error?) -> dict` |
| `tick` | infra | all | no | `() -> dict` |
| `state_get` | state | all | no | `(key: str, _trace_context?) -> dict` |
| `state_set` | state | all | no | `(key: str, value: Any, _trace_context?) -> dict` |
| `state_delete` | state | all | no | `(key: str, _trace_context?) -> dict` |
| `state_list` | state | all | no | `(prefix?: str, _trace_context?) -> list[dict]` |
| `schedule_list` | scheduling | all | no | `() -> list[dict]` |
| `schedule_create` | scheduling | all | no | `(name, cron?, prompt?, task_type?, dispatch_mode?, job_name?, job_args?, timezone?, start_at?, end_at?, until_at?, display_title?, calendar_event_id?, target_date?, lead_time_days?, alert_thresholds?, depends_on?) -> dict` |
| `remind` | notifications | all | no | `(message, channel, delay_minutes?, deliver_at?) -> dict` |
| `schedule_update` | scheduling | all | no | `(task_id?, id?, name?, cron?, dispatch_mode?, prompt?, job_name?, job_args?, enabled?, timezone?, start_at?, end_at?, until_at?, display_title?, calendar_event_id?) -> dict` |
| `schedule_delete` | scheduling | all | no | `(task_id?, id?) -> dict` |
| `deadline_create` | temporal | non-STAFFER only | no | `(name, prompt, target_date, lead_time_days, alert_thresholds, depends_on?) -> dict` |
| `deadline_update` | temporal | non-STAFFER only | no | `(task_id, name?, prompt?, target_date?, lead_time_days?, alert_thresholds?, depends_on?, deadline_status?, enabled?) -> dict` |
| `deadline_list` | temporal | non-STAFFER only | no | `(...filters...) -> dict` |
| `deadline_delete` | temporal | non-STAFFER only | no | `(task_id) -> dict` |
| `schedule_trigger` | scheduling | non-STAFFER only | no | `(task_id?, id?) -> dict` |
| `sessions_list` | sessions | non-STAFFER only | no | `(limit?, offset?) -> list[dict]` |
| `sessions_get` | sessions | non-STAFFER only | no | `(session_id: str) -> dict | None` |
| `sessions_summary` | sessions | non-STAFFER only | no | `(period?: str) -> dict` |
| `sessions_daily` | sessions | non-STAFFER only | no | `(from_date, to_date) -> dict` |
| `top_sessions` | sessions | non-STAFFER only | no | `(limit?: int) -> dict` |
| `schedule_costs` | scheduling | non-STAFFER only | no | `() -> dict` |
| `notify` | notifications | non-STAFFER only | yes (configurable) | `(channel, message?, recipient?, subject?, intent?, emoji?, request_context?, contact_id?, priority?) -> dict` |
| `event_chain_create` | temporal | non-STAFFER only | no | `(name, trigger_type, actions, trigger_reference?) -> dict` |
| `event_chain_update` | temporal | non-STAFFER only | no | `(chain_id, name?, trigger_type?, trigger_reference?, actions?, status?) -> dict` |
| `event_chain_list` | temporal | non-STAFFER only | no | `(trigger_type?, status?, limit?) -> dict` |
| `event_chain_delete` | temporal | non-STAFFER only | no | `(chain_id) -> dict` |
| `seasonal_period_create` | temporal | non-STAFFER only | no | `(name, period_type?, start_month?, start_day?, end_month?, end_day?, timezone?, metadata?, enabled?) -> dict` |
| `seasonal_period_update` | temporal | non-STAFFER only | no | `(period_id, name?, period_type?, start_month?, start_day?, end_month?, end_day?, timezone?, metadata?, enabled?) -> dict` |
| `seasonal_period_list` | temporal | non-STAFFER only | no | `(include_disabled?) -> dict` |
| `seasonal_period_delete` | temporal | non-STAFFER only | no | `(period_id) -> dict` |
| `seasonal_period_create_preset` | temporal | non-STAFFER only | no | `(preset, timezone?) -> dict` |
| `delivery_preferences_set` | n/a (always) | messenger only | no | `(timezone, quiet_hours_start?, quiet_hours_end?, batch_low_priority?, batch_delivery_time?, override_channels?) -> dict` |
| `delivery_preferences_get` | n/a (always) | messenger only | no | `() -> dict` |
| `deferred_notifications_list` | n/a (always) | messenger only | no | `(status?, limit?) -> dict` |
| `deferred_notification_cancel` | n/a (always) | messenger only | no | `(notification_id: str) -> dict` |
| `messenger_delivery_status` | n/a (always) | messenger only | no | `(delivery_id: str) -> dict` |
| `messenger_delivery_search` | n/a (always) | messenger only | no | `(origin_butler?, channel?, intent?, status?, since?, until?, limit?) -> dict` |
| `messenger_delivery_attempts` | n/a (always) | messenger only | no | `(delivery_id: str) -> dict` |
| `messenger_delivery_trace` | n/a (always) | messenger only | no | `(request_id: str) -> dict` |
| `get_attachment` | media | all | no | `(storage_ref: str) -> dict` |
| `module.states` | module_mgmt | all | no | `() -> dict` |
| `module.set_enabled` | module_mgmt | all | no | `(name: str, enabled: bool) -> dict` |
| `correct` | infra | all | no | `(correction_type, target_session_id, description, target_butler?, correct_butler?, state_key?, corrected_value?, memory_type?, memory_id?, action_description?) -> dict` |

**Total distinct tool names:** 48 (of which 4 are switchboard-only, 4 are messenger-delivery ops, and 4 are deferred-notification messenger-only).

**Approval gate:** Applied via `_apply_approval_gates()` (step 13b). The set of gated tools is determined by `butler.toml → [approvals]` config, resolved by `parse_approval_config()`. `notify` is the canonical gated tool in default configs.

**Groups summary:**

| Group | Tools |
|---|---|
| infra | status, trigger, tick, correct |
| state | state_get, state_set, state_delete, state_list |
| scheduling | schedule_list, schedule_create, schedule_update, schedule_delete, schedule_trigger, schedule_costs |
| notifications | remind, notify |
| sessions | sessions_list, sessions_get, sessions_summary, sessions_daily, top_sessions |
| temporal | deadline_create, deadline_update, deadline_list, deadline_delete, event_chain_create, event_chain_update, event_chain_list, event_chain_delete, seasonal_period_create, seasonal_period_update, seasonal_period_list, seasonal_period_delete, seasonal_period_create_preset |
| media | get_attachment |
| module_mgmt | module.states, module.set_enabled |
| switchboard_routing | ingest, route_to_butler, connector.heartbeat |
| switchboard_backfill | backfill.poll, backfill.progress |
| n/a (unconditional) | route.execute, delivery_preferences_set/get, deferred_notifications_list/cancel, messenger_delivery_* |

---

## 2. Startup Sequence Coverage Matrix

Source: `ButlerDaemon.start()` (line 1638) and module docstring (lines 1–33).

| Step | Description | Code Location | Test Coverage |
|---|---|---|---|
| 1 | Load config from butler.toml | line 1648 | `test_daemon.py`, `test_startup_guard.py` |
| 1b | Configure structured logging | line 1652 | Implicit (logging setup, no dedicated test) |
| 1c | Blob storage deferred note | line 1663 | `test_daemon.py` (blob store tests) |
| 2 | Initialize telemetry and metrics | line 1668 | `test_daemon_spans.py` |
| 2.5 | Detect inline secrets in config | line 1672 | No dedicated startup test |
| 3 | Initialize modules (topological order) | line 1680 | `test_module_state.py`, `test_module_composition.py` |
| 4 | Validate module config schemas (non-fatal) | line 1683 | `test_module_boundaries.py` |
| 5 | Validate butler.env credentials (env-only fast-fail) | line 1688 | `test_daemon.py` (credential tests) |
| 6 | Provision database | line 1696 | `test_daemon.py` (db provisioning) |
| 7 | Run core Alembic migrations | line 1708 | `test_butler_migrations.py` |
| 7b | Run butler-specific Alembic migrations | line 1713 | `test_butler_migrations.py` |
| 8 | Run module Alembic migrations (non-fatal) | line 1718 | `test_module_boundaries.py` |
| 8b | Create CredentialStore; validate module credentials (non-fatal) | line 1735 | `test_daemon.py` |
| 8c | Initialize S3 blob storage | line 1767 | `test_daemon.py` |
| 8c2 | Restore CLI auth tokens from DB | line 1795 | No dedicated test |
| 8d | Bootstrap owner entity (idempotent) | line 1809 | `test_owner_bootstrap.py` |
| 9b | Resolve runtime config from DB (seed from toml) | line 1812 | `test_wire_module_runtime.py` (partial) |
| 9 | Module on_startup (non-fatal per-module) | line 1833 | `test_graceful_shutdown.py`, `test_module_state.py` |
| 10 | Create Spawner with runtime adapter | line 1852 | `test_daemon.py` |
| 10a | Set up audit pool | line 1867 | `test_db_topology.py` |
| 10b | Wire message classification pipeline (switchboard only) | line 1882 | `test_routing_pipeline.py` |
| 11 | Sync TOML schedules to DB | line 1884 | `test_schedule_native_dispatch.py` |
| 11b | Open MCP client to Switchboard (non-switchboard) | line 1908 | `test_mcp_only_inter_butler.py` |
| 12 | Create FastMCP server and register core tools | line 1912 | `test_daemon.py::test_all_core_tools_registered` (FAILING) |
| 13 | Register module MCP tools | line 1916 | `test_tool_surface_isolation.py` |
| 13b | Apply approval gates to gated tools | line 1919 | `test_approval_gates.py`, `test_tool_gating.py` |
| 13c | Wire calendar overlap-approval enqueuer | line 1922 | No dedicated test |
| 13d | Wire spawner + switchboard_client into modules | line 1928 | `test_wire_module_runtime.py` |
| 13e | Initialize module runtime states from state store | line 1936 | `test_module_state.py` |
| 14 | Start FastMCP SSE server | line 1939 | `test_daemon.py` |
| 14b | Start durable buffer workers and scanner (switchboard only) | line 1942 | `test_routing_pipeline.py` |
| 14c | Recover unprocessed route_inbox rows (non-staffer) | line 1951 | `test_route_execute_async_dispatch.py` |
| 15 | Launch switchboard heartbeat (non-switchboard) | line 1955 | `test_daemon.py` |
| 16 | Start internal scheduler loop | line 1961 | `test_scheduler_loop.py` |
| 17 | Start liveness reporter | line 1964 | `test_liveness_reporter.py` (FAILING) |

**Steps with no or weak coverage:** 1b (logging config), 2.5 (secret detection), 8c2 (CLI token restore), 13c (calendar approval enqueuer wiring).

---

## 3. Shutdown Sequence Coverage Matrix

Source: `ButlerDaemon.shutdown()` (line 6887).

The docstring labels steps a–i; the implementation uses numbered comments 1–9:

| Step (docstring letter / code number) | Description | Code Location | Test Coverage |
|---|---|---|---|
| a / 1 | Stop MCP server | line 6905 | `test_graceful_shutdown.py` (FAILING) |
| — / 2 | Stop durable buffer (drain queue, cancel workers) | line 6922 | `test_graceful_shutdown.py` |
| — / 2b | Cancel in-flight route_inbox background tasks | line 6929 | `test_graceful_shutdown.py` |
| b / 3 | Stop accepting new triggers; drain in-flight runtime sessions | line 6940 | `test_graceful_shutdown.py` |
| c / 4 | Cancel switchboard heartbeat | line 6947 | `test_graceful_shutdown.py` |
| d / 5 | Close Switchboard MCP client | line 6956 | `test_graceful_shutdown.py` |
| — / 5b | Cancel internal scheduler loop | line 6959 | `test_graceful_shutdown.py` |
| — / 5c | Cancel route_inbox recovery task | line 6968 | `test_graceful_shutdown.py` |
| — / 5d | Cancel liveness reporter loop | line 6977 | `test_graceful_shutdown.py` |
| e / 6 | Module shutdown in reverse topological order | line 6986 | `test_graceful_shutdown.py` (FAILING) |
| — / 6b | Close S3 blob store | line 6996 | No dedicated test |
| f / 7 | Close audit DB pool | line 7001 | `test_db_topology.py` |
| — / 8 | Close credential-layer DB pools | line 7007 | No dedicated test |
| g / 9 | Close DB pool | line 7011 | `test_graceful_shutdown.py` |

Note: The docstring lists steps a–i but the code comment labels do not map exactly 1:1. The implementation has expanded with sub-steps (2b, 5b–5d, 6b, 8) that post-date the original docstring.

**Gaps:** 6b (blob store close), 8 (credential DB pool close) have no dedicated shutdown tests.

---

## 4. Scheduler Job Registry

All deterministic schedule job handler functions registered in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` (line 807).
Each handler has signature `async (pool: asyncpg.Pool, job_args: dict | None) -> dict`.

### Shared handlers (reused across butlers)

| Handler Function | Job Name Key | Butler(s) |
|---|---|---|
| `_run_memory_consolidation_job` | `memory_consolidation` | general, health, relationship, home, lifestyle, switchboard |
| `_run_memory_episode_cleanup_job` | `memory_episode_cleanup` | general, health, relationship, home, lifestyle, switchboard |
| `_run_memory_purge_superseded_job` | `memory_purge_superseded` | general, health, relationship, home, lifestyle, switchboard |

### Per-butler job registry

| Butler | Job Key | Handler Function |
|---|---|---|
| general | `memory_consolidation` | `_run_memory_consolidation_job` |
| general | `memory_episode_cleanup` | `_run_memory_episode_cleanup_job` |
| general | `memory_purge_superseded` | `_run_memory_purge_superseded_job` |
| general | `collect_briefing_contributions` | `_run_collect_briefing_contributions_job` |
| health | `memory_consolidation` | `_run_memory_consolidation_job` |
| health | `memory_episode_cleanup` | `_run_memory_episode_cleanup_job` |
| health | `memory_purge_superseded` | `_run_memory_purge_superseded_job` |
| health | `daily_briefing_contribution` | `_run_health_briefing_contribution_job` |
| health | `insight_scan` | `_run_health_insight_scan_job` |
| finance | `daily_briefing_contribution` | `_run_finance_briefing_contribution_job` |
| relationship | `memory_consolidation` | `_run_memory_consolidation_job` |
| relationship | `memory_episode_cleanup` | `_run_memory_episode_cleanup_job` |
| relationship | `memory_purge_superseded` | `_run_memory_purge_superseded_job` |
| relationship | `daily_briefing_contribution` | `_run_relationship_briefing_contribution_job` |
| relationship | `insight_scan` | `_run_relationship_insight_scan_job` |
| relationship | `interaction_sync` | `_run_relationship_interaction_sync_job` |
| travel | `daily_briefing_contribution` | `_run_travel_briefing_contribution_job` |
| travel | `insight_scan` | `_run_travel_insight_scan_job` |
| education | `compute_analytics_snapshots` | `_run_education_compute_analytics_snapshots_job` |
| education | `daily_briefing_contribution` | `_run_education_briefing_contribution_job` |
| home | `memory_consolidation` | `_run_memory_consolidation_job` |
| home | `memory_episode_cleanup` | `_run_memory_episode_cleanup_job` |
| home | `memory_purge_superseded` | `_run_memory_purge_superseded_job` |
| home | `device_health_check` | `_run_home_device_health_check_job` |
| home | `environment_report` | `_run_home_environment_report_job` |
| home | `energy_digest` | `_run_home_energy_digest_job` |
| home | `maintenance_schedule_check` | `_run_home_maintenance_schedule_check_job` |
| home | `daily_briefing_contribution` | `_run_home_briefing_contribution_job` |
| lifestyle | `memory_consolidation` | `_run_memory_consolidation_job` |
| lifestyle | `memory_episode_cleanup` | `_run_memory_episode_cleanup_job` |
| lifestyle | `memory_purge_superseded` | `_run_memory_purge_superseded_job` |
| lifestyle | `daily_briefing_contribution` | `_run_lifestyle_briefing_contribution_job` |
| switchboard | `eligibility_sweep` | `_run_switchboard_eligibility_sweep_job` |
| switchboard | `insight_delivery_cycle` | `_run_switchboard_insight_delivery_cycle_job` |
| switchboard | `memory_consolidation` | `_run_memory_consolidation_job` |
| switchboard | `memory_episode_cleanup` | `_run_memory_episode_cleanup_job` |
| switchboard | `memory_purge_superseded` | `_run_memory_purge_superseded_job` |
| qa | `qa_patrol` | `_run_qa_patrol_job` |
| qa | `qa_pr_status_check` | `_run_qa_pr_status_check_job` |

**Total unique job handler functions:** 17

### Cron mappings

Cron expressions live in `roster/<butler>/butler.toml` per-schedule entries, not in daemon.py itself. The daemon reads them at runtime and syncs them to DB via `sync_schedules()` (step 11). Job dispatching happens via `_dispatch_scheduled_task()` which delegates to `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` for `dispatch_mode=job` schedules.

Test coverage: `test_briefing_job_registration.py`, `test_schedule_native_dispatch.py`, `test_memory_chain_registration.py`.

---

## 5. Test Suite Snapshot (2026-04-09)

### contracts/ (122 passed, 1 skipped — all green)

| Test File | Tests | Status |
|---|---|---|
| test_approval_gates.py | multiple | PASS |
| test_connector_as_transport.py | multiple | PASS |
| test_context_bus.py | multiple | PASS (1 deprecation warning) |
| test_credential_tier_resolution.py | multiple | PASS |
| test_cross_butler_briefing_exception.py | multiple | PASS |
| test_daemon_determinism.py | multiple | PASS |
| test_finance_soft_delete.py | multiple | PASS |
| test_graceful_shutdown.py | multiple | PASS |
| test_identity_resolution.py | multiple | PASS |
| test_insight_delivery.py | multiple | PASS |
| test_mcp_only_inter_butler.py | multiple | PASS |
| test_module_boundaries.py | multiple | PASS |
| test_module_composition.py | multiple | PASS |
| test_routing_pipeline.py | multiple | PASS |
| test_schema_isolation.py | multiple | PASS |
| test_session_lifecycle.py | multiple | PASS |
| test_staffer_routing_exclusion.py | multiple | PASS |
| test_tool_surface_isolation.py | multiple | PASS |

### daemon/ (72 passed, 17 failed)

| Test File | Result | Notes |
|---|---|---|
| test_briefing_job_registration.py | PASS | |
| test_butler_migrations.py | PASS | |
| test_correct_tool_registration.py | PASS | |
| test_daemon.py | **PARTIAL FAIL** | 5 tests FAIL: test_all_core_tools_registered, test_status_tool, test_health_degraded, test_notify_schema_and_channels, test_notify_delivery_and_failures, test_non_fatal_module_failures |
| test_daemon_spans.py | PASS | |
| test_db_topology.py | PASS | |
| test_graceful_shutdown.py | **FAIL** | TestDaemonGracefulShutdown::test_shutdown_sequence, TestStartupFailureCleanup::test_module_startup_failure_and_cascade |
| test_ingest_reaction_lifecycle.py | PASS | |
| test_liveness_reporter.py | **FAIL** | TestLivenessReporterLifecycle::test_liveness_reporter_task_lifecycle |
| test_memory_chain_registration.py | PASS | |
| test_module_state.py | PASS | |
| test_notify_contact_id.py | **FAIL** | 3 tests FAIL |
| test_notify_react.py | **FAIL** | 1 test FAIL |
| test_owner_bootstrap.py | PASS | |
| test_route_execute_async_dispatch.py | PASS | |
| test_route_execute_authz.py | PASS | |
| test_route_execute_prompt_fencing.py | PASS | |
| test_route_execute_request_context_injection.py | PASS | |
| test_route_execute_sender_entity_id.py | PASS | |
| test_route_execute_trace_decoupling.py | **FAIL** | 2 tests FAIL |
| test_route_execute_trace_propagation.py | PASS | |
| test_route_to_butler_accepted_status.py | **FAIL** | 2 tests FAIL |
| test_schedule_native_dispatch.py | PASS | |
| test_scheduler_loop.py | PASS | |
| test_startup_guard.py | PASS | |
| test_tool_gating.py | PASS | |
| test_wire_module_runtime.py | PASS | |

### Full suite summary (excluding DB/migration integration tests)

```
34 failed, 2146 passed, 97 skipped, 32 warnings, 32 errors
Total runtime: ~136s
```

Errors are in `tests/integration/` (require live DB) and `tests/core/test_sessions_token_columns.py` (schema-dependent).

---

## 6. Key Observations for Decomposition

1. **Size and concentration:** At 7,090 lines, `daemon.py` concentrates startup, shutdown, all core tool definitions (48+ tools), scheduler job dispatch, route_execute processing, notify delivery logic, and butler-specific routing (switchboard ingest pipeline, messenger deliver path) in a single file.

2. **`_register_core_tools()` is the primary extraction target:** Lines 2953–6700 (~3,750 lines). The function is entirely closure-based — all tool handlers close over `pool`, `spawner`, `daemon`, `butler_name`, and `butler_type` locals. This must be preserved or restructured during extraction.

3. **17 failing daemon tests pre-extraction:** These failures should not be caused by the decomposition work. They are the pre-existing baseline. Any extraction that increases the failure count is a regression.

4. **Contracts tests are all green:** 122 tests pass. These are the highest-value regression guard — they test behavioral contracts that must remain intact after extraction.

5. **Startup steps with zero test coverage:** 1b (logging), 2.5 (secret detection), 8c2 (CLI token restore), 13c (calendar approval wiring). These are risk areas for silent breakage during extraction.

6. **Scheduler jobs are fully in daemon.py:** All `_run_*_job` handlers live at module level (lines 459–852), not on the daemon class. They import domain code lazily. These are good candidates for a `butlers/jobs/daemon_jobs.py` module extraction.

7. **`route.execute` is particularly complex:** Lines 3052–3400+ handle the full async accept-phase / background-process pattern with otel tracing, dedup, route_inbox persistence, and messenger inline delivery. Its test coverage spans `test_route_execute_*.py` (8 files).
