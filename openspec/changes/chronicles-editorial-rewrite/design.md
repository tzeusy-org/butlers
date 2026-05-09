# Design: Chronicles Editorial Rewrite

## Page shape (editorial archetype)

The landing surface for `/chronicles` adopts the editorial archetype defined
in `about/heart-and-soul/design-language.md` and laid out in
`about/lay-and-land/frontend.md` (§Editorial archetype layout).

```
┌─────────────────────────────────────────────────────────────────────┐
│ DateEyebrow              BriefingStatus pill (cached / templated)   │
├──────────────────────────────────┬──────────────────────────────────┤
│ Display headline                 │ KPI strip (4 cells, hairline)   │
│                                  │   hours-by-top-lane             │
│ Voice paragraph                  │   longest-episode               │
│   (sourced from day-close cache  │   longest-gap                   │
│    or templated fallback)        │   sleep-duration                │
│                                  ├──────────────────────────────────┤
│                                  │ Attention list                  │
│                                  │   anomalies                     │
│                                  │   source-health                 │
│                                  │   open corrections              │
│                                  ├──────────────────────────────────┤
│                                  │ Recent days index               │
│                                  │   last 7 days, eyebrow-titled   │
│                                  │                                  │
│                                  │ Drilldown launcher              │
│                                  │   "Open Gantt for window"       │
│                                  │   "Open Map"                    │
│                                  │   "Open day-close drawer"       │
└──────────────────────────────────┴──────────────────────────────────┘
                ChroniclesDrilldownPanel (lazy, below the fold)
                  Existing Gantt / Map / Aggregations / Drawer
```

The left column is the Voice surface: date eyebrow, status pill, Display
headline, and serif paragraph. The right column is the index rail: KPI
strip, attention list, recent-days index, and drilldown launcher. The
drilldown panel defers everything that used to be the workspace surface.

## API contracts

### `GET /api/chronicler/briefing?date=YYYY-MM-DD`

Returns:

```json
{
  "date": "2026-05-08",
  "state_class": "urgent" | "busy" | "mild" | "quiet",
  "headline": "string",
  "voice_paragraph": "string",
  "voice_source": "llm·cached" | "templated" | "stale",
  "kpi": {
    "hours_by_top_lanes": [{"lane": "conversations", "hours": 2.4}, ...],
    "longest_episode_minutes": 95,
    "longest_episode_title": "Conversation with Anna",
    "longest_gap_minutes": 312,
    "sleep_minutes": 432,
    "streaks": {"sleep": 4, "exercise": 2}
  },
  "attention_items": [
    {"kind": "anomaly", "severity": "medium", "title": "Short sleep",
     "detail": "5h 12m, well below 7-day median", "action_href": null},
    {"kind": "source_health", "severity": "high", "title": "Spotify projection error",
     "detail": "last_error 2h ago", "action_href": "/ingestion/connectors/spotify"},
    {"kind": "open_correction", "severity": "low", "title": "1 unresolved correction",
     "detail": null, "action_href": null}
  ],
  "recent_days": [
    {"date": "2026-05-08", "total_minutes": 642, "top_lane": "conversations", "episode_count": 23},
    ...
  ]
}
```

`state_class` is deterministic from attention-item severity counts and
total episode density. The headline templates are owned by the API and
keyed by `state_class` (sentence case, no exclamation, no em dash).

`voice_paragraph` is read from `chronicler.tier2_cache` for
`cache_key = day_close:{date}`. If the cache row is fresh
(per the staleness logic already in `get_day_close_cache`), use its
`prose` and set `voice_source = "llm·cached"`. If stale, set
`voice_source = "stale"` and use the cached prose with a stale marker
prepended in the frontend pill (the paragraph itself is unchanged).
If the cache row is missing, fall back to a templated paragraph derived
from the KPI and attention shape; set `voice_source = "templated"`.

The endpoint NEVER initiates a new LLM call. The user explicitly
refreshing the day-close cache is a separate, rate-limited path
(existing `POST /api/chronicler/aggregate/day-close/refresh`).

### `GET /api/chronicler/attention?since=ISO&until=ISO`

Returns the attention list as a standalone resource (the briefing also
embeds it). Attention items are derived from:

- Sleep anomaly: today's sleep_minutes < 0.7 × 7-day median.
- Waking gap: contiguous gap between any two episodes within the window
  that exceeds 6 hours during waking hours (06:00–22:00 owner-tz). Each
  qualifying gap is one item.
- Source health: any row in source-state with `last_error` non-null in
  the last 24h, or with `inactive_reason` set.
- Open corrections: count of override rows whose target episode lies
  within the window and whose `corrected_tombstone_at` is null.

### `GET /api/chronicler/kpi?date=YYYY-MM-DD`

Returns the KPI block from the briefing as a standalone resource. Same
shape as `briefing.kpi`. Useful for cheaper polling.

## Adapter additions

### Health (extended)

`google_health.py` adds three new projection paths alongside the
existing sleep flow, each with its own job in `chronicler/jobs.py`:

| Predicate / table fact     | Output                                          |
|----------------------------|-------------------------------------------------|
| `workout_session`          | episode `workout_episode`, category `other`    |
| `measurement_steps` (`daily_steps` alias) | point event `health.steps` (count + day window)|
| `measurement_resting_hr` (`heart_rate_summary` alias) | point event `health.heart_rate` (avg + max) |

The sleep adapter class stays. Three new sibling classes
(`GoogleHealthWorkoutAdapter`, `GoogleHealthStepsAdapter`,
`GoogleHealthHeartRateAdapter`) live in the same module and share
helpers. Each is registered in `__init__.py` and `jobs.py`.
Each degrades gracefully when `health.facts` or its predicate is absent.
Workout episodes remain `normal` privacy for aggregate duration/distance
facts and escalate to `sensitive` when the source fact carries heart-rate
metadata.

### Focus

`adapters/focus.py` derives `focus_block` episodes from already-projected
chronicler data (read-from-self is permitted: the chronicler is allowed
to read its own tables for derivations).

Signal:
- A `core.sessions` work episode with category `tasks` and duration
  ≥ 45 min.
- AND no overlapping `route` (conversation) episode in the window.

Plus:
- A `google_calendar.completed` `scheduled_block` episode whose title
  matches `focus|deep work|pomodoro` (case-insensitive). Title length
  ≤ 80 chars (defensive guard).

Output: `focus_block` episodes with category `tasks`, source_name
`chronicler.focus_inferred`, source_ref deterministic on the underlying
episode id (so re-running is idempotent). Boundary precision `minute`.
Privacy `normal`. Watermark on `created_at` of the underlying episode.

### Reading

`adapters/reading.py` derives `reading_block` episodes.

Signal:
- A `google_calendar.completed` `scheduled_block` whose title matches
  `\b(read|reading|book:|article:|paper:)\b`.

Optional secondary signal (degrade gracefully when absent):
- `health.facts` rows with predicate `reading_session`.

Output: `reading_block` episodes with category `tasks`, source_name
`chronicler.reading_inferred`, source_ref deterministic, privacy
`normal`, precision `minute`.

## Aggregations map

`_CATEGORY_MAP` adds three rows:

```python
("google_health.measurements", "workout_episode"): "other",
("chronicler.focus_inferred",  "focus_block"):     "tasks",
("chronicler.reading_inferred","reading_block"):   "tasks",
```

(Sleep, music, gaming, travel, meal, home, calendar, conversations, tasks
mappings unchanged.)

## Frontend

The page rewrite uses primitives that already shipped under
`frontend/src/components/overview/` for the dashboard-overview-briefing
change: `DateEyebrow`, `BriefingStatus`, `Headline`, `Elaboration`,
`KpiStrip`, `AttentionList`. The chronicles page composes them with
chronicles-flavoured data.

New chronicles-only components:
- `RecentDaysIndex.tsx`: eyebrow-titled list of `recent_days`. Right
  column.
- `ChroniclesDrilldownPanel.tsx`: lazy host that mounts the existing
  Gantt/Map/Aggregations/Drawer triumvirate when the user opens it.

New hooks:
- `use-chronicles-briefing.ts` (TanStack Query, 30s stale, 5m cache).
- `use-chronicles-attention.ts`.
- `use-chronicles-kpi.ts`.

The `<Page archetype="editorial">` discriminant is added to the existing
`<Page>` component. Today the discriminant union is
`'overview' | 'list' | 'detail' | 'workspace' | 'editor'`. We extend it
with `'editorial'` and route it to a Display-headline heading block
(per the editorial archetype layout in
`about/lay-and-land/frontend.md`).

## Child implementation boundaries

The OpenSpec package owns the full contract and keeps implementation
boundaries explicit for the child beads:

- **Adapter boundary**: health, focus, and reading projection are separate
  deterministic adapter slices. They may add episode types and point-event
  sources, but they do not reshape lane taxonomy, add database schema, or
  invoke LLMs.
- **API boundary**: briefing, attention, and KPI endpoints are additive
  Chronicler API reads. They compose only from `chronicler.*` relations and
  from the existing day-close Tier-2 cache.
- **Frontend boundary**: `/chronicles` changes the landing archetype to
  editorial while preserving existing Gantt, map, aggregation, source-state,
  and drawer components inside the drilldown panel.
- **Integration boundary**: the final integration pass owns cross-slice
  type alignment, guardrail tests, frontend quality gates, and any needed
  documentation drift fixes.

## What is preserved

- All chronicler API routes that exist today.
- All chronicler adapters that exist today.
- Existing chronicles components retained as drilldown surfaces.
- Privacy contract (sensitive episodes hatched in Gantt, masked in
  drawer; restricted hidden server-side): drilldown obeys this.
- Manifesto invariants: deterministic projection, no per-event LLM,
  Tier-2 only on explicit day-close paths.

## What is rejected

- Lane taxonomy reshape. Owner explicitly excluded.
- Episode merging policy changes. Owner explicitly excluded.
- Per-event LLM calls. Manifesto invariant.
- New database tables. None needed.
- Removing existing endpoints. Drilldown depends on them.
