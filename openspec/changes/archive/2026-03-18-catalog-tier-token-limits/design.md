## Context

The Model Catalog (`shared.model_catalog`) provides dynamic model routing with per-butler overrides, but has no mechanism to limit token consumption. A runaway butler or misconfigured schedule can exhaust expensive API quota silently. Token counts are already collected by the spawner (via adapter `usage` dicts) and persisted to the `sessions` table, but there is no aggregation, budget enforcement, or dashboard visibility.

The sessions table is not suitable for quota enforcement because:
- It lacks `catalog_entry_id` — only stores the `model` string, which is ambiguous when multiple aliases map to the same underlying model.
- Querying sessions with time windows at every spawn adds latency to the hot path.
- Sessions include failed/error sessions that shouldn't count against quota.

The discretion dispatcher (`DiscretionDispatcher.call()`) currently discards the `_usage` return from adapter invocations entirely — it would bypass any session-based tracking.

## Goals / Non-Goals

**Goals:**
- Per-catalog-entry (alias) rolling-window token limits at 24h and 30d granularities
- Hard block at spawn time when a limit is exhausted, with a clear error message
- Dedicated usage ledger optimized for high-frequency time-windowed aggregation
- Dashboard visibility: usage-vs-limit progress bars on the model settings page
- Manual reset capability ("clear" button) that preserves historical usage data
- All runtime adapters reliably report `input_tokens` and `output_tokens`
- Discretion dispatcher records usage to the ledger (currently discarded)

**Non-Goals:**
- Per-butler token budgets (limits are per catalog entry, not per butler)
- Automatic tier downgrade on quota exhaustion (hard block only)
- Cost-based budgets (token count only; cost estimation stays in `pricing.py`)
- Real-time alerting or notifications when approaching thresholds
- Rate limiting (requests/sec) — this is about cumulative token budgets
- Backfilling historical usage from existing sessions into the new ledger

## Decisions

### D1: Dedicated ledger table instead of querying sessions

**Decision:** Create `shared.token_usage_ledger` as the single source of truth for usage aggregation, rather than querying the sessions table.

**Rationale:** The sessions table lacks `catalog_entry_id`, stores model as a string (ambiguous across aliases), and would require a JOIN with the catalog on every spawn. A dedicated append-only ledger keyed by `catalog_entry_id` with a timestamp column is purpose-built for fast `SUM() WHERE recorded_at > $cutoff` queries.

**Alternative considered:** Adding `catalog_entry_id` to sessions and querying sessions directly. Rejected because it couples quota enforcement to session lifecycle, mixes concerns, and the sessions table will grow much larger than what the quota system needs (it stores full output text, tool calls, etc.).

### D2: Ledger schema optimized for time-windowed queries

**Decision:** The ledger table uses `recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()` as its primary query axis, with a composite index on `(catalog_entry_id, recorded_at)` for the standard quota-check query pattern: `WHERE catalog_entry_id = $1 AND recorded_at > $2`.

For PostgreSQL 14+ environments, the table should be range-partitioned on `recorded_at` with monthly partitions. This enables:
- Fast partition pruning on time-windowed queries (both 24h and 30d windows hit at most 2 partitions)
- Efficient old-data cleanup via `DROP PARTITION` instead of `DELETE` (no vacuum bloat)
- Write distribution across partitions reduces contention

The Alembic migration creates initial partitions and, if pg_partman is available, registers the table for automatic monthly partition creation and retention (90 days). If pg_partman is not installed, the migration creates a wider buffer (current month + 5 months) and logs a warning that partitions must be managed manually or via a scheduled task.

**Schema:**
```sql
CREATE TABLE shared.token_usage_ledger (
    id          UUID DEFAULT gen_random_uuid(),
    catalog_entry_id UUID NOT NULL REFERENCES shared.model_catalog(id) ON DELETE CASCADE,
    butler_name TEXT NOT NULL,
    session_id  UUID,               -- nullable, discretion calls have no session
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, recorded_at)   -- required for partitioning
) PARTITION BY RANGE (recorded_at);

CREATE INDEX idx_ledger_entry_time
    ON shared.token_usage_ledger (catalog_entry_id, recorded_at);
```

**Alternative considered:** Simple B-tree index without partitioning. Would work at low volume but degrades as the ledger grows; partitioning is cheap to set up and provides a natural cleanup strategy.

### D3: Limits stored in a separate `token_limits` table

**Decision:** Create `shared.token_limits` with one row per catalog entry that has limits configured. Entries without a row in this table are unlimited.

**Schema:**
```sql
CREATE TABLE shared.token_limits (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_entry_id UUID NOT NULL UNIQUE REFERENCES shared.model_catalog(id) ON DELETE CASCADE,
    limit_24h        BIGINT,         -- NULL = unlimited for this window
    limit_30d        BIGINT,         -- NULL = unlimited for this window
    reset_24h_at     TIMESTAMPTZ,    -- manual reset marker for 24h window; NULL = no reset
    reset_30d_at     TIMESTAMPTZ,    -- manual reset marker for 30d window; NULL = no reset
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Token counting unit:** Limits and usage are counted as **total tokens** (`input_tokens + output_tokens`). This is simpler to reason about than separate input/output limits and matches how API providers typically bill.

**`reset_24h_at` / `reset_30d_at` semantics:** Each window has its own independent reset marker. When set, the effective window start becomes `GREATEST(reset_Xh_at, now() - interval)`. This lets the operator reset the 24h window without affecting the 30d budget and vice versa.

**Alternative considered:** Adding limit columns to `model_catalog` directly. Rejected to keep the catalog table focused on routing (alias, tier, priority) and limits as an orthogonal concern. Also avoids schema migration on the heavily-used catalog table.

### D4: `resolve_model()` returns `catalog_entry_id`

**Decision:** Extend the return type of `resolve_model()` from `tuple[str, str, list[str]]` to `tuple[str, str, list[str], UUID]`, adding the catalog entry's `id` as the fourth element.

This is required because:
- The spawner needs `catalog_entry_id` to check quota pre-spawn and record usage post-spawn.
- The discretion dispatcher needs it for the same reason.
- The SQL already selects from `mc.id` implicitly via the table; we just need to add it to the SELECT list.

**Migration:** Callers currently destructure as `runtime_type, model_id, extra_args = catalog_result`. These must be updated to 4-element unpacking. There are exactly two call sites: `Spawner._run_session()` and `DiscretionDispatcher.call()`.

### D5: Pre-spawn quota check as a separate function

**Decision:** Implement `check_token_quota(pool, catalog_entry_id) -> QuotaStatus` as a standalone async function in `model_routing.py`, co-located with `resolve_model()`. The spawner calls it after resolution, before adapter invocation.

`QuotaStatus` is a dataclass:
```python
@dataclasses.dataclass
class QuotaStatus:
    allowed: bool
    usage_24h: int          # tokens used in 24h window
    limit_24h: int | None   # None = unlimited
    usage_30d: int          # tokens used in 30d window
    limit_30d: int | None   # None = unlimited
```

The check query joins `token_limits` with a `SUM()` over `token_usage_ledger` for both windows in a single round-trip:

```sql
WITH limits AS (
    SELECT limit_24h, limit_30d,
           COALESCE(reset_24h_at, '-infinity'::timestamptz) AS reset_24h_at,
           COALESCE(reset_30d_at, '-infinity'::timestamptz) AS reset_30d_at
    FROM shared.token_limits
    WHERE catalog_entry_id = $1
),
usage AS (
    SELECT
        COALESCE(SUM(input_tokens + output_tokens)
            FILTER (WHERE recorded_at > GREATEST(
                (SELECT reset_24h_at FROM limits),
                now() - interval '24 hours'
            )), 0) AS used_24h,
        COALESCE(SUM(input_tokens + output_tokens)
            FILTER (WHERE recorded_at > GREATEST(
                (SELECT reset_30d_at FROM limits),
                now() - interval '30 days'
            )), 0) AS used_30d
    FROM shared.token_usage_ledger
    WHERE catalog_entry_id = $1
      AND recorded_at > GREATEST(
          LEAST(
              (SELECT reset_24h_at FROM limits),
              (SELECT reset_30d_at FROM limits)
          ),
          now() - interval '30 days'
      )
)
SELECT l.limit_24h, l.limit_30d, u.used_24h, u.used_30d
FROM usage u, limits l
```

If no row exists in `token_limits`, the entry is unlimited and the function returns `QuotaStatus(allowed=True, usage_24h=0, usage_30d=0, limit_24h=None, limit_30d=None)` without querying the ledger. If the quota check query itself fails (DB error, timeout), the function fails open — returns `allowed=True` and logs a warning. The guardrail must never become a single point of failure.

**Alternative considered:** Inlining the check into `resolve_model()`. Rejected because resolution and enforcement are separate concerns — resolution picks the best model, enforcement decides whether to allow it. Keeping them separate also means the quota check can be called independently (e.g., by the dashboard preview endpoint).

### D6: Post-spawn ledger recording

**Decision:** The spawner records usage to the ledger in the `finally` block, alongside the existing metrics recording (line ~1245). This ensures usage is recorded regardless of session success or failure — tokens are consumed by the upstream provider on invocation, so failed sessions that reported usage MUST count against the quota.

```python
# In Spawner._run_session(), finally block:
if spawner_result is not None and spawner_result.input_tokens is not None:
    if catalog_entry_id is not None:
        await record_token_usage(
            self._pool,
            catalog_entry_id=catalog_entry_id,
            butler_name=self._config.name,
            session_id=session_id,
            input_tokens=spawner_result.input_tokens,
            output_tokens=spawner_result.output_tokens or 0,
        )
```

The discretion dispatcher must be similarly updated — it currently discards `_usage`. After this change, `DiscretionDispatcher.call()` will capture the usage dict and call `record_token_usage()`.

`record_token_usage()` is a simple INSERT into the ledger. It is best-effort (wrapped in try/except) — a ledger write failure must never block a session result from being returned.

### D7: Adapter token reporting contract

**Decision:** All runtime adapters MUST return `{"input_tokens": int, "output_tokens": int}` in their usage dict from `invoke()`. The spawner already extracts these fields (line ~1077). The contract is that:
- Adapters that can report usage MUST do so.
- Adapters that genuinely cannot (e.g., a process-based adapter where the CLI doesn't expose token counts) return `{}` or `None` — no ledger row is written for that invocation.
- The audit of all adapters is a task, not a design decision. Known adapters: `claude`, `codex`, `gemini`, `opencode`, `ollama` (via opencode).

### D8: Dashboard API extensions

**Decision:** Extend the existing model settings API with:

1. **`GET /api/settings/models`** — existing endpoint gains `usage_24h`, `usage_30d`, `limit_24h`, `limit_30d` fields on each entry. This requires a LEFT JOIN with `token_limits` and a subquery/lateral join against the ledger. To keep the list endpoint fast, usage aggregation is done in a single CTE across all catalog entries.

2. **`PUT /api/settings/models/{entry_id}/limits`** — set or update 24h and/or 30d limits for a catalog entry. Body: `{"limit_24h": int | null, "limit_30d": int | null}`. Setting both to null effectively removes the limit (deletes the `token_limits` row).

3. **`POST /api/settings/models/{entry_id}/reset-usage`** — reset one or both windows. Body: `{"window": "24h" | "30d" | "both"}`. Sets the corresponding `reset_24h_at` and/or `reset_30d_at` to `now()`. Creates the limits row (with null limits) if it doesn't exist, since the reset timestamps need a row to live in.

4. **`GET /api/settings/models/{entry_id}/usage`** — detailed usage for a single entry: `{usage_24h, usage_30d, limit_24h, limit_30d, reset_24h_at, reset_30d_at, percent_24h, percent_30d}`. Used by dashboard for tooltip/detail view.

### D9: Dashboard UX — progress bar columns

**Decision:** The model catalog table on `/butlers/settings` gains two columns after the existing ones:

| Column | Content |
|--------|---------|
| **24h** | Mini horizontal progress bar (green→yellow→red gradient) + text showing `used/limit` (e.g., "142K / 500K"). If no limit set, show `used/-` (e.g., "142K / -") so usage is always visible. |
| **30d** | Same format as 24h. |

Color thresholds:
- 0–60%: green
- 60–85%: yellow
- 85–100%: red
- 100%+: red with a "BLOCKED" badge

Each bar has a small reset icon-button (circular arrow) that calls the reset endpoint. Tooltip on hover shows exact token counts, percentage, window type label ("Rolling 24h/30d window"), and last reset time if applicable.

Entries with no limits configured show usage with a dash for the limit (e.g., "42K / -") — usage is always visible regardless of whether a limit is set. The limit values are editable inline (click the limit portion to set or change it).

## Risks / Trade-offs

**[Race condition on quota check]** → The pre-spawn check and post-spawn record are not atomic. N concurrent spawns targeting the same catalog entry could all pass the check before any records usage, overshooting the limit by up to N sessions' worth of tokens. **Mitigation:** Acceptable for this use case — the limit is a guardrail, not a billing boundary. At current concurrency levels the overshoot is small. If tighter enforcement is needed later, we can add a `SELECT ... FOR UPDATE` advisory lock on the limits row.

**[Ledger write volume]** → Every session and every discretion call writes a ledger row. At current volume (~hundreds/day) this is trivial. **Mitigation:** Monthly partitioning + partition drop keeps table size bounded. The 90-day retention means at most 3 active partitions.

**[Partition maintenance]** → Monthly partitions must be created ahead of time. **Mitigation:** pg_partman handles automatic partition creation and retention. The Alembic migration bootstraps initial partitions and registers the table with pg_partman. If a partition is missing (pg_partman misconfigured), the INSERT fails — the `record_token_usage()` function catches this and logs a warning rather than blocking the session.

**[Breaking change to `resolve_model()` return type]** → Adding `catalog_entry_id` as a 4th tuple element breaks existing callers. **Mitigation:** There are exactly two call sites (spawner, discretion dispatcher), both in this codebase. Update both in the same migration. No external consumers.

**[Adapters that don't report tokens]** → Some adapters may return empty usage dicts. **Mitigation:** No ledger row is written, the quota is not decremented, and the entry appears to use no tokens. This is preferable to blocking — we audit adapters and fix reporting as a separate task.

## Migration Plan

1. **Alembic migration** creates `token_usage_ledger` (partitioned), `token_limits`, initial partitions, and indexes.
2. **`resolve_model()`** updated to return 4-tuple. Both call sites updated atomically.
3. **`check_token_quota()`** and **`record_token_usage()`** added to `model_routing.py`.
4. **Spawner** wired: quota check after resolution, ledger write in finally block.
5. **Discretion dispatcher** wired: capture usage, record to ledger.
6. **Adapter audit**: verify all adapters return token counts.
7. **API endpoints** added for limits CRUD, usage query, and reset.
8. **Dashboard** updated with usage columns.

**Rollback:** Drop the two new tables. Revert `resolve_model()` to 3-tuple. Remove quota check and ledger write from spawner. All changes are additive — the existing system works without them.

## Open Questions

None — all design decisions resolved.
