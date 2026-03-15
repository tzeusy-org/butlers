# Education Analytics Metrics Verification Report

**Spec:** `openspec/specs/module-education-analytics/spec.md`
**Implementation:** `roster/education/tools/analytics.py`
**MCP tools:** `roster/education/modules/tools.py`
**API endpoints:** `roster/education/api/router.py`
**Date verified:** 2026-03-15

---

## Summary

All 14 metrics required by the spec are computed in `analytics_compute_snapshot()` and persisted
to `analytics_snapshots.metrics` (JSONB). All metrics are accessible via the MCP tools and the
REST API. One semantic deviation from the spec was found in the `time_of_day_distribution`
counting logic.

---

## Metric-by-Metric Verification

### Fully Implemented (13/14)

| Metric | Formula | Status |
|---|---|---|
| `total_nodes` | COUNT of nodes for mind map | ✅ Complete |
| `mastered_nodes` | COUNT where `mastery_status = 'mastered'` | ✅ Complete |
| `mastery_pct` | `mastered_nodes / total_nodes`, rounded to 2dp | ✅ Complete |
| `avg_ease_factor` | mean of all node `ease_factor` values, rounded to 2dp | ✅ Complete |
| `retention_rate_7d` | review-only responses, last 7 days; ratio of quality >= 3; null if no review responses | ✅ Complete |
| `retention_rate_30d` | review-only responses, last 30 days; same formula as 7d; null if no review responses | ✅ Complete |
| `velocity_nodes_per_week` | avg nodes reaching mastered per week over 4 weekly buckets (28 days) | ✅ Complete |
| `estimated_completion_days` | `ceil((total_nodes - mastered_nodes) / velocity * 7)`; null when velocity=0 or all mastered | ✅ Complete |
| `struggling_nodes` | node UUIDs with 5+ review responses and last-5-review avg quality < 2.5 | ✅ Complete |
| `strongest_subtree` | node UUID with highest average mastery_score in its subtree | ✅ Complete |
| `total_quiz_responses` | COUNT of all quiz_responses for mind map | ✅ Complete |
| `avg_quality_score` | mean of all quiz response quality scores, rounded to 1dp | ✅ Complete |
| `sessions_this_period` | COUNT DISTINCT of responded_at::date in last 30 days | ✅ Complete |

### Deviating from Spec (1/14)

| Metric | Spec | Implementation | Gap |
|---|---|---|---|
| `time_of_day_distribution` | Count of **quiz sessions grouped by date** in each time bucket | Count of **individual response rows** in each time bucket | Overcounts when multiple responses occur in the same session |

**Spec requirement** (from `Requirement: Time-of-Day Distribution Bucketing`):
> The `time_of_day_distribution` JSONB field SHALL be a map of `{"morning": <int>, "afternoon":
> <int>, "evening": <int>}` counting the number of **quiz sessions (grouped by date)** falling
> in each bucket.

**Implementation** (lines 180–194 in `analytics.py`):
```python
time_rows = await conn.fetch(
    """
    SELECT EXTRACT(HOUR FROM responded_at AT TIME ZONE 'UTC')::int AS hour
    FROM education.quiz_responses
    WHERE mind_map_id = $1
      AND responded_at::date <= $2
      AND responded_at::date > $2 - INTERVAL '30 days'
    """,
    ...
)
tod_dist: dict[str, int] = {"morning": 0, "afternoon": 0, "evening": 0}
for row in time_rows:
    bucket = _bucket_hour(row["hour"])
    tod_dist[bucket] += 1
```

The query fetches one row per **quiz response**, not per **session date**. If a user has 5
responses on Tuesday morning, the implementation counts 5 toward `morning`; the spec would count
1 (one session on that date). The three keys (`morning`, `afternoon`, `evening`) are always
present in the output (zero-filled when empty), which satisfies the corresponding scenario
requirement. Only the counting unit is wrong.

---

## Nightly Job Verification

**Configured in `butler.toml`:**
```toml
[[butler.schedule]]
name = "nightly-analytics"
cron = "0 3 * * *"
dispatch_mode = "job"
job_name = "compute_analytics_snapshots"
```

The scheduler calls `analytics_compute_all(pool)`, which:
- Queries active mind maps (quiz activity in last 90 days OR unmastered nodes) ✅
- Calls `analytics_compute_snapshot()` for each active map ✅
- Uses upsert (`ON CONFLICT (mind_map_id, snapshot_date) DO UPDATE`) ✅
- Returns count of snapshots computed ✅
- Fires `curriculum_replan()` callback when `struggling_nodes >= 3` OR `retention_rate_7d < 0.60` ✅

---

## MCP Tool Exposure

All analytics functions are registered as MCP tools in `roster/education/modules/tools.py`:

| MCP Tool | Underlying Function |
|---|---|
| `analytics_get_snapshot` | `analytics_get_snapshot(pool, mind_map_id, date?)` |
| `analytics_get_trend` | `analytics_get_trend(pool, mind_map_id, days=30)` |
| `analytics_get_cross_topic` | `analytics_get_cross_topic(pool)` |

`analytics_compute_snapshot` and `analytics_compute_all` are **not** exposed as direct MCP
tools — they are only invoked by the nightly scheduler job. This is intentional by design:
snapshot computation is a background operation, not an on-demand tool.

---

## API Endpoint Exposure

Analytics are exposed via the REST API (`roster/education/api/router.py`):

| Endpoint | Returns |
|---|---|
| `GET /api/education/mind-maps/{id}/analytics` | Latest snapshot `metrics` dict + optional trend; the `metrics` dict contains all 14 fields |
| `GET /api/education/analytics/cross-topic` | Cross-topic comparison with per-topic `mastery_pct`, `retention_rate_7d`, `velocity` |

The `AnalyticsSnapshotResponse` model uses `metrics: dict[str, Any]` — all 14 metric fields
pass through without schema filtering. The `CrossTopicAnalyticsResponse` model exposes a
subset (3 fields per topic plus portfolio-level stats); this is acceptable as a summary view.

---

## Weekly Progress Digest

The weekly digest schedule (`cron: "0 9 * * 0"`) uses `dispatch_mode = "prompt"`, which fires
an ephemeral LLM session. The prompt instructs the agent to read `analytics_get_trend()` data,
identify retention and velocity trends, and deliver a summary via `notify()`. This satisfies the
spec requirement but the trend-direction analysis (improving / stable / declining) is performed
by the LLM at runtime rather than computed deterministically in analytics code.

---

## Identified Gap

### GAP-1: `time_of_day_distribution` counts responses, not sessions

**Severity:** Low
**File:** `roster/education/tools/analytics.py`, lines 180–194
**Spec reference:** `Requirement: Time-of-Day Distribution Bucketing`

The implementation counts individual quiz response rows per time bucket. The spec requires
counting the number of **quiz sessions (grouped by date)** per bucket. To fix this, the query
should use `COUNT(DISTINCT responded_at::date)` grouped by hour bucket, or group responses by
`(responded_at::date, time_bucket)` and count distinct dates per bucket.

This is a documentation-only finding. No fix is implemented here per task instructions.

---

## Conclusion

The analytics subsystem is substantially complete and faithfully implements the spec. All 14
specified metrics are computed and persisted in each snapshot. All required behavior (nightly
job, idempotent upsert, retention rate filtering, velocity bucketing, null safety, feedback loop
triggers, trend retrieval, cross-topic comparison) is implemented and tested. One minor semantic
gap was found: `time_of_day_distribution` counts responses rather than sessions (grouped by
date) as specified.
