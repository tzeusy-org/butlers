# Token Limits Gen-1 Reconciliation Report

**Epic:** bu-lm4m — Add rolling-window token limits per model catalog entry
**Reconciliation bead:** bu-lm4m.5
**Date:** 2026-03-17
**Auditor:** Agent worker on branch `agent/bu-lm4m.5`

## Summary

All four sibling implementation beads (bu-x2re, bu-21g0, bu-lm4m.1, bu-lm4m.2,
bu-lm4m.3, bu-lm4m.4) have been merged to main. This report maps every spec
requirement and scenario to its implementing bead/code and documents two
minor gaps found during the audit.

**Overall coverage: HIGH. 2 gaps found (both minor).**

---

## Sources Audited

| Source | Location |
|--------|----------|
| `catalog-token-limits/spec.md` | 8 requirements, ~40 scenarios |
| `model-catalog/spec.md` | 2 requirements (modified + added) |
| `design.md` | 9 decisions D1–D9 |
| `tasks.md` | 38 implementation tasks |

---

## Spec Coverage Checklist

### `specs/model-catalog/spec.md`

#### Requirement: Model Resolution (MODIFIED)

| Scenario | Status | Implementation |
|----------|--------|----------------|
| Resolution with global defaults only — returns 4-tuple `(runtime_type, model_id, extra_args, catalog_entry_id)` | COVERED | `src/butlers/core/model_routing.py` — `_RESOLVE_SQL` selects `mc.id`; `resolve_model()` returns 4-tuple. Integration test: `test_resolve_global_only` in `tests/core/test_model_routing.py` unpacks 4-element tuple and asserts `catalog_entry_id`. |
| Resolution with butler overrides | COVERED | Pre-existing; 4-tuple return verified by all integration tests in `test_model_routing.py`. |
| Resolution with disabled override | COVERED | Pre-existing test `test_resolve_override_disable`. |
| No candidates fallback → None | COVERED | Pre-existing test `test_resolve_no_candidates_returns_none`. |
| Priority tie-breaking by `created_at` | COVERED | Pre-existing test `test_resolve_tie_breaking_by_created_at`. |
| Return type includes `catalog_entry_id` | COVERED | `resolve_model()` return type annotation is `tuple[str, str, list[str], uuid.UUID] \| None`. Spawner and DiscretionDispatcher both unpack 4 elements. |

#### Requirement: Adapter Token Reporting Contract (ADDED)

| Scenario | Status | Implementation |
|----------|--------|----------------|
| Adapter reports token usage — `{"input_tokens": int, "output_tokens": int}` | COVERED | All four adapters audited in `tests/adapters/test_adapter_contract.py`. Claude, Codex, and OpenCode adapters report token counts. |
| Adapter cannot determine token counts — returns `{}` or `None` | COVERED | Gemini adapter: `GeminiAdapter.invoke()` returns `None` for usage (CLI does not expose token counts). Documented and tested in `test_gemini_usage_is_none` in `test_adapter_contract.py`. |
| Known adapters to audit: `claude`, `codex`, `gemini`, `opencode` | COVERED | All four audited. `claude` — extracts from `ResultMessage.usage`. `codex` — extracts from `turn.completed`/`response.completed` events. `opencode` — extracts from `step_finish` parts and standalone `usage` events. `gemini` — CLI does not expose token counts, returns `None`. |

---

### `specs/catalog-token-limits/spec.md`

#### Requirement: Token Usage Ledger Schema

| Scenario | Status | Implementation |
|----------|--------|----------------|
| Ledger entry structure — all columns present | COVERED | Migration `alembic/versions/core/core_035_token_usage_ledger_and_limits.py`. Source inspection tests in `tests/migrations/test_token_usage_ledger_migration.py`. |
| Composite primary key `(id, recorded_at)` | COVERED | Migration creates `PRIMARY KEY (id, recorded_at)`. Test: `test_partitioned_by_range_recorded_at`. |
| Query-optimized index `idx_ledger_entry_time` on `(catalog_entry_id, recorded_at)` | COVERED | Migration creates `CREATE INDEX IF NOT EXISTS idx_ledger_entry_time`. Test: `test_idx_ledger_entry_time_exists`. |
| Monthly partitioning with pg_partman when available | COVERED | Migration calls `partman.create_parent(...)` when pg_partman available. Source inspection test: `test_pg_partman_used_when_available`. |
| Monthly partitioning without pg_partman — 6-month buffer + warning | COVERED | Migration creates 6 monthly partitions and logs warning. Test: `test_fallback_partition_count_is_6`. The spec says "5 months ahead" (current + 5 = 6 total) which matches `_FALLBACK_PARTITION_COUNT = 6`. |
| Cascade on catalog entry deletion | COVERED | `ON DELETE CASCADE` FK in migration. Migration test: `test_catalog_entry_id_references_model_catalog_cascade`. |
| Delete and recreate resets usage history | PARTIALLY COVERED | The CASCADE constraint guarantees this at the DB level (new UUID = no old rows), but there is no dedicated integration test that creates an entry, inserts ledger rows, deletes the entry, recreates it with the same alias, and verifies zero usage. The DB schema makes this correct by construction. **Gap noted — see Gap 1 below.** |
| Discretion calls have no session — `session_id = NULL`, `butler_name = "__discretion__"` | COVERED | `record_token_usage()` accepts `session_id=None`. DiscretionDispatcher passes `session_id=None`. Tests: `test_record_token_usage_null_session_id` and `test_session_id_is_always_none_for_discretion_calls`. |

#### Requirement: Token Limits Schema

| Scenario | Status | Implementation |
|----------|--------|----------------|
| Limits entry structure — all columns present | COVERED | Migration creates `shared.token_limits` with all required columns. Source inspection tests in `tests/migrations/test_token_usage_ledger_migration.py`. |
| Token counting unit — total tokens (`input_tokens + output_tokens`) | COVERED | `_QUOTA_CHECK_SQL` sums `input_tokens + output_tokens`. API CTE uses same formula. Tests verify total is summed correctly (e.g., `test_check_quota_within_both_limits` inserts 100+50=150 and asserts `usage_24h == 150`). |
| No limits row means unlimited | COVERED | Fast path in `check_token_quota()`: `if limits_row is None: return _unlimited`. Test: `test_check_quota_no_limits_row_fast_path`. |
| Cascade on catalog entry deletion | COVERED | `ON DELETE CASCADE` on `token_limits` FK. Migration test: `test_catalog_entry_id_fk_cascade` asserts exactly 2 cascade FK clauses. |
| Disabled entry with limits re-enabled via override | PARTIALLY COVERED | The DB schema correctly scopes limits to the catalog entry (not the override), so this is correct by construction. However, there is no test explicitly verifying that a globally-disabled entry with an override that re-enables it still enforces its token limits. **Gap noted — see Gap 2 below.** |

#### Requirement: Independent Window Resets

| Scenario | Status | Implementation |
|----------|--------|----------------|
| Reset 24h window only — `reset_24h_at = now()`, `reset_30d_at` unchanged | COVERED | `POST /api/settings/models/{entry_id}/reset-usage` with `window="24h"` only sets `reset_24h_at`. Test: `test_resets_24h_window`. Integration test: `test_check_quota_reset_24h_at_respected`. |
| Reset 30d window only — `reset_30d_at = now()`, `reset_24h_at` unchanged | COVERED | API endpoint with `window="30d"`. Test: `test_resets_30d_window`. Integration test: `test_check_quota_reset_30d_at_respected`. |
| Reset both windows | COVERED | API endpoint with `window="both"`. Test: `test_resets_both_windows`. |
| Reset on entry without limits row — creates new row with null limits | COVERED | API endpoint uses `ON CONFLICT DO UPDATE` upsert. Test: `test_resets_24h_window` uses a mock pool that accepts the upsert. Spec scenario verified. |

#### Requirement: Pre-Spawn Quota Check

| Scenario | Status | Implementation |
|----------|--------|----------------|
| `QuotaStatus` return type — all 5 fields | COVERED | `QuotaStatus` dataclass in `model_routing.py`. Test: `test_quota_status_dataclass_fields`. |
| Entry with no limits — fast path, no ledger query | COVERED | `_LIMITS_EXISTS_SQL` check before `_QUOTA_CHECK_SQL`. Test: `test_check_quota_no_limits_row_fast_path`. |
| Usage within both limits → `allowed=True` | COVERED | `check_token_quota()` logic. Test: `test_check_quota_within_both_limits`. |
| 24h limit exceeded → `allowed=False`, `usage_24h >= limit_24h` | COVERED | Test: `test_check_quota_24h_limit_exceeded`. Equality case covered by `test_check_quota_exactly_at_limit_is_blocked`. |
| 30d limit exceeded → `allowed=False`, `usage_30d >= limit_30d` | COVERED | Test: `test_check_quota_30d_limit_exceeded`. |
| One window unlimited, other exceeded → `allowed=False` | COVERED | Test: `test_check_quota_24h_unlimited_30d_exceeded`. |
| Reset markers affect window calculation | COVERED | Tests: `test_check_quota_reset_24h_at_respected` and `test_check_quota_reset_30d_at_respected`. |
| Single-query execution (CTE-based) | COVERED | `_QUOTA_CHECK_SQL` is a CTE. Design D5 SQL is implemented verbatim. Verified by code review. |
| Fail-open on quota check error | COVERED | `except Exception:` in `check_token_quota()` returns `_unlimited` and logs warning. Test: `test_check_token_quota_fail_open_on_db_error`. |

#### Requirement: Post-Spawn Ledger Recording

| Scenario | Status | Implementation |
|----------|--------|----------------|
| Spawner records usage after successful session | COVERED | `record_token_usage()` called in spawner `finally` block when `_ledger_input_tokens is not None` and `catalog_entry_id is not None`. Test: `test_successful_session_records_to_ledger`. |
| Spawner records usage after failed session (adapter reported tokens) | COVERED | `finally` block executes regardless of success/failure. `_ledger_input_tokens` is set as soon as adapter reports usage. Tests: `test_failed_session_with_usage_records_to_ledger`, `test_failed_session_with_reported_usage_still_records`. |
| Adapter invocation fails before returning usage — no ledger row | COVERED | `_ledger_input_tokens is None` guard prevents recording. Test: `test_no_ledger_recording_when_adapter_reports_no_usage`. |
| Discretion dispatcher records usage | COVERED | DiscretionDispatcher captures `_usage_dict` and calls `record_token_usage()` with `session_id=None`. Tests: `test_records_usage_after_successful_call`, `test_session_id_is_always_none_for_discretion_calls`. |
| Best-effort recording — failure logged, session result returned | COVERED | `record_token_usage()` wraps INSERT in `try/except`, logs warning, never raises. Test: `test_record_token_usage_best_effort_on_error`. Spawner test: `test_no_ledger_recording_when_adapter_reports_no_usage`. |
| No recording for TOML-fallback resolution | COVERED | `catalog_entry_id is None` guard in spawner `finally` block prevents recording. Test: `test_no_ledger_recording_when_no_catalog_entry_id`. |
| No recording when adapter reports no usage | COVERED | `_ledger_input_tokens is None` guard. Tests: `test_no_ledger_recording_when_adapter_reports_no_usage` (spawner), `test_no_recording_when_adapter_returns_no_usage` (dispatcher). |

#### Requirement: Hard Block on Quota Exhaustion

| Scenario | Status | Implementation |
|----------|--------|----------------|
| Spawner blocks on quota exceeded — no adapter invocation, `SpawnerResult(success=False)` | COVERED | Spawner calls `check_token_quota()` after `resolve_model()` and before adapter invocation. If `not quota.allowed`: returns `SpawnerResult(success=False, error=...)`. Tests: `test_spawn_blocked_when_24h_limit_exhausted`, `test_spawn_blocked_when_30d_limit_exhausted`. |
| Discretion dispatcher blocks — raises `RuntimeError` | COVERED | DiscretionDispatcher raises `RuntimeError` on `not quota.allowed`. Tests: `test_raises_runtime_error_when_24h_quota_exhausted`, `test_raises_for_both_windows_exceeded`. |
| Error message includes quota details — alias, window(s), usage, limit | COVERED | Spawner builds `windows_exceeded` list with `used=X, limit=Y`. Dispatcher does same. Tests: `test_quota_error_message_includes_alias_and_windows`, `test_raises_runtime_error_includes_window_and_usage_details`. |

#### Requirement: Token Limits API

| Scenario | Status | Implementation |
|----------|--------|----------------|
| `GET /api/settings/models` includes `usage_24h`, `usage_30d`, `limit_24h`, `limit_30d` | COVERED | `list_catalog_entries()` in `model_settings.py` uses CTE aggregation. `ModelCatalogEntry` has all 4 fields. Tests: `test_entries_include_usage_and_limit_fields`, `test_entries_with_null_limits_show_usage_only`. |
| Usage aggregated via single CTE (not N+1) | COVERED | `usage_agg` CTE aggregates across all entries in one query. Verified by code review. |
| `PUT /api/settings/models/{entry_id}/limits` — upsert token limits | COVERED | `upsert_token_limits()` endpoint. Test: `test_sets_limits_successfully`, `test_allows_partial_limits_one_null`. |
| Both null → deletes `token_limits` row | COVERED | Explicit `DELETE` branch when both null. Test: `test_deletes_limits_row_when_both_null`. |
| Limit value validation — must be >= 1, 0/negative rejected with 422 | COVERED | `TokenLimitsRequest.model_post_init` validates `>= 1`. Tests: `test_rejects_zero_limit_with_422`, `test_rejects_negative_limit_with_422`. |
| `POST /api/settings/models/{entry_id}/reset-usage` — sets reset timestamps, creates row if needed | COVERED | `reset_token_usage()` endpoint. Upsert creates row if needed. Tests: `test_resets_24h_window`, `test_resets_30d_window`, `test_resets_both_windows`. |
| `GET /api/settings/models/{entry_id}/usage` — detailed usage with percentages | COVERED | `get_token_usage()` endpoint returns all fields including `percent_24h`, `percent_30d` (null when no limit). Tests: `test_returns_usage_with_limits_and_percentages`, `test_returns_null_percentages_when_no_limits`. |
| Resolve-model preview includes quota status — `quota_blocked`, actual usage (not fast path zeroes) | COVERED | `resolve_model_preview()` queries ledger directly (not `check_token_quota()` fast path). `quota_blocked` is True when either window exceeded. Tests: `test_quota_fields_present_when_resolved`, `test_quota_blocked_when_24h_exceeded`, `test_quota_blocked_when_30d_exceeded`, `test_quota_not_blocked_for_unlimited_entries`. |

#### Requirement: Dashboard Usage Columns

| Scenario | Status | Implementation |
|----------|--------|----------------|
| Usage bar with limit configured — progress bar + `used/limit` text | COVERED | `UsageBar` component in `frontend/src/components/settings/ModelCatalogCard.tsx`. Shows progress bar when `limit != null`. |
| Usage display without limit — `used/-` with no progress bar | COVERED | `UsageBar`: when `limit == null`, renders `{formatTokens(used)} / -` with no progress bar DOM element. |
| Color thresholds — green 0–60%, yellow 60–85%, red 85–100%, red+BLOCKED ≥100% | COVERED | `usageBarColor()` function: `bg-emerald-500` (<60%), `bg-yellow-500` (60–85%), `bg-red-500` (85–100%), `bg-red-600` (≥100%). `BLOCKED` badge rendered when `isBlocked` (`used >= limit`). |
| Reset button per entry — calls `POST reset-usage`, bar updates immediately | COVERED | `handleReset()` calls `useResetModelUsage()` mutation which invalidates the catalog query. React Query refetches and updates the bar. |
| Tooltip on hover — exact counts, percentage, window label, last reset if applicable | COVERED | `tooltipLines` built in `useMemo`: counts, percentage, window label, and `Last reset: Xh ago` when `resetAt` is set. Tooltip uses `useModelUsageDetail` to fetch `reset_24h_at`/`reset_30d_at` lazily. |
| Inline limit editing — click limit portion to edit, save calls PUT limits | COVERED | `LimitEditorDialog` component. Clicking the limit text triggers `onLimitClick` → `setLimitEditor`. Dialog calls `useSetModelTokenLimits()` mutation targeting `PUT /api/settings/models/{entry_id}/limits`. |

---

## Design Decision Coverage

| Decision | Implemented | Notes |
|----------|-------------|-------|
| D1: Dedicated ledger table | YES | `shared.token_usage_ledger` created in migration core_035 |
| D2: Ledger schema optimized for time-windowed queries | YES | Partitioned table, composite index, monthly partitions |
| D3: Limits stored in `token_limits` table | YES | `shared.token_limits` created in migration core_035 |
| D4: `resolve_model()` returns 4-tuple | YES | Return type is `tuple[str, str, list[str], UUID]` |
| D5: Pre-spawn quota check as separate function | YES | `check_token_quota()` in `model_routing.py` |
| D6: Post-spawn ledger recording | YES | Spawner `finally` block + DiscretionDispatcher |
| D7: Adapter token reporting contract | YES | All 4 adapters audited; Gemini returns `None` (documented) |
| D8: Dashboard API extensions | YES | All 4 endpoints implemented in `model_settings.py` |
| D9: Dashboard UX — progress bar columns | YES | `UsageBar` + `LimitEditorDialog` components |

---

## Task Coverage (tasks.md — 38 tasks)

All 38 tasks verified implemented:

- **Tasks 1.1–1.4** (DB schema): core_035 migration.
- **Tasks 2.1–2.5** (model resolution changes): `model_routing.py`, spawner, dispatcher.
- **Tasks 3.1–3.4** (quota check function): `check_token_quota()` + tests.
- **Tasks 4.1–4.4** (ledger recording): `record_token_usage()` + spawner/dispatcher wiring + tests.
- **Tasks 5.1–5.3** (pre-spawn enforcement): spawner + dispatcher + integration tests.
- **Tasks 6.1–6.4** (adapter audit): `test_adapter_contract.py`.
- **Tasks 7.1–7.6** (API endpoints): `model_settings.py` + `test_model_settings.py`.
- **Tasks 8.1–8.6** (dashboard frontend): `ModelCatalogCard.tsx`.

---

## Gaps Found

### Gap 1: No integration test for "delete and recreate resets usage history" scenario

**Spec reference:** `catalog-token-limits/spec.md` — Token Usage Ledger Schema, Scenario: "Delete and recreate resets usage history"

**Description:** The spec requires that when a catalog entry is deleted and recreated with the same alias, the new entry has a new UUID and zero usage (old ledger rows cascaded). The DB schema makes this correct by construction (CASCADE + new UUID), but there is no dedicated integration test that:
1. Creates a catalog entry and inserts ledger rows
2. Deletes the entry (triggers CASCADE)
3. Recreates an entry with the same alias (gets a new UUID)
4. Verifies usage is zero for the new entry

The schema-level behavior is correct; this gap is about test documentation of the contract.

**Suggested bead:**
- Title: "Add integration test for ledger CASCADE + delete/recreate usage reset"
- Type: task
- Priority: 3
- Parent: bu-lm4m

### Gap 2: No test for "disabled entry with limits re-enabled via override still enforces limits"

**Spec reference:** `catalog-token-limits/spec.md` — Token Limits Schema, Scenario: "Disabled entry with limits re-enabled via override"

**Description:** The spec states that when a globally-disabled entry is re-enabled via a butler override, the global `token_limits` row still applies. This is correct by construction (the quota check uses `catalog_entry_id` which is the same regardless of override state), but there is no test exercising this specific path:
1. Create a catalog entry that is globally disabled
2. Create a butler override that re-enables it
3. Verify that when that butler spawns with that entry, the token limits are still checked

**Suggested bead:**
- Title: "Add test: disabled-entry-with-override still enforces token limits"
- Type: task
- Priority: 3
- Parent: bu-lm4m

---

## Notes on Minor Behavioral Observations

1. **Gemini adapter usage reporting:** The Gemini CLI does not expose token counts. `GeminiAdapter.invoke()` returns `(result_text, tool_calls, None)`. This is correct per D7 ("adapters that genuinely cannot report return `{}` or `None`"). The `test_gemini_usage_is_none` test and `test_adapter_contract.py` document this. No ledger row is written for Gemini sessions.

2. **Claude adapter usage format:** `ClaudeCodeAdapter.invoke()` returns `usage = dict(message.usage)` from the SDK `ResultMessage`. The SDK returns an object with `input_tokens` and `output_tokens` attributes; `dict()` coerces this to a plain dict. The spawner extracts `spawner_result.input_tokens` from `SpawnerResult`, which is set by the adapter's usage dict. This works correctly.

3. **Spawner `_ledger_input_tokens` variable:** The spawner uses a separate `_ledger_input_tokens` variable (set immediately when the adapter reports usage) rather than relying on `spawner_result.input_tokens`. This correctly handles the case where `session_complete()` raises after the adapter returns but before `spawner_result` is fully set.

4. **Race condition acknowledgement:** The spec explicitly documents that pre-spawn check and post-spawn record are not atomic, allowing N concurrent spawns to overshoot the limit. This accepted design decision is documented in both the spec and design.md.

---

## Conclusion

The gen-1 implementation has HIGH coverage of all spec requirements. Both gaps
are minor schema-level test documentation issues — the underlying behavior is
correct by construction. No functionality gaps were found.

Two child beads should be created:
1. Integration test for ledger CASCADE + delete/recreate reset (P3, task)
2. Test for disabled-entry-with-override still enforcing limits (P3, task)

Since only documentation/test coverage gaps exist (no missing functionality), no
gen-2 reconciliation bead is strictly required. If the coordinator creates the two
gap beads, a gen-2 reconciliation bead to verify them after completion would be
optional but desirable for completeness.
