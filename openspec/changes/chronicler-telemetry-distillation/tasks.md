## 1. Owner sign-off (gate)

- [ ] 1.1 Confirm the scope split vs. `bu-whhll`: this change is the
  consumption-side distillation layer; feeder repair (Google Health OAuth,
  HA connectivity, OwnTracks client config) stays owned by `bu-whhll`.
- [ ] 1.2 Confirm the two new adapters' lane choices (HA sensor-activity
  never claims `work`/`occupation`; both new sources are independent
  corroborators, not replacements, for `occupation_inferred`).
- [ ] 1.3 Confirm the rollup/anomaly surface ships as passive/queryable only
  in this change (no new owner-facing notification), with `routine_break`
  as the coordination point for `bu-whhll.12`'s gap-interview surface.

## 2. HA non-person sensor-activity adapter (independent, no deps)

- [ ] 2.1 New adapter module reading `connectors.filtered_events` for
  non-`person` Home Assistant domains; rule-table classifier for
  motion → `room_activity_episode`, door/garage → `entry_event` point event.
- [ ] 2.2 Retention-lag monitoring: alert when the adapter's checkpoint
  watermark approaches the oldest retained `filtered_events` partition
  cutoff (new risk class — see design doc §5).
- [ ] 2.3 Reconciliation-gated promotion: raw episodes `layer=evidence`;
  promoted to `layer=activity, confidence=low` only when
  `reconciliation.py` finds a corroborator.
- [ ] 2.4 Tests: rule-table classification; retention-lag alert fires;
  promotion only occurs with a corroborator; new episodes never map to the
  `work`/`occupation` category in `aggregations.py`.

## 3. OwnTracks place-cluster adapter (independent, no deps)

- [x] 3.1 New adapter module: radius/dwell-time clustering over
  `connectors.owntracks_points` into `place_episode` rows. (bu-ac2pg —
  `owntracks.place_cluster` / `place_episode`)
- [x] 3.2 Deterministic place labeling against owner-declared reference
  points (home/work lat-lon); unlabeled recurring clusters surface as
  `place_unknown`. (bu-ac2pg — `OWNTRACKS_PLACE_REFERENCES` env var, no
  migration)
- [ ] 3.3 Coordinate `source_name`/`episode_type` with `bu-whhll.5` (Wi-Fi
  SSID presence adapter) so both can appear side by side in
  `occupation.py`'s corroborator list without collision. bu-ac2pg landed
  first with `owntracks.place_cluster` / `place_episode`; bu-whhll.5 (or
  whichever lands second) adds its own distinct pair plus the
  `occupation.py` corroborator wiring for both.
- [x] 3.4 Tests: cluster formation over synthetic point fixtures (radius/dwell
  edges, singleton points, teleport outliers, gaps, cross-batch carryover);
  stable labeling across days; no geocoding/external API calls. (bu-ac2pg —
  unit + real-Postgres integration coverage)

## 4. Daily rollup materializer (depends on §1)

- [ ] 4.1 Migration: `chronicler.daily_rollups` (per local_date + lane) and
  `chronicler.daily_rollup_flags` (per local_date + flag_type) tables.
- [ ] 4.2 Rollup job (pure aggregation function + thin async orchestrator,
  mirroring `routines.py`'s split) calling `aggregations.lane_for_activity`/
  `union_seconds` directly — no parallel counting logic.
- [ ] 4.3 Idempotent upsert on `(local_date, lane)` / `(local_date, flag_type)`
  so re-runs after late corrections simply recompute.
- [ ] 4.4 Tests: rollup output matches a same-window `aggregate/by-category`
  call bit-for-bit (regression guard against KPI-divergence, the
  bu-whhll.1-class bug).

## 5. Anomaly flag rules (depends on §4)

- [x] 5.1 `feeder_dark`: source inactive or checkpoint stale beyond 2x its
  cron interval. (bu-v76a7 — `flags.py::is_source_dark`)
- [x] 5.2 `sleep_missing`, `routine_break`, `lane_share_outlier`: each rule
  consults `source_adapter_state` first (classify-before-flagging) so a known
  outage never gets misreported as a behavioral anomaly. (bu-v76a7)
- [x] 5.3 Tests: each flag's positive and negative case; feeder-outage day
  produces `feeder_dark` only, not a fabricated behavioral flag. (bu-v76a7 —
  `tests/chronicler/test_flags.py` + real-Postgres
  `tests/integration/test_daily_rollup_flags_integration.py`)

## 6. Dashboard/API read surface (depends on §4, §5)

- [ ] 6.1 `GET /api/chronicler/rollups` (or equivalent) endpoint.
- [ ] 6.2 Degraded-source envelope: a day carrying `feeder_dark` renders as
  "data unavailable" for the affected lane, not a false all-clear zero.

## 7. Optional bounded LLM labeling pass (depends on §5)

- [ ] 7.1 One LLM call per local day, invoked after rollup + flags are
  written, input = reduced rollup/flag rows + top episode titles only.
- [ ] 7.2 Output written back to `daily_rollup_flags.detail`/
  `daily_rollups.narrative`; owner-toggleable; disabling it never affects
  rollup/flag correctness.

## 8. Coordination follow-up (not blocking this change)

- [ ] 8.1 Wire `routine_break` into `bu-whhll.12`'s gap-interview trigger
  once both are live, avoiding two independent unaccounted-time detectors.

---

## Out of Scope

- **Feeder repair.** Google Health OAuth re-consent, Home Assistant connector
  connectivity, OwnTracks client SSID/waypoint configuration remain owned by
  `bu-whhll` Tier 0/1.
- **Ingestion-policy rule changes.** The `skip`/`pass_through` triage
  decisions are accepted as correct; this change works with what they already
  retain downstream.
- **Notifications.** Flags stay passive/queryable; `bu-whhll.12` owns turning
  any of them into an owner-facing prompt.
- **Reverse-geocoding / external mapping APIs / LLM place identification.**
- **Real-time or streaming projection.**
