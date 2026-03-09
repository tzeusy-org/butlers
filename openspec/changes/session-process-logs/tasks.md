## 1. Database

- [ ] 1.1 Create migration `core_022_create_session_process_logs` with table schema, FK, CASCADE, expires_at default, and index
- [ ] 1.2 Run migration against dev database and verify table exists per butler schema

## 2. Core Module

- [ ] 2.1 Create `src/butlers/core/session_process_logs.py` with `write()`, `get()`, and `cleanup()` functions
- [ ] 2.2 Verify `write()` caps stderr at 32 KiB and upserts on duplicate session_id
- [ ] 2.3 Verify `cleanup()` deletes only expired rows and returns count

## 3. Runtime Adapters

- [ ] 3.1 Add `last_process_info` property to `RuntimeAdapter` ABC (returns None by default)
- [ ] 3.2 Implement `last_process_info` in `CodexAdapter` — capture pid, exit_code, command, stderr on success and timeout paths
- [ ] 3.3 Implement `last_process_info` in `GeminiAdapter` — same pattern
- [ ] 3.4 Implement `last_process_info` in `OpenCodeAdapter` — same pattern

## 4. Spawner Integration

- [ ] 4.1 Import `session_process_log_write` from `session_process_logs` module in spawner
- [ ] 4.2 Add best-effort process log write after `session_complete` on success path
- [ ] 4.3 Add best-effort process log write after `session_complete` on error path

## 5. API

- [ ] 5.1 Add `ProcessLog` Pydantic model to `src/butlers/api/models/session.py`
- [ ] 5.2 Add optional `process_log` field to `SessionDetail` model
- [ ] 5.3 Update session detail endpoint to query and attach process log (best-effort, graceful on missing table)

## 6. Tests

- [ ] 6.1 Unit tests for `session_process_logs.write()` — insert, upsert, stderr cap, custom TTL
- [ ] 6.2 Unit tests for `session_process_logs.get()` — found, expired, missing
- [ ] 6.3 Unit tests for `session_process_logs.cleanup()` — deletes expired, skips non-expired
- [ ] 6.4 Unit tests for adapter `last_process_info` — verify populated after invoke success and timeout
- [ ] 6.5 Verify existing tests pass (no regressions from append-only safety test)
