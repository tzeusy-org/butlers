# Design: Chronicler activity decision tree refinement

## Context

`chronicler-situational-inference` turns where-segments into counted blocks. A precedence rule decides which lane wins: sleep > exercise > travel > work > play > eat > social > rest. This change keeps that order and the place machinery. It corrects four inputs the order consumes:

- sleep coverage
- what counts as exercise
- when Steam play happened
- trips

The proposal records the evidence and the owner decisions.

## The refined tree

For each minute of a local day, the first rule that matches decides the lane:

```
0. The owner said so (gap answer, correction)            → that lane
1. Asleep: watch sleep, else inferred sleep (§1)          → Sleep
2. On a trip (§4)
     effortful workout (§2)                               → Exercise
     otherwise                                            → Travel
3. Effortful workout (§2)                                 → Exercise
4. Moving between places (OwnTracks movement)             → Travel
5. At the registered work place                           → Work
6. Steam in-game, owner not known to be away (§3)         → Play
7. At a place that is not home (incl. slow walks there)   → Social
8. At home                                                → Rest
9. No location, no activity                               → unaccounted (gap interview)
```

The following never decide a lane: Spotify, butler chat, message bursts, PC power, home presence sensors, and the study PC. This follows owner decisions 1 and 2.

## 1. Inferred sleep and the personal waking window

**Sleep window.** The sleep window comes from the last 28 days of watch sleep. With at least 5 nights, it is the median bedtime − 2 h to the median wake time + 2 h, in owner-local time. Otherwise it is 22:00–10:00.

**Inferred sleep.** For a night with no watch `sleep_episode` overlapping the window, `chronicler.situation_inferred` looks for the longest quiet run inside the window.

- A quiet run is an interval of at least 3 h that holds none of these owner signals:
  - an owner outbound message (`owner_outbound.messages`)
  - a Steam in-game session
  - a Spotify session
  - an OwnTracks movement episode
  - a fix more than 150 m from the run's place
- The owner must be at one stay for the whole run (a place segment or the tail hold).
- The run becomes an `inferred_sleep_block`: category `sleep`, `layer = activity`, `confidence = low`, minute precision.
- The block is re-derived and tombstoned like the other situation blocks.
- A watch sleep record arriving later supersedes the block: the night then has watch sleep, so the next run tombstones the inferred block.

**Awake time.** A local day's awake time is the day minus its sleep spans (watch or inferred). A night with neither falls back to the median window as sleep. Untracked time and the gap interview measure only awake time. The fixed 06:00–22:00 window remains the fallback only when the owner has no sleep history at all.

Example: Nov 1. The watch has no sleep for Oct 31 → Nov 1. The phone sat at home with no fix after 03:00, and the first owner message came at 08:21. The quiet run inside the window is about 03:00–08:20, so it becomes inferred sleep. That removes 06:00–08:20 from the 11.3 h gap. The outing, Steam sessions and home blocks then fill most of the rest of the day.

## 2. Exercise needs effort

`google_health.measurements` projects a `workout_episode` at `layer = activity` only when one of these holds:

- `payload.detection` is not `auto` (the owner started it).
- `payload.activity_type` is not `walking`.
- The average speed, `distance_m` divided by the episode's duration, is at least 4 km/h and the episode lasts at least 10 minutes.

Any other workout is projected at `layer = evidence`, with `payload.effort = "low"`.

- Away from home, the surrounding outing or travel block then covers the time.
- At home, the home block covers it.
- No data is dropped.

A migration re-layers existing auto-detected slow walks with the same rule, so history and new rows agree.

## 3. Steam in-game status becomes timed sessions

**Connector.** The Steam connector already polls `GetPlayerSummaries` every 5 minutes and diffs `gameextrainfo`. It now also inserts one row per observed change into `connectors.steam_status_history`:

- Columns: `steam_id`, `steam_account_id`, `observed_at` (the poll time), `persona_state`, `game_id`, `game_name`, `prev_game_name`, `recorded_at`.
- The table is idempotent on `(steam_id, observed_at)`.
- A core Alembic migration creates it and grants SELECT to `butler_chronicler_rw`.

**Chronicler.** A new adapter, `steam.status_history`, pairs the changes into `play_session` episodes.

- A session starts at the first poll that shows game G. It ends at the first later poll that shows no game, another game, or offline.
- An open session ends at the latest poll + 10 minutes, capped at now, and is marked `provisional`.
- Sessions are minute precision (±5 min) at `layer = evidence`. The title is "Played <game>".

**Situation.** `chronicler.situation_inferred` turns sessions into `play_block` rows: category `gaming` → Play, `layer = activity`, medium confidence.

- The part of a session that overlaps a where-segment away from home gets no block. Someone else may be using the account, or the client may be idle.
- Unknown location does not block Play.

**Daily totals.** `steam.play_history` keeps projecting the daily total. A date that has any status session gets its daily `play_episode` at `layer = evidence`. A date without one keeps the current activity row, which is the only source for history from before the status table existed.

## 4. Trip days are Travel

**Trip span.** A trip span starts at the first accepted fix more than 50 km from home. It continues while each later fix is also more than 50 km away, bridging silences of up to 72 hours. It ends at the first fix within 50 km of home.

**Calendar extension.** A `google_calendar.completed` block whose `payload.schema = 'travel'` extends a span if it overlaps it or lies within 24 h of it. These blocks are the Travel butler's own flights and stays. This covers airport time and flights with the phone off. Calendar blocks from other schemas never extend a span.

**Blocks.** `chronicler.situation_inferred` emits a `trip_block` over the span: category `travel`, `layer = activity`, medium confidence.

- The block excludes sleep, effortful workouts and owner answers.
- It replaces the outing, home and work blocks inside the span.
- The earlier rule "a stay more than 50 km from home gets no block" is replaced by this one.

**Gap interview.** The interview asks nothing for a local day when more than half of its awake time is inside a trip span.

## Trade-offs

- **Bed and wake medians ± 2 h** catch late nights (the owner's bedtimes run from 23:18 to 02:48) without treating an afternoon nap at home as sleep. The quiet-run threshold of 3 h keeps a long movie with the phone down from becoming sleep, unless it falls inside the window.
- **4 km/h** separates walking for its own sake from errands. Sustained walking runs at about 4.5–5 km/h. Auto-detected sessions that include standing still fall far below that: 82 of 124 average under 2.5 km/h.
- **Steam status vs. PC power.** Power would place sessions more tightly and would also catch non-Steam games. The owner chose Steam only, so power stays out, and the 5-minute poll bounds the error.
- **Trips by distance, not by calendar.** Location is the evidence that the owner went. The calendar only fills the edges, so a cancelled booking never invents a trip.
