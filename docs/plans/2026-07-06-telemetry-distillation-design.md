# Telemetry Distillation Layer — projecting raw HA/OwnTracks/Spotify telemetry into Chronicler episodes & routines

**Date:** 2026-07-06
**Bead:** bu-p2d0f (design deliverable, gated behind bu-shk4p — released 2026-07-05)
**Complements:** bu-whhll (Chronicler workday visibility — the *supply-side* feeder-repair epic).
This bead is the *consumption-side* counterpart: given that raw telemetry
arrives, how much of it turns into something Chronicler can reason about?

## 0. tl;dr

- 96% of ingested volume (HA + OwnTracks + Spotify, ~964k rows/100 days) is
  correctly `skip`-routed at the ingestion-policy layer — that is by design,
  not a bug, and is *not* the gap (§1.3). The gap is downstream: only a thin
  slice of what is durably retained ever gets projected into
  `chronicler.episodes`/`point_events`, and **nothing rolls episodes up into
  daily/weekly aggregates or flags anomalies** — that materialization layer
  does not exist at all today (§2.3).
- Per-source finding: Spotify is *already well distilled* (no material gap).
  OwnTracks has one signal (raw-GPS movement) and is about to gain a second
  (Wi-Fi SSID presence, bu-whhll.5, in flight) — this design adds a third,
  complementary GPS-clustering **place episode** for when SSID is unavailable.
  Home Assistant is the real gap: **only the `person.*` domain is durably
  retained** (`connectors.home_assistant_history`); every other allow-listed
  domain (`binary_sensor`, `sensor`, `light`, `switch`, `climate`, `lock`,
  `cover`) lands in `connectors.filtered_events` (12-month retention, JSONB
  payload intact) and is **never read by anything except the wellness
  promoter**, which only cares about body-metric device classes.
- Proposal: a small number of new **deterministic** projection adapters
  (reusing the existing `ProjectionAdapter` contract — no new adapter
  infrastructure) that mine `connectors.filtered_events` for non-person HA
  domains into activity-shaped episodes, plus one genuinely new piece of
  infrastructure — a nightly **daily-rollup materializer** — that aggregates
  the day's activity-layer episodes into a persisted per-lane summary and a
  small deterministic anomaly-flag set. LLM involvement is optional, additive,
  and bounded to **one call per day** that labels/narrates the rollup's
  notable rows — never per-event, per RFC 0014 §D5.
- This composes with, and does not duplicate, bu-whhll's in-flight Tier 1/2
  work (SSID presence adapter bu-whhll.5, routine miner bu-whhll.9 — shipped,
  occupation adapter bu-whhll.10 — shipped). The rollup materializer is a
  natural downstream consumer of routines/occupation_block, not a competing
  model.

---

## 1. Grounding: what's actually retained, and what already gets distilled

This section is a code- and data-grounded inventory (2026-07-06), not a
re-statement of the 2026-07-05 deep dive (`docs/archive/plans/2026-07-05-chronicler-time-inference-deep-dive.md`),
which this design builds directly on top of (its Tier 1/2 items are the
supply side; read it first for the workday-specific narrative).

### 1.1 The ingestion pipeline stages, precisely

Three distinct layers exist between "HA fires a state change" and "a byte is
durably retained somewhere Chronicler could read it," and it matters which
layer a given domain/source clears:

1. **Connector-local pre-filter** (per `openspec/specs/ingestion-policy/spec.md`
   `connector:<type>:<identity>`-scoped rules, plus HA's own three-stage
   pipeline in `home_assistant_pipeline.py`: domain allowlist →
   significance-delta filter → discretion). This is where the bulk of the
   800k+ "pre-filtered" volume never becomes an envelope at all (e.g. a
   `sensor.cpu_temp` wobbling by 0.1°C every 10s). **Nothing to distill here —
   it's genuinely below the noise floor.**
2. **Global ingestion-policy evaluation** (`switchboard.ingestion_rules`,
   scope=`global`) — this is where the `skip` action lives that the bu-shk4p
   gate's 165,259/171,619 figure refers to. Per the ingestion-policy spec,
   `public.ingestion_events` (the audit ledger, no payload) and
   `connectors.filtered_events` (the raw JSONB payload, monthly-partitioned,
   12-month default retention — `retention.py::_FILTERED_EVENTS_DEFAULT_KEEP_MONTHS`)
   are written **before** this evaluation runs ("post-ingest/pre-LLM").
   `skip` means *"do not forward to a butler for LLM triage/routing"* — it
   does **not** mean the raw data disappears. **This is the crux: `skip` !=
   "discarded." The raw payload for every non-pre-filtered HA/OwnTracks/Spotify
   event is sitting in `connectors.filtered_events` for up to 12 months,
   completely unread by anything except three narrow consumers (below).**
3. **Durable, long-retention, source-specific tables** — written directly by
   each connector, independent of the global policy's routing decision:
   `connectors.home_assistant_history` (person domain only — see §1.2),
   `connectors.owntracks_points` (`core_081`), `connectors.spotify_*`
   (`core_079`). These have no TTL and are what today's Chronicler adapters
   actually read.

### 1.2 Home Assistant: the durable-retention domain gap

`src/butlers/connectors/home_assistant.py::_DEFAULT_DOMAIN_ALLOWLIST` passes
ten domains through the connector-local filter: `light, switch, sensor,
climate, lock, cover, binary_sensor, automation, script, person`. All ten,
once past the significance/discretion filters, get submitted to the
Switchboard `ingest` tool and land in `connectors.filtered_events` (12-month
retention). But `_dispatch()`'s final persistence step is domain-gated:

```python
# Persist person.* state-change events to the history evidence table
if domain == "person" and db_pool is not None:
    await persist_ha_history(...)
```

Only `person.*` writes to `connectors.home_assistant_history` — the table
Chronicler's `HomeAssistantHistoryAdapter` (`adapters/home_assistant.py`)
reads to project `presence_episode`s. (Live-data caveat, 2026-07-06: this
table currently holds zero rows — bu-whhll.3's allowlist/persistence fix is
deployed, but no live `person.*` state transition has occurred yet to
exercise it; tracked by the still-open bu-bm2pm. This does not affect this
design's proposals, which target the other nine domains below and the
rollup layer, neither of which reads `home_assistant_history`.) The other
nine domains — motion,
door/window contact, light/switch/climate/lock/cover state, script and
automation firings — are captured, pass every filter, and then sit in
`filtered_events` for up to a year, read by exactly one consumer:
`home_assistant_wellness.py`'s deterministic classifier, which only promotes
readings that look like body metrics (smart-scale weight, BP cuff, SpO2 —
matched on `device_class`/`unit_of_measurement`) onto the `health.facts`
wellness channel. **Everything else — every motion sensor firing, every door
open/close, every light/switch/climate transition — is retained and never
touched again.** This is the single largest concrete distillation gap this
design addresses (§3.1).

### 1.3 OwnTracks and Spotify: narrower, more nuanced gaps

- **OwnTracks** (`adapters/owntracks.py`, `SOURCE_NAME = "owntracks.points"`):
  today emits exactly one episode type, `movement_episode`, from a
  speed/displacement heuristic over raw points. It does **not** cluster
  stationary point-runs into distinct **places** (home / work / elsewhere) —
  that's precisely what bu-whhll.5 (SSID presence adapter, in flight, not yet
  landed per `occupation.py`'s docstring) is scoped to solve, but only for
  phones reporting Wi-Fi SSID. The 2026-07-02 case-study day had `conn: "w"`
  but no SSID field — i.e. **SSID-based place detection has a real, expected
  failure mode** (client not configured, or connected to Wi-Fi without
  reporting SSID/BSSID) that a GPS-cluster fallback would cover. This design
  proposes that fallback (§3.2) as a complement, not a replacement, for
  bu-whhll.5.
- **Spotify** (`adapters/spotify.py`, `listening_episode`): already
  well-distilled — every session becomes an episode, categorized `music`,
  corroborating occupation inference. **No material gap here**; it is
  reasonable evidence *of* the workday (as the deep-dive's case study shows),
  and is already wired as a corroborator into `occupation.py`. This design
  does not propose new Spotify projection.

### 1.4 The rollup/anomaly gap — true for every source

Regardless of source, there is **no persisted daily or weekly aggregate**
anywhere in the schema today. `aggregations.py` (`category_for`,
`lane_for_activity`, `union_seconds`) is described in its own docstring as
"pure, deterministic functions used by aggregate endpoints" — i.e. computed
**on read**, per API request, never materialized. `routines.py` re-scans up
to 6 weeks of raw episodes on every mining run. There is no table that says
"on 2026-07-02, sleep=0m, work=0m (pre-fix), music=7.2h, and this was
abnormal because trailing-14-day median sleep is 6.1h." This is the second
concrete gap this design addresses (§3.3) and is what actually turns
episodes into "knowledge" per the bead's framing — a batch rollup is the
natural place for a bounded, once-a-day LLM labeling pass, and the natural
place for anomaly detection (missing sleep, a feeder gone dark, a routine
broken) that today requires a human staring at `/chronicles`.

### 1.5 What this design deliberately does *not* duplicate

- Routine mining (`chronicler.routines`, `routines.py`, bu-whhll.9) — shipped.
  The rollup materializer (§3.3) is a downstream *consumer* of routines/
  `occupation_block`, not a re-implementation.
- Occupation inference (`chronicler.occupation_inferred`, bu-whhll.10) —
  shipped. Its corroborator/contradictor pattern is the template §3.1's HA
  sensor-activity adapter and §3.4's anomaly rules reuse.
- Wi-Fi SSID presence (bu-whhll.5) — in flight, not yet landed. §3.2's GPS
  cluster adapter is scoped as an independent, complementary source
  (different `source_name`/`episode_type`), so it composes cleanly whenever
  bu-whhll.5 lands, exactly like `occupation.py`'s docstring anticipates for
  its own corroborator list.
- Work-lane semantics split (bu-whhll.14, "owner occupation vs butler ops") —
  the new HA sensor-activity episodes categorize into `home`/`rest`/a new
  `ambient` category, deliberately **not** into `work`/`occupation`, so they
  cannot re-introduce the lane-conflation problem bu-whhll.14 is fixing.

---

## 2. Design principles (doctrine compliance)

Restating and applying RFC 0014 and the existing `butler-chronicler` spec's
already-binding constraints, because every piece of this design must satisfy
them:

1. **No per-event LLM** (RFC 0014 §D5, `ProjectionAdapter._llm_probe`
   guardrail). Every new adapter is a pure deterministic aggregator, same
   contract as every existing adapter. The **only** LLM call in this design
   (§3.5) is one bounded call per local day, over the day's already-reduced
   rollup row — the same shape as the existing `chronicler_day_close` job's
   one-call-per-day pattern, not a new precedent.
2. **Retrospective-only, no ingestion ownership** (`butler-chronicler/spec.md`
   "Retrospective-Only Scope"). New adapters read `connectors.filtered_events`
   and `connectors.owntracks_points` the same way `HomeAssistantHistoryAdapter`
   already reads `connectors.home_assistant_history` — asynchronous, scheduled,
   read-only, no new ingestion path, no connector ownership change.
3. **Evidence, not fabrication** — every new episode carries `source_name`,
   `source_ref`, `precision`, `confidence`, `evidence_refs`, exactly like the
   IEA layering model (`chronicler-intent-evidence-activity`) already
   mandates. Ambient HA sensor activity is inherently weak signal —
   `confidence=low` by default, same posture as `occupation_inferred`.
4. **Reconciliation, not a parallel merge model** — contradictor/corroborator
   evaluation reuses `reconciliation.py`'s existing seam (per the deep-dive's
   §7 finding that `occupation_inferred`'s contradictor logic should "reuse
   this seam rather than invent a parallel one"); the anomaly-flag rules
   (§3.4) do the same.
5. **Classify before flagging** (per the fleet-wide degraded-source
   convention in `CLAUDE.md`/`docs/CLAUDE.md` API conventions, applied here to
   anomaly detection rather than a fan-out endpoint): a day with zero HA
   presence rows because the HA connector is *known* to be down
   (`source_adapter_state.active = false` / stale checkpoint) is a **feeder
   outage**, not a **behavioral anomaly** ("owner never came home"). The
   rollup's anomaly rules must consult `source_adapter_state` before emitting
   a behavioral flag, exactly as the classify-before-flagging convention
   requires distinguishing "legitimately absent" from "genuinely failed."
6. **Retention-window awareness (new constraint this design introduces).**
   Unlike every existing adapter, which reads chronicler's own schema or a
   TTL-free connector table, the HA sensor-activity adapter (§3.1) reads a
   table with a **12-month rolling TTL**
   (`retention.py::prune_filtered_events_partitions`). 12 months is ample
   headroom for a daily/hourly-cadence job, but this is a genuinely new
   category of adapter risk (a paused/broken adapter silently loses source
   data permanently, rather than merely falling behind) that did not exist
   for any prior adapter. §3.1 and §5 (risks) call this out explicitly with
   a lag-monitoring recommendation.

---

## 3. Proposed architecture

### 3.1 New adapter: HA non-person sensor activity (`home_assistant.sensor_activity`)

**Reads:** `connectors.filtered_events` filtered to `source_channel =
'home_assistant'` (or equivalent envelope field — confirm exact column name
against the `ingestion-event-registry` spec at implementation time) and
`domain != 'person'`, windowed by `recorded_at`/`occurred_at` since the last
checkpoint (same tuple-cursor convention as every other adapter:
`WHERE (ts, id) > ($1, $2)`).

**Projects:** a small, deterministic, **rule-table-driven** classifier —
structurally identical to `home_assistant_wellness.py`'s `WellnessRule`
approach (metadata-driven, vendor-agnostic, matched on `device_class`/
`unit_of_measurement`/entity-id token), but targeting *ambient/occupancy*
signatures instead of body metrics:

| device_class / domain | rolled-up episode_type | category (aggregations.py) |
|---|---|---|
| `binary_sensor` + `device_class=motion` | `room_activity_episode` (contiguous `on` runs, gap-tolerant like OwnTracks' movement clustering) | new `ambient` → `rest` lane (not `work`) |
| `binary_sensor` + `device_class in {door, garage_door, opening}` | `entry_event` (point event, not episode — instantaneous) | n/a (point event) |
| `light`/`switch` state changes clustered by room-prefix entity-id convention (owner-configurable via `HA_ROOM_PREFIX_MAP`, mirroring `HA_WELLNESS_RULES_EXTRA`'s extensibility pattern) | `device_usage_episode` | new `ambient` → `rest` lane |
| everything else allow-listed but unclassified | not projected (stays evidence-layer only, i.e. exactly today's status quo) | n/a |

Deliberately **excluded from v1**: `climate`/`lock`/`cover`/`script`/
`automation` — lower signal-to-noise for occupancy purposes, revisit only if
a concrete use case emerges. This keeps the adapter's rule table small and
its false-positive surface bounded, matching the wellness classifier's own
"conservative by default" posture.

**Layer/confidence:** `layer=evidence` for raw per-sensor episodes (they are
not, by themselves, strong enough to count as lived time — a single motion
sensor firing doesn't mean much) — **except** when reconciled with an
existing corroborator (an enabled routine window, an `occupation_block`, a
Spotify session), at which point reconciliation promotes the aggregate to
`layer=activity, confidence=low`, reusing `reconciliation.py`'s merge seam
rather than inventing a second promotion path. This mirrors the "activity
only counts when corroborated" IEA doctrine exactly, and is what stops
"the cat walked past the motion sensor at 3am" from becoming a lived-time
episode.

**Why this doesn't just re-derive presence_episode:** HA presence
(`person.*`) is a phone-app-derived home/away boolean; room-level ambient
sensors are a materially different, complementary signal (which room, when,
device-level usage) that composes with SSID/GPS place clustering (§3.2) to
eventually answer "which room was the owner in" rather than just "were they
home" — directly useful for the day-ribbon / room-level narrative the
IEA epic's evidence-chain UI is building toward.

### 3.2 New adapter: OwnTracks GPS place clustering (`owntracks.place_cluster`)

**Reads:** `connectors.owntracks_points` (no TTL concern — durable table,
existing adapter already reads it).

**Projects:** a deterministic single-pass clustering (radius + dwell-time
threshold — e.g. points within ~150m for ≥20 continuous minutes form a
cluster; tunables mirror `routines.py`'s `MIN_SUPPORT_*` constant style),
emitting `place_episode` rows. Cluster *centroids* that recur across many
days at a stable lat/lon are labeled via simple reverse lookup against two
owner-declared reference points (home lat/lon already resolvable from HA
presence correlation or an owner-declared `chronicler.routines`-style
config row; "work" from repeated weekday-only clusters) — deterministic
distance-threshold labeling, not geocoding/reverse-geocoding (no external
API call, no LLM). Unlabeled recurring clusters surface as `place_unknown`
— visible, not fabricated.

**Relationship to bu-whhll.5:** independent `source_name`
(`owntracks.place_cluster` vs bu-whhll.5's SSID-adapter name, TBD at its
implementation). Both can independently corroborate `occupation_inferred`
(§ occupation.py already anticipates adding new corroborator pairs to
`_CORROBORATOR_EPISODE_SOURCES` with zero other changes) and both can
disagree — reconciliation's existing overlap/corroboration logic handles
that the same way it handles any two independent activity sources.

### 3.3 New infrastructure: daily rollup materializer (`chronicler.daily_rollups`)

This is the one genuinely new piece of infrastructure (everything else above
is "more adapters of a kind that already exists"). A scheduled job
(`chronicler_rollup_daily`, cron once/day shortly after local midnight in
the owner's timezone, e.g. `05 00 * * *` UTC-adjusted the same way
`routines.py`/`occupation.py` resolve `Asia/Singapore` — actually the cleanest
implementation runs hourly and rolls up any local calendar day whose window
has fully elapsed, so a single missed run doesn't lose a day, mirroring
`occupation.py`'s "only project fully-elapsed windows" pattern) that:

1. Reads all `layer=activity` episodes + point_events for the closed local
   day (reusing `lane_for_activity` + `union_seconds`, i.e. **the exact same
   counting rules as `aggregate/by-category`** — this rollup must never
   diverge from what the live endpoint already computes, or the dashboard and
   the rollup tell two different stories, re-opening the KPI-intent-leak class
   of bug bu-whhll.1 just fixed).
2. Writes one row per local day per lane to `chronicler.daily_rollups`
   (illustrative shape, not a migration):

   ```sql
   CREATE TABLE daily_rollups (
       id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
       local_date DATE NOT NULL,
       timezone TEXT NOT NULL DEFAULT 'Asia/Singapore',
       lane TEXT NOT NULL,                    -- sleep|exercise|work|play|social|travel|eat|rest
       seconds INTEGER NOT NULL CHECK (seconds >= 0),
       episode_count INTEGER NOT NULL DEFAULT 0,
       distinct_place_count INTEGER,          -- from place_episode/presence_episode, nullable
       computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       UNIQUE (local_date, lane)
   );
   CREATE TABLE daily_rollup_flags (
       id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
       local_date DATE NOT NULL,
       flag_type TEXT NOT NULL,               -- see §3.4
       severity TEXT NOT NULL DEFAULT 'info',
       detail JSONB NOT NULL DEFAULT '{}'::jsonb,
       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       UNIQUE (local_date, flag_type)
   );
   ```

3. Is idempotent (upsert on `(local_date, lane)` / `(local_date, flag_type)`)
   so a re-run after a late-arriving correction/override simply recomputes.
4. Feeds three consumers: (a) a new dashboard aggregate endpoint (trend view
   without recomputing weeks of unions on every request — a genuine
   performance win once weeks of history accumulate), (b) the anomaly rules
   (§3.4), (c) optionally `routines.py`, which could read rollups instead of
   re-scanning raw episodes on every mining run (an efficiency follow-up, not
   required for v1 — routines.py's docstring already notes new activity
   sources "flow into the desk-signal set automatically" with zero code
   change, and that stays true whether it reads episodes or rollups).

### 3.4 Anomaly flags (deterministic, on the rollup)

A short, reviewable rule list (each rule a pure function over the day's
rollup + `source_adapter_state`, same style as `occupation.py`'s
corroborator/contradictor checks):

| flag_type | condition | severity |
|---|---|---|
| `feeder_dark` | a source's `source_adapter_state.active=false` or checkpoint stale >2x its cron interval | warning (classify-before-flag: this is an infra flag, gates all behavioral flags below for the same source) |
| `sleep_missing` | `lane='sleep'` seconds == 0 for the day, **and** google_health feeder is active (not already `feeder_dark`) | warning |
| `routine_break` | an enabled routine's window has no corroborated `occupation_block` on a day it should fire, **and** the corroborating sources are active | info |
| `lane_share_outlier` | a lane's share of tracked time deviates >2x trailing-14-day median **and** total tracked seconds that day is above a minimum-evidence floor (guards against a low-evidence day producing a spurious 100%-in-one-lane outlier) | info |

Explicitly **not** proposed for v1: notification/paging on any flag. Flags
are a passive, queryable dashboard/API surface — `chronicler_day_close`'s own
spec already forbids extra proactive messages, and bu-whhll.12's gap-interview
surface is the correct, already-scoped home for turning `routine_break` into
an actual owner-facing prompt (this design's `routine_break` flag is exactly
the signal that surface consumes — sequencing note in §6).

### 3.5 LLM labeling (optional, additive, bounded)

Exactly one bounded call per local day (same shape/cost profile as
`chronicler_day_close`), invoked only after the deterministic rollup +
flags are written, over the reduced output only:
input = the day's `daily_rollups` rows + `daily_rollup_flags` rows +
episode titles for the top 1-2 episodes per lane (not raw sensor rows —
by the time the LLM sees anything, HA's 900k+ raw rows for the day have
already been reduced to a double-digit number of aggregate rows). Output =
a short natural-language label per flag and/or a one-line day summary,
written back to `daily_rollup_flags.detail`/a new `daily_rollups.narrative`
column. This is presentation polish, not inference — the deterministic
flags/lanes are already correct and complete without it; the LLM only makes
them readable. Fully optional for v1 shipping (can be a follow-up bead,
§6) and trivially disable-able (owner toggle) without affecting correctness.

---

## 4. Non-goals

- Not a per-event or streaming pipeline — batch/scheduled only, per the
  bead's explicit framing ("cost-prohibitive at this volume").
- Not a replacement for bu-whhll's Tier 0/1 feeder repairs — if Google
  Health's OAuth scopes stay broken, `sleep_missing` will fire constantly and
  correctly (that's the point: it surfaces the outage instead of silently
  reporting zero as normal, per §2 principle 5). This design assumes the
  feeders as they exist today, dark spots included.
- Not a geocoding/reverse-geocoding integration — place labeling (§3.2) is
  distance-threshold-only against owner-known reference points, no
  external API, no LLM.
- Not a notification surface — flags are queryable, not pushed (bu-whhll.12
  already owns the one sanctioned "ask the owner" surface for this class of
  signal).
- Not a schema/migration in this bead — table shapes above are illustrative
  for the implementation beads to migrate for real for later beads.

## 5. Risks

- **Retention-window race (new risk class, §2 principle 6):** the HA
  sensor-activity adapter is the first Chronicler adapter reading a table
  with a rolling TTL. A silently-broken adapter (vs. an adapter reading a
  TTL-free table, which just falls behind harmlessly) permanently loses
  unprojected source days once their `filtered_events` partition drops.
  Mitigation: `source_adapter_state`'s existing staleness visibility already
  covers "is this adapter running," but the implementation bead should add an
  explicit lag-vs-retention-cutoff check (e.g. alert if checkpoint watermark
  is within 30 days of the oldest retained partition) — a genuinely new
  monitoring requirement, not covered by any existing adapter.
- **Rule-table false positives** (motion sensor in a hallway everyone
  passes through) — mitigated by requiring corroboration before promotion to
  `layer=activity` (§3.1), same posture as `occupation_inferred`.
- **Anomaly fatigue** — a poorly-tuned `lane_share_outlier` could fire most
  days. Mitigated by the minimum-evidence floor and by shipping flags as a
  passive queryable surface (not a notification) in v1.
- **Double-counting risk between the new HA sensor-activity episodes and
  future ActivityWatch/SSID adapters** (bu-whhll.5/.6) — both could plausibly
  claim the same window. Mitigated by lane choice (§1.5: HA sensor-activity
  never claims `work`/`occupation`) and by `union_seconds`' existing
  same-lane overlap merge, which already handles two sources agreeing on one
  lane without double-counting.

## 6. Proposed implementation beads (sequenced)

Coordinator files these; sizes are rough (S/M/L). Dependencies are on each
other unless noted.

1. **[M] HA non-person sensor-activity adapter** — `home_assistant.sensor_activity`
   source reading `connectors.filtered_events` (domain != person), rule-table
   classifier (motion→`room_activity_episode`, door/garage→`entry_event` point
   events), `layer=evidence` by default with reconciliation-based promotion to
   `activity`. Include the retention-lag monitoring check from §5 as part of
   this bead (it's cheap to add alongside the adapter, and there is no later
   natural home for it). Depends on: nothing new (filtered_events already
   populated today). *Discovered-from bu-p2d0f.*
2. **[M] OwnTracks GPS place-cluster adapter** — `owntracks.place_cluster`
   source, deterministic radius/dwell clustering over `owntracks_points`,
   distance-threshold place labeling against owner-declared reference points.
   Independent of #1; can run in parallel. Coordinate `source_name`/
   `episode_type` choice with whichever lands second between this and
   bu-whhll.5 so `occupation.py`'s corroborator list can add both cleanly.
   *Discovered-from bu-p2d0f.*
3. **[M] `chronicler.daily_rollups` + `daily_rollup_flags` migration and
   rollup materializer job** (`chronicler_rollup_daily`) — reuses
   `lane_for_activity`/`union_seconds` exactly as the live aggregate endpoint
   does (regression test: rollup output must match a same-window
   `aggregate/by-category` call bit-for-bit, closing off any KPI-divergence
   risk). Depends on: nothing new functionally, but sequences after #1/#2 in
   practice so the first rollups have richer lane coverage to summarize.
   *Discovered-from bu-p2d0f.*
4. **[S] Deterministic anomaly flag rules** (`feeder_dark`, `sleep_missing`,
   `routine_break`, `lane_share_outlier`) on top of #3, consulting
   `source_adapter_state` per the classify-before-flagging principle (§2.5).
   Depends on: #3.
5. **[M] Dashboard/API surface for daily rollups + flags** —
   `GET /api/chronicler/rollups?date=...` (or a range), a small trend widget;
   follows the existing degraded-source envelope conventions where relevant
   (a day with a `feeder_dark` flag should render as "data unavailable," not
   a false all-clear zero). Depends on: #3, #4.
6. **[S] Bounded once-daily LLM labeling pass** on rollup + flags (§3.5) —
   genuinely optional; ship after #4 once there is something worth narrating.
   Depends on: #4.
7. **[S] Wire `routine_break` flag into bu-whhll.12's gap-interview surface**
   as its unaccounted-time trigger input, if bu-whhll.12 has not already
   shipped its own detection by the time this lands — coordinate at
   implementation time rather than building two detectors. Depends on: #4,
   and on bu-whhll.12's status.

None of the above requires a schema change to any table this bead's design
doc did not already name; none touches another butler's schema; all reuse
the existing `ProjectionAdapter`/scheduled-job/cron-registration pattern
(`roster/chronicler/butler.toml` + `src/butlers/chronicler/jobs.py`) with no
new infrastructure beyond the rollup table itself.
