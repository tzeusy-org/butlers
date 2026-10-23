# Chronicler Projection Recovery

> **Purpose:** Inspect operational sessions and recover historical episode projections.
> **Audience:** Operators diagnosing Chronicler projections.
> **Prerequisites:** [Chronicler](../butlers/chronicler.md) and
> [migration verification](chronicler-migration-verification.md).

These procedures moved from Chronicler's session guidance. The SQL reset below changes
projection checkpoints and replays historical rows; select the intended deployment and
verify the migration prerequisites before running it. Session agents receive no new authority.

## Ops sessions escape hatch

> **Canonical:** the projection-exclusion + ops-endpoint contract is normative
> in the spec, see `openspec/specs/butler-chronicler/spec.md` Requirement
> "Operational Sessions Excluded from User-Visible Projection". The notes below
> are the runtime-agent elaboration; keep them consistent with the spec.

Operational sessions are **never projected** into `chronicler.episodes`.
Engineers who need to audit scheduler cadence, switchboard tick rate, or QA
canary health can use the dedicated ops endpoint, which reads the raw sessions
tables directly via `fan_out_with_status`.

**Endpoint:** `GET /api/chronicler/ops/sessions`

**Purpose:** Returns only the sessions whose `trigger_source` matches the
exclusion set (`tick`, `classification`, `qa`, `healing`, `schedule:*`). These are invisible to
the user-facing `/api/chronicler/episodes` endpoint by design.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `trigger_source` | string (optional) | Filter to a specific ops source (e.g. `tick`, `schedule:chronicler_day_close`) |
| `since` | ISO datetime (optional) | `started_at >= since` |
| `until` | ISO datetime (optional) | `started_at < until` |
| `limit` | int 1–500 (default 50) | Max rows returned |

**Example (last 50 tick sessions across all butlers):**
```bash
curl 'http://localhost:8000/api/chronicler/ops/sessions?trigger_source=tick&limit=50'
```

**Example (all schedule:* sessions in the last hour):**
```bash
curl "http://localhost:8000/api/chronicler/ops/sessions?since=$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)"
```

**Response shape:**
```json
{
  "data": [
    {
      "butler": "chronicler",
      "session_id": "...",
      "trigger_source": "tick",
      "started_at": "2026-04-29T10:00:00Z",
      "completed_at": "2026-04-29T10:00:01Z",
      "duration_ms": 1234,
      "success": true,
      "model": "claude-sonnet-4-6"
    }
  ],
  "meta": {"total": 1, "offset": 0, "limit": 50}
}
```

**Invariant:** data from this endpoint will NEVER appear in
`/api/chronicler/episodes`. The separation is enforced at the adapter layer
(`CoreSessionsAdapter`) and tested in `tests/chronicler/test_ops_sessions_api.py`.

## Watermark reset (full re-projection)

Reset the adapter watermark to `NULL` so the next scheduled run re-projects all
calendar episodes from scratch.  Each row will receive `episode_entities` rows at
projection time (re-writes titles and re-runs the dedup pass).

```sql
UPDATE chronicler.projection_checkpoints
SET watermark = NULL,
    watermark_id = NULL,
    updated_at = now()
WHERE source_name = 'google_calendar.completed';
```

After the reset, trigger a manual adapter run or wait for the next scheduled
invocation.

Verify the resulting links with [backfill completeness checks](chronicler-migration-verification.md#verify-backfill-completeness).
