# Design: Chronicler activity decision tree refinement

## Context

`chronicler-situational-inference` turns where-segments into counted blocks. A precedence rule decides which lane wins: sleep > exercise > travel > work > play > eat > social > rest. This change keeps the place machinery. It corrects four inputs the tree consumes:

- sleep coverage
- what counts as exercise
- when Steam play happened
- trips

It also applies the owner's three decisions end to end. The proposal records the evidence and the decisions.

`chronicler.situation_inferred` becomes the single fusion point for every lane that depends on place. Lane totals sum overlapping activity rows; they do not resolve overlaps. So a lane that place must be able to displace cannot also be counted from a separate activity row. Those inputs are projected as evidence instead:

- Steam in-game sessions
- workouts
- trip time
- Spotify

The adapter turns them into blocks itself.

## The refined tree

For each minute of a local day, the first rule that matches decides the lane:

```
0. The owner said so (gap answer, correction)            → that lane
1. Asleep: watch sleep, else inferred sleep (§1)          → Sleep
2. On a trip (§4)                                         → Travel
3. Effortful workout (§2)                                 → Exercise
4. Moving between places (OwnTracks movement), airport    → Travel
5. At the registered work place                           → Work
6. Steam in-game, owner not known to be away (§3)         → Play
7. At a place that is not home (incl. slow walks there)   → Social
8. At home                                                → Rest
9. No location, no activity                               → unaccounted (gap interview)
```

Placing trips above exercise follows owner decision 3: every waking hour of a trip is Travel. This is the one change to the earlier precedence order. Everywhere else, exercise still outranks travel.

Some sources never displace a place block:

- butler chat (`core.sessions`)
- message bursts (`comms.message_bursts`), which keep their existing Social rows
- episodes shorter than 10 minutes

Some signals never decide any lane:

- Spotify, now evidence (owner decision 2)
- PC power, home presence sensors and media players (owner decisions 1 and 2)

**Home is never Work.** Owner occupation, focus and screen episodes add nothing to the Work lane where they overlap a span at home. The occupation and focus adapters drop the at-home part, and the ActivityWatch screen projection does too.

## Computation order inside `chronicler.situation_inferred`

The adapter excludes its own rows from the activity spans it reads. So it derives the spans in precedence order and subtracts each from everything below it before it emits any block:

1. Owner answers, then watch sleep and inferred sleep.
2. Trip spans.
3. Effortful workouts outside trips.
4. Movement and airport time.
5. Work-place stays.
6. Steam sessions not away from home.
7. Other stays, then home stays.

It then emits the blocks in that order. No two of its blocks overlap. A `play_block` and a `home_block`, for example, never cover the same minute.

## 1. Inferred sleep and awake time

**Sleep window.** With at least 5 nights of watch sleep in the last 28 days, the sleep window runs from the median bedtime − 2 h to the median wake time + 2 h, in owner-local time. With fewer, it is 22:00–10:00.

**Inferred sleep.** For a night with no watch `sleep_episode` overlapping the window, the adapter finds the longest quiet run inside the window.

- A quiet run is an interval of at least 3 h with no owner signal:
  - an owner outbound message (`owner_outbound.messages`)
  - a Steam in-game session
  - a Spotify session
  - an OwnTracks movement episode
  - a fix more than 150 m from the run's place
- The owner must be at one stay for the whole run (a place segment or the tail hold).
- The run becomes an `inferred_sleep_block`: category `sleep`, `layer = activity`, `confidence = low`, minute precision.
- It is re-derived and tombstoned like the other blocks.
- Watch sleep that arrives later supersedes it on the next run.

**Awake time.** A local day's awake time is the day minus its sleep spans. Untracked time and the gap interview's block choice measure only awake time.

- A night with neither watch nor inferred sleep falls back as follows:
  - With at least 5 nights of history, the plain median bed→wake interval counts as sleep (not the ±2 h window).
  - With fewer, 22:00–06:00 counts as sleep, which is today's 06:00–22:00 waking window.

Example: Nov 1. There is no watch sleep for Oct 31 → Nov 1. The phone sat at home with no fix after 03:02, and the first owner message came at 08:21. That makes 03:02–08:21 inferred sleep and removes 06:00–08:21 from the 11.3 h gap.

## 2. Exercise needs effort

**Connector.** The Google Health connector currently maps every recording method except `MANUAL` to `detection = "auto"`. It now keeps three values:

- `manual` for `MANUAL`
- `active` for `ACTIVELY_MEASURED` (a workout the owner started on a device)
- `auto` for everything else

**Projection.** `google_health.measurements` projects every `workout_episode` at `layer = evidence`. The workout is effortful when one of these holds:

- `detection` is `manual` or `active`
- `activity_type` is not `walking`
- the average speed is at least 4 km/h over at least 10 minutes (`distance_m` divided by the episode's duration)

`payload.effort` records the result as `high` or `low`.

**Blocks.** The situation adapter emits an `exercise_block` (category `workout`, `layer = activity`) for each effortful workout outside a trip span. A low-effort walk produces no block, so the surrounding block decides its time:

- movement → Travel
- an outing → Social
- home → Rest

**History.** A migration re-layers existing workout rows and computes their `effort`. Before this change, rows cannot tell `active` from `auto`, so they rely on the speed and type rules.

## 3. Steam in-game status becomes timed sessions

**Connector.** The Steam connector polls `GetPlayerSummaries` every 5 minutes and diffs `personastate` and `gameextrainfo`; that delta rule is unchanged. On each change it inserts a row into `connectors.steam_status_history`:

- Columns: `steam_id`, `steam_account_id`, `observed_at` (the poll time), `persona_state`, `game_id` (stored when present, not diffed), `game_name`, `prev_game_name`, `recorded_at`.
- The table is idempotent on `(steam_id, observed_at)`.
- A core Alembic migration creates it and grants SELECT to `butler_chronicler_rw`.

**Chronicler.** A new adapter, `steam.status_history`, pairs the change rows into `play_session` episodes.

- A session starts at the row that shows game G. It ends at the next row, which shows no game, another game, or offline.
- An open session has no next row yet. It ends at the earlier of now and start + 12 h, is marked `provisional`, and is re-projected each run.
- Sessions are `layer = evidence`, minute precision (±5 minutes). The title is "Played <game>".

**Situation.** `chronicler.situation_inferred` emits a `play_block` (category `gaming` → Play, `layer = activity`, medium confidence) for each session.

- The part that overlaps a where-segment away from home gets no block. Someone else may be using the account, or the client may be idle.
- Unknown location does not block Play.

**Daily totals.** `steam.play_history` keeps projecting the daily total. A date with any status session gets its daily `play_episode` at `layer = evidence`. A date without one keeps the current activity row, which is the only source for the time before the status table existed.

**Spotify.** `spotify.session_summary` listening episodes become `layer = evidence`, by migration for history and by the adapter from now on. They keep their role as quiet-run breakers (§1).

## 4. Trip days are Travel

**Trip span.** A trip span starts at the last accepted fix within 50 km of home before the first fix more than 50 km away. Departure is usually seen at the airport, so the span covers the flight while the phone is off. The span ends at the first later fix within 50 km of home, which on return is usually the arrival airport.

- A silence longer than 72 h between far fixes ends the span at the last far fix. The span then resumes at the next far fix.

The calendar is not used. On dev, flight and hotel events land under whichever butler schema first projects them (`relationship`, `general`, `travel`), and none carry `butler_generated`. So no reliable filter selects the travel ones.

**Blocks.** The adapter emits a `trip_block` (category `travel`, `layer = activity`, medium confidence) over each span, minus sleep and owner answers. Nothing else inside the span gets a block.

**Gap interview.** The interview asks nothing for a local day when more than half of its awake time lies inside a trip span.

## Trade-offs

- **Median ± 2 h** catches the owner's late nights (bedtimes run from 23:18 to 02:48) without treating an afternoon nap at home as sleep. The 3 h quiet-run minimum and the ban on owner signals keep a long movie with the phone down from becoming sleep, unless it falls deep inside the window.
- **4 km/h** separates walking for its own sake from errands. Sustained walking runs at about 4.5–5 km/h. Auto-detected sessions that include standing still fall far below that: 82 of 124 average under 2.5 km/h.
- **Steam only.** PC power would place sessions more tightly and catch non-Steam games. The owner chose Steam only, so power stays out, and the 5-minute poll bounds the error.
- **Trips by location only.** Location is the evidence that the owner went, and the departure and arrival fixes bound the flights. The calendar would add little and is not reliably attributable.
- **More evidence, fewer activity rows.** Workouts, Steam sessions and Spotify move to evidence so that one adapter can resolve overlaps. The source rows stay queryable for the timeline; only lane totals change.
