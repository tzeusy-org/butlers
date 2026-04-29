# Gen-1 Reconciliation: Chronicles Signal-vs-Noise (bu-zm1na)

**Bead:** bu-3zagb  
**Epic:** bu-zm1na — Chronicles dashboard: signal-vs-noise rework  
**Date:** 2026-04-30  
**Author:** beads-worker (bu-3zagb)

---

## Epic Success Criteria (from bu-zm1na)

1. `/butlers-dev/chronicles` Work lane (or renamed successor) contains zero episodes whose `trigger_source` is in `{tick, qa, healing}` or matches `schedule:*`.
2. Owntracks projection runs cleanly: `chronicler.projection_checkpoints.last_error WHERE source_name='owntracks.points'` is NULL after the fix lands.
3. Each life lane (Sleep, Meal, Music, Gaming, Travel, Home, Conversations, Tasks, Calendar) shows non-zero episodes within 7 days OR the lane shows the empty-state affordance with the upstream bead linked.
4. A documented butler-ops view exists for engineers to audit scheduler/QA/healing telemetry, separate from the chronicles dashboard.
5. After all child beads close, the user judges the chronicles dashboard usable as a "what am I spending my day on" tool (subjective, recorded by epic owner).
6. The terminal reconciliation child bead (gen-1) has been created, executed, and either closed clean or escalated to gen-2/gen-3.

---

## Coverage Matrix

### Criterion 1 — Work lane noise removed

**Status: COVERED**

| Bead | PR | Commit | Evidence |
|------|----|--------|----------|
| bu-x096m | #1239 (`cc607c71`) | merged 2026-04-29T13:52Z | Added `EXCLUDED_TRIGGER_SOURCES = frozenset({'tick', 'qa', 'healing'})` and `EXCLUDED_TRIGGER_SOURCE_PREFIX = 'schedule:'` constants to `src/butlers/chronicler/adapters/sessions.py`. SQL-level WHERE clause applied in both `since=None` and `since-set` branches of `_fetch_sessions`. |
| bu-noocq | #1251 (`e1ba5363`) | merged 2026-04-29T14:56Z | Added `scripts/backfill_tombstone_heartbeat_episodes.py` to tombstone ~5000+ pre-existing noise episodes idempotently. Sets `tombstone_at = now()` on matching rows; `v_episodes_corrected` view already hides tombstoned rows. 16 unit tests for idempotency and count-by-category. |
| bu-jomz2 | #1246 (`30e758db`) | merged 2026-04-29T14:35Z | Renamed "Work" lane to "Conversations" (trigger_source='route') and "Tasks" (all others). `category_for()` in `aggregations.py` dispatches on `trigger_source`. Frontend `LANE_TAXONOMY` updated to replace `work` with `conversations` + `tasks`. No backfill bead required — `trigger_source` already stored in `episodes.payload` JSONB; `category` is computed at query time, never stored. |

**Gap note:** The bu-x096m design notes explicitly deferred `deadline:*` trigger_source classification ("mixed user-set vs butler-set, needs a marker"). This is not documented in `roster/chronicler/AGENTS.md` or `sessions.py` as of current HEAD. See Discovered-Follow-Ups.

### Criterion 2 — Owntracks projection error cleared

**Status: COVERED**

| Bead | PR | Commit | Evidence |
|------|----|--------|----------|
| bu-cenng | #1238 (`0b2c6364`) | merged 2026-04-29T13:44Z | Fixed inverted `end_at < start_at` episodes caused by device clock skew. Two-pronged fix: (1) buffer re-sorted by `recorded_at` ascending before episode construction; (2) defensive guard swaps bounds if `end_at < effective_start_at`. Unit tests added in `tests/chronicler/test_owntracks_adapter.py` for out-of-order points and two-endpoint clock skew. |

**Runtime verification gap:** The `projection_checkpoints.last_error` field being NULL after the fix requires the dev stack to be running. The dev stack was NOT running at reconciliation time (connection refused on port 8000). Runtime verification of owntracks last_error clearance must be confirmed by the epic owner post-deploy.

### Criterion 3 — All life lanes non-zero or showing empty-state affordance

**Status: PARTIALLY COVERED** (code-level: fully covered; runtime-level: unverifiable without dev stack)

Each lane addressed:

#### Travel (owntracks)
- bu-cenng / PR #1238: Fix removes the constraint violation that blocked all owntracks projections since 2026-04-28. Code-level coverage confirmed.
- Runtime: dev stack not available; lane growth past 2026-04-27 requires post-deploy verification.

#### Music (Spotify)
- bu-r742f / PR #1240 (`0d03804e`): Wired existing `SpotifySessionAdapter` into the scheduler. Added `run_project_spotify()` job handler, registered `_run_chronicler_project_spotify_job`, and added `[[butler.schedule]]` block `chronicler_project_spotify` at `*/30` cadence to `roster/chronicler/butler.toml`. Integration test seeds spotify rows and asserts `GET /api/chronicler/episodes?source_name=spotify.session_summary` returns episodes with `category=music`.
- 13 source rows already existed in `connectors.spotify_listening_sessions` pre-fix.

#### Home (Home Assistant)
- bu-ykm2a / PR #1242 (`8794a75d`): Investigation found Option A — adapter (`HomeAssistantHistoryAdapter`) was correct and taxonomy already matched. Root cause was that the connector table (`connectors.home_assistant_history`) does not exist in dev, causing adapter to gracefully skip every tick. PR adds a taxonomy-contract test pinning `source_name='home_assistant.history'`, `episode_type='presence_episode'` → `category='home'`.
- **Important gap:** The Home lane will remain empty if `connectors.home_assistant_history` is not populated by the connector. This is expected degradation behavior per PR #1242. No follow-up bead was filed to ensure the connector is actually writing rows. See Discovered-Follow-Ups.

#### Sleep (Google Health)
- bu-bx5kl / PR #1269 (`e937579d`): Two bugs found and fixed: (1) the policy-bypass path in `pipeline.py` was not passing `input.context` to the Health butler's LLM, preventing `wellness_ingest_envelope` from ever being called; (2) `translate_wellness_envelope` extracted sleep metadata but never passed it to `memory_store_fact`. Fix: bypass path now fetches `raw_payload` and embeds as `input.context`; `memory_store_fact` now accepts `metadata` parameter; full sleep metadata (`end_time`, `session_id`, `minutes_asleep`, `minutes_awake`) is now stored.
- Note: The chronicler adapter reads `health.facts` (not `health.measurements` as the bead title implied). `SOURCE_NAME = "google_health.measurements"` is the logical name used for the projection checkpoint; physical reads are from `health.facts WHERE predicate = 'sleep_session'`.
- Runtime: requires at least one wellness envelope to be processed post-deploy to confirm rows flow.

#### Meal
- bu-iq762 / PR #1270 (`3b7d9914`): Root cause was that `meal_log()` only wrote to `health.facts` (memory subsystem), leaving `health.meals` empty. Fix: added `_write_to_health_meals()` dual-write in `roster/health/tools/diet.py` with `ON CONFLICT (id) DO NOTHING` idempotency. `MANIFESTO.md` updated to document the write path.
- Runtime: requires a real `meal_log()` call (user texting meal to health butler) post-deploy.

#### Gaming (Steam)
- bu-d0acy / PR #1267 (`38f0ae3b`): Root cause was that `c7950d38` skipped first-poll baseline writes but left a bug for new games appearing on subsequent polls — used a falsy `if prev_entry` check, treating `{}` as missing and writing full 14-day cumulative as a delta. Fix: extracted `_compute_play_delta()` helper, returns `None` (skip) when `prev_playtime is None` for new games. 6 regression tests added.
- At investigation time, 2 pre-existing rows from before `c7950d38` were in `connectors.steam_play_history`. The chronicler adapter had already projected those as Gaming episodes. Runtime verification of new Gaming episodes depends on actual gaming sessions occurring post-deploy.

#### Conversations and Tasks (core.sessions)
- bu-x096m / PR #1239 + bu-jomz2 / PR #1246: After filtering noise, surviving sessions now correctly categorized as Conversations (trigger_source='route') or Tasks (other). Code-level coverage confirmed.

#### Calendar
- Not a target of this epic (Calendar was already working per the original problem statement: 537 episodes in 7 days). No dedicated bead filed; coverage is pre-existing.

#### Empty-state affordance (all lanes)
- bu-p4vd3 / PR #1255 (`d0e30e98`): `GanttSwimlaneInner` now renders all 10 `LANE_TAXONOMY` categories in the swimlane grid, even with 0 episodes. Empty lanes show a muted affordance with "No data this period" SVG text. `AggregatePieChart` renders `AllCategoriesLegend` listing all 10 categories. `SourceStateBadgeStrip` wraps `last_error` in a hover tooltip when present. New `GET /api/chronicler/projection-health` endpoint exposes per-source `last_error`, `last_run_at`, `rows_projected`, `watermark`. Component tests cover populated, empty, and error-badge states.

### Criterion 4 — Documented butler-ops view

**Status: COVERED**

| Bead | PR | Commit | Evidence |
|------|----|--------|----------|
| bu-4zu95 | #1254 (`a4e21ba5`) | merged 2026-04-29T15:12Z | Selected Option 2: `GET /api/chronicler/ops/sessions` — reads operational sessions (tick, qa, healing, schedule:*) from per-butler raw sessions tables via `db.fan_out()`. Supports `trigger_source`, `since`, `until`, `limit` params. Decision and rationale documented in `roster/chronicler/AGENTS.md` under "Ops sessions escape hatch". Integration test asserts ops data visible only via ops endpoint, never via `/api/chronicler/episodes` (cross-surface contamination test). |

### Criterion 5 — Subjective dashboard usability judgment

**Status: PENDING — requires runtime verification**

This criterion is inherently subjective and requires the epic owner to load `/butlers-dev/chronicles` after all child beads deploy and judge usability. Cannot be verified at reconciliation time because:
1. Dev stack is not running (port 8000 connection refused).
2. Several lanes (Sleep, Meal, Gaming, Home) require real data to flow through connectors/tools post-deploy before the user can assess the full picture.

### Criterion 6 — Terminal reconciliation bead executed

**Status: IN PROGRESS** (this bead is that execution)

bu-3zagb is the gen-1 reconciliation bead. Execution in progress; will be closed by coordinator after this report.

---

## Gaps and Deferred Items

### Gap 1: `deadline:*` trigger_source classification is undocumented

The original bu-x096m design notes say: "Defer `deadline:%` to a follow-up — mixed user-set vs butler-set, needs a marker." This deferral was never documented in `roster/chronicler/AGENTS.md` or `sessions.py`. Today, `deadline:*` sessions would be projected into the Conversations/Tasks lanes (not excluded). Whether this is correct depends on whether `deadline:*` represents user-set reminders (should appear as Tasks) or butler-internal scheduling (should be excluded like `schedule:*`). The design intent is ambiguous and requires a decision.

### Gap 2: Home lane depends on connector table that may not exist

PR #1242 confirmed the Home lane adapter is correctly wired, but the adapter silently skips every tick when `connectors.home_assistant_history` doesn't exist. Whether the Home Assistant connector is actually populating that table in production is unverified. If the connector is not writing rows, the Home lane remains permanently empty and criterion 3 is not met for Home.

### Gap 3: Runtime verification of Sleep/Meal/Gaming/Home lanes

The Sleep, Meal, Gaming, and Home lanes all require real connector data or user actions post-deploy to show non-zero episodes. The code fixes are in place but no runtime verification of actual episode creation was possible at reconciliation time (dev stack not running). If any of these lanes remain empty after 7 days in production, criterion 3 is not fully met.

### Gap 4: Subjective user acceptance (criterion 5) uncaptured

The epic owner (uniquosity@gmail.com) needs to load the dashboard post-deploy and record their subjective acceptance verdict in the epic. This cannot be done programmatically.

---

## Summary per Epic Criterion

| # | Criterion | Status | Primary PR(s) | Notes |
|---|-----------|--------|---------------|-------|
| 1 | Work lane noise removed | COVERED | #1239, #1251, #1246 | `deadline:*` classification deferred, undocumented |
| 2 | Owntracks last_error NULL | COVERED (code) | #1238 | Runtime verify needed (dev stack offline) |
| 3 | All lanes non-zero or empty-state | PARTIALLY COVERED | #1238, #1240, #1242, #1246, #1267, #1269, #1270, #1255 | Sleep/Meal/Gaming/Home need live data post-deploy; Home connector table existence unverified |
| 4 | Butler-ops view documented | COVERED | #1254 | Option 2: `/api/chronicler/ops/sessions` endpoint |
| 5 | User judges dashboard usable | PENDING | — | Subjective; requires owner verification post-deploy |
| 6 | Reconciliation bead executed | IN PROGRESS | — | This bead (bu-3zagb) |

---

## Dashboard Verification Status

**Dev stack: NOT RUNNING** at reconciliation time (port 8000 connection refused). All verification is code-review-only.

Verification attempts:
- `curl --connect-timeout 5 http://localhost:8000/api/chronicler/projection-health` → exit code 7 (connection refused)
- `curl --connect-timeout 5 http://localhost:3000` → exit code 7 (connection refused)

Runtime verification deferred to post-deploy by epic owner.

---

## Gen-2 Reconciliation Assessment

Gen-2 is needed for the following open items:
1. Confirm `deadline:*` classification decision and document it.
2. Verify Home lane actually shows data (connector table exists and is populated).
3. Confirm Sleep/Meal/Gaming lanes show episodes after 7 days in production.
4. Capture epic owner's subjective verdict (criterion 5).

A gen-2 reconciliation bead should depend on:
- A `deadline:*` classification decision bead (low priority, unblocks criterion 1 completeness)
- Criterion 3 runtime verification (requires 7 days of production data post-deploy)
- Epic owner review session

**Recommended gen-2 timing:** 7 days after this PR merges, to allow live data to flow.
