## Why

When a runtime session hangs or fails silently (e.g. a Codex process stuck for 14+ minutes), there is no way to diagnose the issue from the session detail view — it shows only the input prompt and null fields. Process-level signals (stderr, exit code, PID, command) are captured during execution but discarded before reaching the database. Operators need these diagnostics to triage hung or failed sessions without SSH-ing into the host.

## What Changes

- New `session_process_logs` table (1:1 with sessions, FK + CASCADE) storing pid, exit_code, command, stderr, and runtime_type
- 14-day TTL via `expires_at` column with periodic cleanup to prevent storage bloat
- All subprocess-based runtime adapters (Codex, Gemini, OpenCode) capture process metadata via a `last_process_info` property on the adapter
- Spawner writes process log to DB after session completion (both success and error paths), best-effort (never blocks result)
- Session detail API endpoint joins process log into the response when available
- Stderr capped at 32 KiB per row to bound storage

## Capabilities

### New Capabilities
- `session-process-logs`: TTL-managed process-level diagnostics table, write/read/cleanup functions, and API integration with session detail endpoint

### Modified Capabilities
- `core-sessions`: Session detail API response gains an optional `process_log` field (additive, non-breaking)
- `core-spawner`: Spawner writes process log after runtime invocation (additive, non-breaking)

## Impact

- **Database**: New table `session_process_logs` per butler schema (migration core_022)
- **Runtime adapters**: `base.py` gains `last_process_info` property on ABC; Codex, Gemini, OpenCode adapters implement it
- **Spawner**: Two new best-effort write calls after `session_complete` (success + error paths)
- **API**: Session detail endpoint (`GET /api/butlers/{name}/sessions/{id}`) returns optional `process_log` object
- **API models**: New `ProcessLog` Pydantic model added to `session.py`
- **Cleanup**: New `session_process_logs.cleanup()` function for periodic TTL reaping (caller responsibility to schedule)
