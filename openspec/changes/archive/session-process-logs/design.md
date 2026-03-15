## Context

When a butler spawns a subprocess-based runtime (Codex, Gemini, OpenCode), process-level signals — stderr, exit code, PID, command line — are available in memory during execution but discarded after parsing. The session record captures only structured output (result text, tool calls, token usage). When a session hangs or fails silently, operators have no diagnostic data in the dashboard.

The core `sessions` table follows an append-only contract (no DELETE/DROP/TRUNCATE in `sessions.py`). Process logs are inherently ephemeral and can be large (stderr up to 32 KiB), so they belong in a separate TTL-managed table.

## Goals / Non-Goals

**Goals:**
- Capture process-level diagnostics (pid, exit_code, command, stderr, runtime_type) for every subprocess-based runtime session
- Make diagnostics available through the existing session detail API
- Auto-expire old rows to prevent storage bloat (14-day default TTL)
- Never block or degrade session completion — all writes are best-effort

**Non-Goals:**
- Capturing stdout (already parsed into session result/tool_calls)
- Capturing diagnostics for SDK-based adapters (ClaudeCodeAdapter) — no subprocess to inspect
- Real-time streaming of process output during execution
- Automatic alerting on process anomalies

## Decisions

### Separate table vs. columns on sessions
**Decision**: Separate `session_process_logs` table with FK to sessions.

**Rationale**: The `sessions` table is append-only by convention, enforced by a safety test. Process logs need periodic DELETE for TTL cleanup. A separate table keeps the append-only contract intact and allows independent lifecycle management. CASCADE delete means session cleanup automatically cleans process logs.

**Alternative considered**: Adding columns directly to sessions — rejected because it violates the append-only contract and bloats every session row even when no process info exists (SDK-based sessions).

### Separate module vs. extending sessions.py
**Decision**: New `session_process_logs.py` module in `butlers/core/`.

**Rationale**: The safety test (`test_module_has_no_drop_or_truncate`) asserts that `sessions.py` contains no `DELETE FROM`, `DROP TABLE`, or `TRUNCATE`. The cleanup function requires `DELETE FROM`. A separate module respects this boundary.

### Property on adapter vs. return value change
**Decision**: `last_process_info` property on RuntimeAdapter ABC, populated after each `invoke()`.

**Rationale**: Changing the `invoke()` return signature from `(text, tool_calls, usage)` to a 4-tuple would be a breaking change across all adapters. A property is additive — the base class returns `None` by default, and only subprocess adapters populate it. The spawner reads it after `invoke()` returns.

### Stderr size cap
**Decision**: 32 KiB per row, hard-capped in the write function.

**Rationale**: Codex/Gemini/OpenCode can produce unbounded stderr (progress bars, debug output, warnings). 32 KiB captures the meaningful tail of most failures while keeping per-row storage bounded. Excess is trimmed with a marker.

### TTL implementation
**Decision**: `expires_at` column with periodic `cleanup()` call, not a database-level cron job.

**Rationale**: The project already uses application-level cleanup patterns (approvals retention, memory episode TTL, switchboard metadata pruning). Keeping the same pattern avoids introducing pg_cron or similar dependencies. The caller (daemon tick handler or scheduled task) invokes `cleanup()`.

## Risks / Trade-offs

- **[Storage spikes]** → Mitigated by 32 KiB stderr cap and 14-day TTL. Worst case: ~2.2 MB per 100 sessions/day × 14 days ≈ 30 MB per butler.
- **[Cleanup not called]** → If no caller schedules `cleanup()`, expired rows accumulate. Mitigated by CASCADE delete (sessions table cleanup also cleans process logs) and the `expires_at` filter in read queries (stale rows are never returned).
- **[Table doesn't exist yet]** → API endpoint wraps the process log query in try/except to gracefully handle pre-migration databases.
- **[Best-effort write failures]** → Process log write failures are logged at DEBUG level and never propagate. Acceptable because this is diagnostic data, not business-critical.
