## ADDED Requirements

### Requirement: Inferred Sleep and Owner Awake Time

Chronicler SHALL infer a low-confidence sleep block for a night the watch did not record, from the owner's usual sleep window and the absence of owner activity. It SHALL measure untracked time and choose gap-interview blocks only inside the owner's awake time: the local day minus its sleep. It SHALL NOT use a fixed clock window while the owner has sleep history.

ID: REQ-butler-chronicler-018
Source: bu-r3fmja.5; deep dive 2026-11-03 [Observed] dev database (watch sleep missing on 3 of 14 nights; median bedtime about 00:50, wake about 07:25)
Scope: v1-mandatory

#### Scenario: A night without watch sleep gets inferred sleep
- **WHEN** no watch `sleep_episode` overlaps the owner's sleep window for a night (the median bedtime − 2 h to the median wake time + 2 h over the last 28 days, from at least 5 nights)
- **AND** inside that window the owner stays at one place for at least 3 hours with no owner outbound message, Steam in-game session, Spotify session or movement episode
- **THEN** Chronicler SHALL emit an `inferred_sleep_block` over the longest such run, counted in the Sleep lane with `confidence = low`

#### Scenario: Watch sleep supersedes inferred sleep
- **WHEN** a watch `sleep_episode` for that night arrives after an `inferred_sleep_block` was emitted
- **THEN** the next re-derivation SHALL tombstone the inferred block, and the watch episode alone SHALL count

#### Scenario: Late-night activity counts and morning sleep does not
- **WHEN** the owner sleeps 02:18–07:53 and plays from 21:00 to 01:56 the night before
- **THEN** untracked time SHALL exclude 02:18–07:53
- **AND** the 22:00–01:56 activity SHALL count toward tracked time

#### Scenario: No sleep history at all
- **WHEN** the owner has fewer than 5 nights of watch sleep in the last 28 days and no sleep for the night in question
- **THEN** awake time SHALL fall back to 06:00–22:00 local

### Requirement: Exercise Requires Effort

Chronicler SHALL count a Google Health workout as Exercise only when the owner started it, when it is not a walk, or when it averages at least 4 km/h for at least 10 minutes. Any other workout SHALL be kept as evidence that counts toward no lane, so the place block around it decides the time.

ID: REQ-butler-chronicler-019
Source: bu-r3fmja.6; deep dive 2026-11-03 [Observed] dev database (82 of 124 auto-detected walks in 28 days average under 2.5 km/h, 76 of 95 h)
Scope: v1-mandatory

#### Scenario: A slow auto-detected walk is not exercise
- **WHEN** Google Health auto-detects a walk of 88 minutes covering 1.7 km
- **THEN** its `workout_episode` SHALL have `layer = evidence` and `payload.effort = "low"`
- **AND** an outing block over the same time SHALL count it as Social

#### Scenario: A brisk or deliberate workout is exercise
- **WHEN** a workout was started by the owner, or is not a walk, or averages at least 4 km/h over at least 10 minutes
- **THEN** its `workout_episode` SHALL have `layer = activity` and count in the Exercise lane

#### Scenario: History agrees with new rows
- **WHEN** the change is deployed
- **THEN** existing `workout_episode` rows SHALL be re-layered by the same rule

### Requirement: Steam In-Game Status Times Play

Chronicler SHALL time Play from Steam's in-game status changes when they exist, and SHALL fall back to the daily playtime total only for dates that have none. Play SHALL come from Steam alone. Chronicler SHALL NOT infer Play from PC power, home presence or media players. Play SHALL NOT be counted while the owner is known to be away from home.

ID: REQ-butler-chronicler-020
Source: bu-r3fmja.7; owner decision 2026-11-03 ("only Steam means play"); deep dive [Observed] Nov 1 daily total placed at 21:38–01:56 while in-game changes ran about 13:15–16:51 and 21:06–01:56
Scope: v1-mandatory

#### Scenario: In-game changes become a timed session
- **WHEN** Steam status history shows game G from a 13:15 poll until a 16:51 poll that shows no game
- **THEN** Chronicler SHALL project a `play_session` evidence episode spanning 13:15–16:51 with minute precision
- **AND** a `play_block` SHALL count that span in the Play lane while the owner is at home or their location is unknown

#### Scenario: Away from home is not Play
- **WHEN** part of a `play_session` overlaps a where-segment away from home
- **THEN** that part SHALL produce no `play_block`

#### Scenario: Daily totals step aside for timed sessions
- **WHEN** a date has at least one `play_session`
- **THEN** the date's `steam.play_history` `play_episode` SHALL be `layer = evidence`
- **AND** a date with no session SHALL keep its daily-total `play_episode` at `layer = activity`

#### Scenario: PC power never makes Play
- **WHEN** a PC draws over 300 W at home with no Steam in-game status
- **THEN** no Play block SHALL be emitted

### Requirement: Trip Days Count as Travel

Chronicler SHALL count the owner's awake time on a trip away from home as Travel. A trip is established by location more than 50 km from home and extended by the Travel butler's calendar blocks. The gap interview SHALL ask nothing about a day spent mostly on a trip. This requirement replaces the rule that a stay more than 50 km from home produces no situational block.

ID: REQ-butler-chronicler-021
Source: bu-r3fmja.8; owner decision 2026-11-03 ("whole trip is Travel"); deep dive [Observed] Oct 21–30 trip mostly unaccounted
Scope: v1-mandatory

#### Scenario: A trip by location is Travel
- **WHEN** consecutive accepted fixes are more than 50 km from home, with no silence longer than 72 hours, until a fix within 50 km of home
- **THEN** Chronicler SHALL emit a `trip_block` over that span in the Travel lane
- **AND** the block SHALL exclude sleep, effortful workouts and owner answers

#### Scenario: Travel calendar blocks cover the edges
- **WHEN** a `google_calendar.completed` block with `payload.schema = 'travel'` overlaps a trip span or lies within 24 hours of it
- **THEN** the trip span SHALL extend to cover that block
- **AND** a calendar block from any other schema SHALL NOT extend a trip span

#### Scenario: No calendar trip without location
- **WHEN** a travel calendar block exists but no fix is more than 50 km from home within 24 hours of it
- **THEN** no `trip_block` SHALL be emitted

#### Scenario: No gap interview on a trip day
- **WHEN** more than half of a local day's awake time lies inside a trip span
- **THEN** the gap interview SHALL ask nothing for that day
