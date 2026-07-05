## Why

96% of all ingested volume (Home Assistant + OwnTracks + Spotify, ~964k
rows/100 days) is correctly `skip`-routed at the global ingestion-policy layer
— that routing decision is by design (telemetry doesn't need per-message LLM
triage) and is not itself a defect. But `skip` only means "don't forward to a
butler for LLM routing" — the raw payload still lands durably in
`connectors.filtered_events` (12-month retention) or a source-specific table.
Almost none of that durable substrate is ever read again: Chronicler's Home
Assistant adapter only persists and projects the `person.*` domain
(`presence_episode`); every other allow-listed HA domain (motion, door,
light/switch/climate state) is captured and then never touched except by the
narrow wellness-metric promoter. OwnTracks has one signal (raw-GPS movement)
and no place-clustering yet. And regardless of source, there is no persisted
daily/weekly rollup anywhere in the schema — `aggregations.py` computes
lane totals on read, per API call, and nothing ever materializes a "here's
what changed vs. normal" summary or flags an anomaly. The system records life
at high fidelity and distills almost none of it into knowledge: Chronicler
has had 3 ingestion-triggered sessions all-time and 83 sessions total.

This is the design + spec delta for the consumption-side fix, complementing
epic `bu-whhll` (Chronicler workday visibility), which is the supply-side
feeder-repair counterpart. Full grounding, code citations, and the detailed
per-source gap analysis live in
`docs/plans/2026-07-06-telemetry-distillation-design.md`.

## What Changes

- **New deterministic projection adapter** `home_assistant.sensor_activity`:
  reads `connectors.filtered_events` for non-`person` Home Assistant domains
  (motion, door/entry, light/switch usage) and projects `room_activity_episode`
  / `entry_event` rows at `layer=evidence`, promoted to `layer=activity` only
  when reconciliation finds a corroborating signal (an enabled routine window,
  an `occupation_block`, a Spotify session) — never claiming the `work`/
  `occupation` lane, to avoid re-opening the lane-conflation problem
  bu-whhll.14 is fixing.
- **New deterministic projection adapter** `owntracks.place_cluster`:
  radius/dwell-time clustering over `connectors.owntracks_points` into
  `place_episode` rows, labeled against owner-declared reference points
  (no geocoding, no LLM) — a GPS-only fallback for when Wi-Fi SSID reporting
  (bu-whhll.5) is unavailable or not yet landed, using an independent
  `source_name` so both can corroborate `occupation_inferred` side by side.
- **New infrastructure**: a nightly/hourly `chronicler.daily_rollups` +
  `daily_rollup_flags` materializer that aggregates the day's `activity`-layer
  episodes into per-lane second totals (reusing the exact same
  `lane_for_activity`/`union_seconds` counting rules the live
  `aggregate/by-category` endpoint already uses, so the two surfaces can never
  diverge) plus a small deterministic anomaly-flag rule set (`feeder_dark`,
  `sleep_missing`, `routine_break`, `lane_share_outlier`), each rule
  consulting `source_adapter_state` first so a known feeder outage is flagged
  as an outage, not fabricated as a behavioral anomaly.
- **Optional, additive, bounded LLM labeling pass**: exactly one LLM call per
  local day (same cost/shape profile as the existing `chronicler_day_close`
  job), invoked only after the deterministic rollup + flags are written, over
  the already-reduced rollup output — never per-event, per RFC 0014 §D5.
  Presentation polish only; the deterministic rollup/flags are correct and
  complete without it.
- **No new ingestion path, no new connector, no schema change to another
  butler.** Every new adapter reuses the existing `ProjectionAdapter` contract
  and scheduled-job/cron pattern; the rollup tables are new Chronicler-owned
  tables only.

## Capabilities

### New Capabilities

- `chronicler-telemetry-distillation`: the HA non-person sensor-activity
  adapter, the OwnTracks place-cluster adapter, the daily-rollup
  materializer + anomaly-flag rule set, and the bounded once-daily LLM
  labeling pass.

### Modified Capabilities

- `butler-chronicler`: adds a retention-window-awareness obligation for any
  adapter reading a TTL-bearing connector table (a genuinely new adapter risk
  class — every existing adapter reads either its own schema or a TTL-free
  connector table), and names `daily_rollups`/`daily_rollup_flags` alongside
  the existing storage-shape requirement.

## Impact

- **Backend** (`src/butlers/chronicler/adapters/`): two new adapter modules
  (`home_assistant_sensor_activity.py`, `owntracks_place_cluster.py`),
  following the existing `ProjectionAdapter` base class exactly.
- **Backend** (new `src/butlers/chronicler/rollups.py` or equivalent): the
  daily-rollup materializer, modeled on `routines.py`'s pure-function /
  async-orchestrator split (a pure aggregation function the unit tests
  exercise directly, plus a thin DB-reading orchestrator).
- **Backend** (`src/butlers/chronicler/aggregations.py`): no counting-rule
  change — the rollup must call the exact same `lane_for_activity`/
  `union_seconds` functions the live endpoint uses, not a reimplementation.
- **Backend** (`roster/chronicler/api/router.py` + `api/models.py`): new
  `GET /api/chronicler/rollups` (or equivalent) read surface, following the
  degraded-source envelope conventions where a `feeder_dark` flag is present.
- **Backend** (`roster/chronicler/butler.toml`): new scheduled-job entries for
  the two adapters and the rollup materializer.
- **Migration** (`roster/chronicler/migrations/`): `daily_rollups`,
  `daily_rollup_flags` tables; `source_adapter_state`-consulting retention-lag
  metric for the new HA adapter (illustrative shapes only — see design doc
  §3.3; actual migrations are implementation-bead work, not part of this
  change).
- **Frontend**: optional trend widget consuming the new rollup endpoint —
  scoped as a later implementation bead, not required for the design/spec
  delta itself.

## Sequencing

- The two projection adapters (HA sensor-activity, OwnTracks place-cluster)
  are independent of each other and can land in either order or in parallel.
- The daily-rollup materializer functionally depends on neither adapter (it
  aggregates whatever `activity`-layer episodes already exist), but is more
  useful once they land, so it sequences after in practice.
- The anomaly-flag rule set depends on the rollup materializer.
- The LLM labeling pass depends on the anomaly-flag rule set (it narrates
  flags) and is the last, fully optional link in the chain.
- Wiring `routine_break` into bu-whhll.12's gap-interview surface is a
  coordination point, not a hard dependency — implement against whichever of
  the two lands first.

## Out of Scope

- Any change to the ingestion-policy `skip`/`pass_through` routing rules
  themselves — this change accepts today's triage decisions as correct and
  works with what is already durably retained downstream of them.
- Repairing broken feeders (Google Health OAuth scopes, HA connector
  connectivity, OwnTracks client SSID configuration) — owned by `bu-whhll`
  Tier 0/1. This design's anomaly flags are expected to fire correctly
  against those known outages, not paper over them.
- Reverse-geocoding, external mapping APIs, or any LLM-based place
  identification — place labeling is deterministic distance-threshold matching
  against owner-declared reference points only.
- Proactive notifications or owner-facing messages of any kind — flags are a
  passive, queryable surface. Turning a flag into an actual owner-facing
  prompt is bu-whhll.12's explicitly-scoped surface, not this change's.
- Real-time/streaming projection — batch/scheduled cadence only, per the
  originating bead's explicit cost framing.
- Any schema change to a table owned by another butler.
