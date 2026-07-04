# Chronicler Time-Inference Deep Dive — why the workday is invisible

**Date:** 2026-07-05
**Trigger:** Owner report — "whenever I'm at work (10–12 h/day every weekday) there's no
signal on `/chronicles`; the pie chart severely underrepresents work. Geolocation for work
is the same as home, so that isn't a useful signal. Steam and Spotify tracking are great,
but major time sinks aren't being inferred."
**Method:** Live-data audit against the dev DB (`butlers` @ butlers-db-dev, schema
`chronicler` + `connectors`), connector container logs, and a code/spec read of the
chronicler projection + aggregation + day-close pipeline.

---

## 1. Case study: Thursday 2026-07-02 (SGT)

What the chronicler actually stored for the day the owner cited:

| Source | Episode type | Count | Span (SGT) | Hours |
|---|---|---|---|---|
| `spotify.session_summary` | listening_episode | 5 | 12:06–22:45 | 7.15 |
| `steam.play_history` | play_episode | 1 | 22:22–00:53 | 2.52 |
| `comms.message_bursts` | social_episode | 19 | 00:29–22:29 | 2.02 |
| `core.sessions` (butler LLM sessions) | work | 30 | scattered | 0.56 |
| `owntracks.points` | movement_episode | 2 | — | 0.03 |

KPI endpoint (`/api/chronicler/kpi?date=2026-07-02`) returned top lanes:
**other 8.0 h** (the all-day *"Singapore Armed Forces Day"* calendar event — an intent-layer
row leaking into the KPI, see bug §3.4), **music 7.2 h**, **social 2.0 h**. `sleep_minutes: 0`.
Work: effectively zero.

Raw-signal audit for the same day:

- **OwnTracks:** 3 points all day (significant-motion mode; owner stationary). Payload has
  `conn: "w"` but **no SSID field** and `inregions: []` (no waypoints configured).
- **Home Assistant history:** 0 rows.
- **`health.facts`:** 1 weight measurement. No sleep, steps, heart-rate, or workout
  predicates exist in the table *at all* (all-time).
- **Calendar:** 0 timed events (the owner's work calendar is not connected; only the
  personal calendar syncs, plus the all-day holiday).
- **Desktop / screen / browser / dev activity:** no such signal source exists in the system.

The Spotify sessions (12:06–22:45, five blocks with short gaps) are almost certainly the
owner's **desk-listening soundtrack at work** — the system stores 7 h of evidence that the
owner was at a desk, and classifies it "music/play".

## 2. How time becomes a pie slice today

1. **16 projector jobs** (`chronicler_project_*`, cron `*/15`–`*/30 min`) run deterministic
   adapters (`src/butlers/chronicler/adapters/`) that read approved source surfaces and
   upsert `chronicler.episodes` / `point_events` with `layer` ∈ intent|evidence|activity and
   `confidence`. No LLM (RFC 0014 §D5).
2. **`category_for()` + `lane_for_activity()`** (`src/butlers/chronicler/aggregations.py`)
   map `(source_name, episode_type)` → category → life-balance lane
   (`sleep, exercise, work, play, social, travel, eat, rest`). Only `activity`-layer episodes
   count; intent (calendar) and evidence rows are dropped.
3. **Day-close** (`chronicler_day_close`, 01:05 nightly) narrates a pre-reconciled,
   token-bounded bundle via one LLM call and telegrams a summary. It is explicitly barred
   from re-deriving what counts and from sending correction prompts.
4. The frontend (`AggregatePieChart.tsx`, `GanttSwimlane.tsx`) renders backend categories;
   there is no "untracked" slice for unaccounted waking time.

**The only paths into the "work" lane today:**

- `core.sessions` butler LLM sessions (`conversations`/`tasks` categories) — *the butlers'*
  work, not the owner's; ~34 min on 07-02.
- `chronicler.focus_inferred` focus blocks — fires only on (a) butler sessions ≥ 45 min or
  (b) calendar events titled `focus|deep work|pomodoro`. **Dry since 2026-04-24.**
- `chronicler.reading_inferred` reading blocks — reads `health.facts
  predicate=reading_session`; **has never produced a row.**

The owner's employment — the single largest time allocation of the week — has **no signal
source, no adapter, and no taxonomy representation**.

## 3. Root causes, ranked

### 3.1 No sensor covers the workday (primary)

Nothing observes the work computer, and every existing sensor degenerates during
work hours: phone stationary, geolocation identical to home, work calendar not synced,
HA/health dark (see 3.2). This is a **coverage** problem before it is an inference problem —
no amount of prompt or adapter cleverness can conjure evidence that is never captured.

### 3.2 Three feeder sources are broken (verified 2026-07-05)

| Source | Failure | Effect |
|---|---|---|
| google-health connector | **403 "insufficient authentication scopes"** on sleep, steps, resting-HR, SpO₂, respiratory, activity — every data type, every poll | `health.facts` never receives `sleep_session`/`workout_session`/steps/HR ⇒ sleep lane permanently 0, `exercise_inferred` + `reading_inferred` starved, sleep/exercise streaks always 0 |
| home-assistant connector | `ConnectionError: Failed to connect to http://butlers-up:41100/sse` on ingest | `connectors.home_assistant_history` empty ⇒ `presence_episode` (home/rest lane) never projected |
| OwnTracks app config | significant-motion only (1–78 pts/day), SSID reporting off, no waypoints/regions defined | movement episodes near-zero; the one sensor that *could* disambiguate home-vs-office (Wi-Fi SSID) isn't reporting it |

Fixing the Google Health scopes alone restores ~7–8 h/day (sleep) of currently-unknown time.

### 3.3 Taxonomy conflates butler work with owner work

`conversations`/`tasks` (butler sessions) map to the **Work** lane. When the owner's real
work eventually gets signal, it will share a lane with butler telemetry. The lane needs to
mean *owner occupation*; butler-session time is better folded elsewhere (or kept as a
separate, clearly-labelled lane).

### 3.4 KPI intent-leak bug

`_compute_kpi` (`src/butlers/chronicler/editorial.py`) buckets episodes with raw
`category_for()` and **skips the `lane_for_activity` layer filter and `union_seconds`
overlap merge** that the aggregate endpoint applies. Result: the all-day SAF-Day calendar
*intent* row surfaced as "other: 8.0 h" — the top slice of the owner's day — and
`longest_episode_title` proudly reports an event nobody attended. Same-day episodes also
double-count on overlap. One-file fix.

### 3.5 The inference layer stops at projection

Corrections/overrides machinery exists and works (`chronicler_submit_correction`,
override-wins views) — **0 rows ever written**. `seasonal_periods` — empty. There is no
routine model: the chronicler has watched ~10 weeks of days in which Spotify hums
12:00–22:00 and messages burst on weekday daytimes, and has never been asked to notice the
pattern. Day-close explicitly may not ask the owner anything.

## 4. What already exists to build on

- **IEA epic `bu-jc6htw`** (openspec change `chronicler-intent-evidence-activity`):
  layer/confidence storage, activity-only counting, comms→social adapter, and day-close
  deterministic reconciliation already shipped. Remaining open children: memory write-back
  (bu-93y4rt), balance/trends/who-with endpoints (bu-jc6htw.2), **Day Ribbon frontend that
  replaces the pie** (bu-8whey5), validation (bu-f3cznw).
- **Decision loop, RFC 0021 / PR #2840** (one-tap approvals + decision memory, gated behind
  bu-24lu6.1) — the natural vehicle for "was this a work day?" confirmations.
- **Confidence ladder + `evidence_refs`** — inferred episodes can carry low confidence and
  cite the weak signals that support them.
- `chronicler.overrides`/`corrections` + `v_episodes_corrected` — owner corrections layer.

## 5. Improvement plan

### Tier 0 — repair the dark feeders (plumbing; days, high leverage)

1. **Google Health re-consent with widened scopes** (`fitness.sleep.read`,
   `fitness.activity.read`, heart-rate etc. per `google_health.py` requirements); verify
   `health.facts` receives sleep/steps/HR; watch the 7-day test-mode expiry trap.
2. **Fix Home Assistant → switchboard ingest connectivity** (`butlers-up:41100/sse`
   unreachable from the connector container).
3. **Fix `_compute_kpi`** to count activity-layer only via `lane_for_activity` +
   `union_seconds` (regression test: all-day calendar event contributes 0).
4. **OwnTracks client config on the phone:** enable Wi-Fi SSID reporting, define
   home/office waypoints, consider `monitoring=significant`→`move` during waking hours.
   (Owner action + a docs page; no code.)

### Tier 1 — give the workday a sensor (the decisive move)

5. **Wi-Fi SSID presence adapter** (cheapest honest signal): once OwnTracks reports SSID,
   a deterministic adapter rolls contiguous same-SSID points into `presence_episode`s
   (office SSID → work-presence, home SSID → home). Solves the "home ≈ work geolocation"
   degeneracy outright, entirely on existing infrastructure.
6. **Desktop-activity connector (ActivityWatch)**: AW is open-source, local-first, runs on
   the work machine (subject to employer policy), captures active app/window title + AFK.
   New connector polls the local AW REST API (or accepts pushed heartbeats over
   Tailscale), buckets app classes (IDE/terminal/browser-by-domain), writes
   `connectors.activitywatch_events`; chronicler adapter projects `work_session` /
   `screen_episode` activity rows. This is the gold-standard fix — it is exactly the class
   of signal Steam/Spotify already prove works well.
7. **Work calendar via ICS subscription** (if corp policy allows the private ICS URL):
   meetings land as intent-layer blocks, corroborated into activity by SSID/desktop
   presence.
8. **Owner outbound messages as point events**: the user-client connectors already see
   owner-authored messages (tagged `(owner)` in bundles); project them as
   phone-activity point evidence for burst/corroboration purposes.

### Tier 2 — teach the chronicler routines (inference proper)

9. **Routine miner** (deterministic, no LLM): weekly job that mines N weeks of episodes +
   point events for stable weekday patterns (e.g. "Mon–Fri 09:30–19:30: continuous desk
   signals, no movement, no gaming") and writes owner-reviewable rows to a new
   `chronicler.routines` table (day-of-week mask, window, label, support/confidence,
   evidence summary).
10. **`occupation_inferred` adapter**: on weekdays matching an approved work routine, emit
    `occupation_block` activity episodes (`confidence=low`, `precision=hour`,
    `evidence_refs` citing the corroborating weak signals) when ≥1 weak corroborator is
    present (desk-Spotify, SSID presence, outbound messages) and no contradictor
    (movement/travel episodes away, leave/holiday all-day events, gaming). Category
    `occupation` → Work lane.
11. **Owner-declared schedule as bootstrap**: a dashboard/settings surface (or telegram
    exchange) where the owner declares "I work Mon–Fri ~09:30–19:30" directly into
    `chronicler.routines` with `origin=declared`, so inference works from day one and the
    miner refines it.
12. **Day-close gap interview via the decision loop** (needs the deliberate opt-in the
    day-close prompt reserves): when >2 h of waking time remains unaccounted, send ONE
    one-tap prompt ("Yesterday 09:30–19:30 looks like a work day — confirm?"); the answer
    writes a correction override and reinforces/decays the routine prior. Corrections
    finally get their first tenant, and every answer improves the model (decision memory,
    RFC 0021).

### Tier 3 — presentation honesty

13. **Untracked slice**: until the Day Ribbon (bu-8whey5) ships, the pie should show
    unaccounted waking time explicitly instead of renormalising over 4 h of tracked
    evidence — an 11 h music+play day the owner didn't have.
14. **Split "Work" lane semantics**: owner occupation vs butler-session time must not share
    a slice; relabel butler sessions ("Butler ops"?) or fold them out of the default view.

## 6. Doctrine compliance check

- **No per-event LLM** (RFC 0014 §D5): routine miner, SSID adapter, occupation adapter,
  ActivityWatch projection are all deterministic. Day-close stays one bounded call. The gap
  interview is one owner-facing decision per day, not a per-event call.
- **Retrospective-only scope**: routines are mined from the past and used to label the
  past; the declared schedule is configuration, not planning. No scheduling tools added.
- **Evidence, not fabrication**: `occupation_block`s are low-confidence, evidence-cited,
  correction-prompted — consistent with IEA's "only corroborated activity counts", with the
  corroboration bar explicitly set for weak-sensor days.
- **Connector pattern**: ActivityWatch/ICS follow the adding-connectors-and-modules skill
  (account registry → connector → module → dashboard).

## 7. Verification queries used (dev DB)

```sql
-- Episodes for a local day (SGT)
SELECT source_name, episode_type, layer, count(*),
       round(sum(EXTRACT(EPOCH FROM (coalesce(end_at,start_at)-start_at)))/3600.0,2) AS hours
FROM chronicler.episodes
WHERE start_at >= '2026-07-01 16:00+00' AND start_at < '2026-07-02 16:00+00'
  AND tombstone_at IS NULL
GROUP BY 1,2,3;

-- Adapter registry + health
SELECT source_name, active, read_surface FROM chronicler.source_adapter_state;
SELECT name, cron, last_run_at, last_result->>'rows_projected'
FROM chronicler.scheduled_tasks;

-- Feeder emptiness
SELECT predicate, count(*) FROM health.facts GROUP BY 1;          -- no sleep/steps/HR ever
SELECT count(*) FROM connectors.home_assistant_history;           -- empty for 07-02
SELECT count(*) FROM chronicler.overrides;                        -- 0
```

Connector log evidence: `docker logs butlers-dev-connector-google-health-1` (403
insufficient scopes on every data type), `docker logs butlers-dev-connector-home-assistant-1`
(ConnectionError to butlers-up:41100/sse).
