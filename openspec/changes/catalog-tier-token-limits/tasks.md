## 1. Database Schema

- [ ] 1.1 Create Alembic migration with `shared.token_usage_ledger` table (range-partitioned on `recorded_at`, monthly partitions for current + 2 months, composite PK `(id, recorded_at)`, FK to `model_catalog` with CASCADE)
- [ ] 1.2 Add `idx_ledger_entry_time` composite index on `(catalog_entry_id, recorded_at)`
- [ ] 1.3 Register `token_usage_ledger` with pg_partman if available; if pg_partman is not installed, create 6 months of initial partitions and log a warning
- [ ] 1.4 Create `shared.token_limits` table in same migration (UNIQUE on `catalog_entry_id`, FK CASCADE, `limit_24h`, `limit_30d`, `reset_24h_at`, `reset_30d_at`)

## 2. Model Resolution Changes

- [ ] 2.1 Update `_RESOLVE_SQL` in `model_routing.py` to include `mc.id` in SELECT list
- [ ] 2.2 Change `resolve_model()` return type from `tuple[str, str, list[str]]` to `tuple[str, str, list[str], UUID]`
- [ ] 2.3 Update spawner `_run_session()` to unpack 4-element tuple and propagate `catalog_entry_id` through the spawn flow
- [ ] 2.4 Update `DiscretionDispatcher.call()` to unpack 4-element tuple and retain `catalog_entry_id`
- [ ] 2.5 Update all tests for `resolve_model()` to expect 4-tuple return

## 3. Quota Check Function

- [ ] 3.1 Add `QuotaStatus` dataclass to `model_routing.py`
- [ ] 3.2 Implement `check_token_quota(pool, catalog_entry_id) -> QuotaStatus` with CTE-based single-query for both windows (respecting independent `reset_24h_at` / `reset_30d_at`)
- [ ] 3.3 Implement fail-open error handling: wrap quota check query in try/except, return `QuotaStatus(allowed=True, ...)` on DB errors, log warning
- [ ] 3.4 Write unit tests for `check_token_quota()`: no limits row (fast path, no ledger query), within limits, 24h exceeded, 30d exceeded, one unlimited + other exceeded, reset markers, DB error returns fail-open

## 4. Ledger Recording

- [ ] 4.1 Implement `record_token_usage(pool, catalog_entry_id, butler_name, session_id, input_tokens, output_tokens)` in `model_routing.py` — best-effort INSERT with try/except
- [ ] 4.2 Wire ledger write into spawner `finally` block (after existing metrics recording, guarded by `catalog_entry_id is not None` and `input_tokens is not None`) — records for BOTH successful and failed sessions (tokens are consumed by the provider regardless of outcome)
- [ ] 4.3 Update `DiscretionDispatcher.call()` to capture `_usage` dict and call `record_token_usage()` with `session_id=None`
- [ ] 4.4 Write tests for ledger recording (success path, failed session with usage still records, adapter failure without usage skips recording, missing partition handled gracefully, no-op when catalog_entry_id is None)

## 5. Pre-Spawn Enforcement

- [ ] 5.1 Wire `check_token_quota()` into spawner `_run_session()` after `resolve_model()` and before adapter invocation — if `allowed=False`, return `SpawnerResult(success=False)` with descriptive error
- [ ] 5.2 Wire `check_token_quota()` into `DiscretionDispatcher.call()` after `resolve_model()` — raise `RuntimeError` on quota exhaustion
- [ ] 5.3 Write integration tests: spawn blocked when 24h limit exhausted, spawn blocked when 30d limit exhausted, spawn proceeds when within limits, spawn proceeds when no limits configured

## 6. Adapter Audit

- [ ] 6.1 Audit `claude` adapter — verify `invoke()` returns `{"input_tokens": int, "output_tokens": int}` in usage dict
- [ ] 6.2 Audit `codex` adapter — verify or fix token reporting
- [ ] 6.3 Audit `gemini` adapter — verify or fix token reporting
- [ ] 6.4 Audit `opencode` adapter — verify or fix token reporting (including ollama models)

## 7. API Endpoints

- [ ] 7.1 Extend `GET /api/settings/models` response to include `usage_24h`, `usage_30d`, `limit_24h`, `limit_30d` per entry (single CTE aggregation across all entries)
- [ ] 7.2 Add `PUT /api/settings/models/{entry_id}/limits` endpoint — upsert token limits, delete row when both null, validate limit values >= 1 (reject 0 and negatives with 422)
- [ ] 7.3 Add `POST /api/settings/models/{entry_id}/reset-usage` endpoint — accepts `{"window": "24h" | "30d" | "both"}`, sets corresponding `reset_*_at` to now(), creates limits row if needed
- [ ] 7.4 Add `GET /api/settings/models/{entry_id}/usage` endpoint — returns detailed usage with percentages
- [ ] 7.5 Extend `GET /api/butlers/{name}/resolve-model` response to include `quota_blocked`, `usage_24h`, `limit_24h`, `usage_30d`, `limit_30d` — must query actual ledger usage (not the `check_token_quota` fast path) so unlimited entries show real usage matching the dashboard
- [ ] 7.6 Write API tests for all new endpoints (CRUD limits, reset, usage query, list augmentation, resolve-model quota fields, limit validation rejects 0/negatives)

## 8. Dashboard Frontend

- [ ] 8.1 Add 24h and 30d usage columns to model catalog table on `/butlers/settings`
- [ ] 8.2 Implement progress bar component with green→yellow→red gradient (0–60% green, 60–85% yellow, 85–100% red, 100%+ red with BLOCKED badge)
- [ ] 8.3 Show `used/-` format when no limit is configured (usage always visible)
- [ ] 8.4 Add reset icon-button per entry per window, wired to reset-usage endpoint
- [ ] 8.5 Add tooltip on hover showing exact token counts, percentage, window type ("Rolling 24h/30d window"), and last reset time if applicable
- [ ] 8.6 Implement inline limit editing (click limit portion to edit, save calls PUT limits endpoint)
