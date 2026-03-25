## Why

The General butler's EOD briefing currently only covers calendar events for the next day. Specialist butlers (Health, Finance, Relationship, Travel, Education, Home) each hold domain-specific data that would make the briefing significantly more useful -- upcoming bills, medication adherence, birthdays, departures, learning streaks, device alerts. Today there is no mechanism for cross-butler data aggregation, forcing the owner to check each butler individually for a complete daily picture.

## What Changes

- Each specialist butler gains a `daily_briefing_contribution` scheduled job that queries its own domain tables deterministically (zero LLM cost) and writes structured JSON to its state store under `briefing/daily/<YYYY-MM-DD>`
- General gains a `collect_briefing_contributions` scheduled job that performs a read-only cross-schema query to aggregate all specialist contributions into `briefing/combined/<date>`
- General's existing `eod-tomorrow-prep` prompt is expanded to incorporate the aggregated specialist summaries alongside the calendar timeline
- A cross-schema SQL view or database role is introduced to enable General's aggregation job to read specialist state stores (read-only, infrastructure code only)

## Capabilities

### New Capabilities
- `cross-butler-briefing-contribution`: Per-butler deterministic job that extracts domain highlights and writes structured JSON to state store. Covers the contribution schema, scheduling, and per-domain extraction logic.
- `cross-butler-briefing-aggregation`: General butler's aggregation job that reads contributions cross-schema, merges them, and writes a combined briefing payload. Covers the cross-schema read mechanism, aggregation logic, and combined output format.

### Modified Capabilities
- `butler-general`: The EOD prompt is updated to consume `briefing/combined/<date>` state and render a multi-domain briefing message (calendar + specialist highlights).
- `core-scheduler`: No spec-level changes -- existing `dispatch_mode="job"` and TOML schedule sync handle the new jobs without modification.

## Impact

- **Database:** Controlled cross-schema read access from `general` schema to `health`, `finance`, `relationship`, `travel`, `education`, `home` schemas' `state` tables. Implemented as a SQL view or restricted database role.
- **Butler configs:** 7 new `[[butler.schedule]]` entries across `butler.toml` files (one per specialist + one for General's aggregation).
- **Code:** ~6 new job files (one per specialist butler, ~50 lines each) + 1 aggregation job (~40 lines) + prompt update in General's skill/schedule.
- **Runtime cost:** 7 additional deterministic Python jobs per day (negligible). LLM session count remains 1/day (same as today).
- **No breaking changes.** The existing EOD briefing continues to work if specialist contributions are absent (graceful degradation).
