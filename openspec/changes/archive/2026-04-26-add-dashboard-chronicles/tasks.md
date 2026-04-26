# Tasks — Chronicles Dashboard Page

Tasks are tracked in beads under epic `bu-<TBD>` (created in Phase 3 of
the project-direction workflow). The epic decomposes into the streams
below; see the epic and its children for detailed acceptance criteria.

## Streams

### Backend — Chronicler API extensions
- [ ] `category_for()` deterministic mapping function + unit test +
      no-LLM guardrail
- [ ] `GET /api/chronicler/aggregate/by-category` handler + Pydantic
      response model + SQL on corrected views
- [ ] `GET /api/chronicler/aggregate/by-day` handler + Pydantic
      response model
- [ ] `GET /api/chronicler/source-state` handler + Pydantic response
      model (joins `source_adapter_state` and `projection_checkpoints`)
- [ ] Structured `400` error shapes for invalid time-range / timezone
- [ ] No-LLM guardrail test for new handler files
- [ ] No-cross-schema-read guardrail test for new handler files —
      static analysis: parse each handler module's source, extract SQL
      string literals, validate every relation reference (bare or
      schema-qualified) resolves to a known `chronicler.*` relation;
      relations resolved via `search_path` are accepted iff they
      resolve into the `chronicler` schema
- [ ] Backfill audit on existing `roster/chronicler/api/router.py`
      handlers using the same static-analysis test (existing handlers
      must pass; if any reference a non-`chronicler` relation, file a
      discovered-from bead for remediation)
- [ ] OTel spans `chronicler.aggregate.by_category`,
      `chronicler.aggregate.by_day`, `chronicler.source_state`
- [ ] Latency benchmark on synthetic 7-day fixtures (P95 < 200 ms)

### Backend — Day-close cache (durable)
- [ ] `chronicler.tier2_cache` migration (additive; new table only;
      no changes to `chronicler.episodes` / `point_events` /
      `overrides`)
- [ ] Cache writer wired into the existing `chronicler_day_close`
      scheduled prompt (writes prose, provenance refs, window, and
      `cache_built_at`)
- [ ] `GET /api/chronicler/aggregate/day-close` reader with staleness
      check across `episodes.tombstone_at`, `episodes.updated_at`,
      `point_events.tombstone_at`, `point_events.updated_at`,
      AND `overrides.created_at`
- [ ] `POST /api/chronicler/aggregate/day-close/refresh` re-invocation
      endpoint (re-uses the existing scheduled
      `chronicler_day_close` Tier-2 entry point; rate-limited
      1 per day per window; `429` with `retry_after_seconds` on
      breach) — NO new LLM path

### Frontend — Page shell
- [ ] Route `/chronicles` registered in `frontend/src/router.tsx`
- [ ] Sidebar entry under Dedicated Butlers; tooltip discriminator
      against `/timeline`
- [ ] Time-window picker with day / week presets
- [ ] `useChroniclesAggregates`, `useChroniclesEpisodes`,
      `useChroniclesSourceState`, `useChroniclesDayClose` hooks
- [ ] Auto-refresh adoption (30 s today, static older)
- [ ] Source-state badge strip with disabled-lane tooltips

### Frontend — Gantt swimlane
- [ ] `LANE_TAXONOMY` constant in
      `frontend/src/components/chronicles/lane-taxonomy.ts`
- [ ] Gantt rendering component (overlap-friendly; recharts custom or
      visx-derived)
- [ ] Lane mask render for `privacy_tier=sensitive` episodes
- [ ] Hover tooltip showing source provenance, precision, duration
- [ ] Click-to-drilldown drawer (single explicit Tier-2 path; no
      auto-trigger)

### Frontend — Map widget
- [ ] Add `maplibre-gl` (BSD-3) to `frontend/package.json`
- [ ] Map shell component with OpenStreetMap tile source
- [ ] Trail rendering from OwnTracks point events (when adapter lands;
      shell ships disabled until then)
- [ ] Playhead bound to scrubber; snaps to nearest point event
- [ ] Calendar `location` text-based pan (if recognizable)

### Frontend — Aggregations panel
- [ ] Pie chart (recharts) for time-by-category
- [ ] Stacked bar for by-day × category
- [ ] Streak callouts (longest contiguous category lane)
- [ ] Empty-state and error-state per `dashboard-shell` patterns

### Documentation
- [ ] `about/lay-and-land/components.md` §4a Chronicler row updated to
      list new aggregate, source-state, and day-close routes
- [ ] RFC 0014 "Open Questions" L253–262 closure note added
- [ ] `dashboard-shell` delta lands as part of this change
      (`specs/dashboard-shell/spec.md` modifies Sidebar Navigation and
      Full Route Map Requirements to register `/chronicles` under
      Dedicated Butlers with the Chronicles-vs-Timeline tooltip
      discriminator)

### Sibling unlocks (NOT children of this change; parent links recorded)
- [ ] OwnTracks projection adapter (sibling bead; unblocks Map content
      and Travel lane)
- [ ] Steam projection adapter (sibling bead; unblocks Gaming lane)
- [ ] Google Health connector + sleep projection adapter (sibling beads;
      unblock Sleep lane)
- [ ] Meals projection (sibling bead; Health butler; point-event
      treatment given no `end_at`)
- [ ] Home Assistant presence projection (sibling bead)
