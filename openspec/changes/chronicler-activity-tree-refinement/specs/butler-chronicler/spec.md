## ADDED Requirements

### Requirement: Inferred Sleep and Owner Awake Time

Chronicler SHALL infer a low-confidence sleep block for a night the watch did not record, from the owner's usual sleep window and the absence of owner activity. It SHALL measure untracked time and choose gap-interview blocks only inside the owner's awake time: the local day minus its sleep. While the owner has at least 5 nights of watch sleep in the last 28 days, it SHALL NOT use a fixed clock window.

ID: REQ-butler-chronicler-018
Source: bu-r3fmja.5; deep dive 2026-11-03 [Observed] dev database (watch sleep missing on 3 of 14 nights; median bedtime about 00:50, wake about 07:25)
Scope: v1-mandatory

#### Scenario: A night without watch sleep gets inferred sleep
- **WHEN** no watch `sleep_episode` overlaps the owner's sleep window for a night (median bedtime − 2 h to median wake time + 2 h over the last 28 days, from at least 5 nights; otherwise 22:00–10:00)
- **AND** inside that window the owner stays at one place for at least 3 hours, with no fix more than 150 m from it and no owner outbound message, Steam in-game session, Spotify session or movement episode
- **THEN** Chronicler SHALL emit an `inferred_sleep_block` over the longest such run, counted in the Sleep lane with `confidence = low`

#### Scenario: Watch sleep supersedes inferred sleep
- **WHEN** a watch `sleep_episode` for that night arrives after an `inferred_sleep_block` was emitted
- **THEN** the next re-derivation SHALL tombstone the inferred block, and the watch episode alone SHALL count

#### Scenario: Late-night activity counts and morning sleep does not
- **WHEN** the owner sleeps 02:18–07:53 after playing from 21:00 to 01:56
- **THEN** untracked time SHALL exclude 02:18–07:53
- **AND** the 22:00–01:56 play SHALL count toward tracked time

#### Scenario: A night with no sleep evidence uses the median
- **WHEN** a night has neither watch nor inferred sleep and the owner has at least 5 nights of watch sleep in the last 28 days
- **THEN** the median bedtime to the median wake time SHALL count as sleep when computing awake time

#### Scenario: No sleep history
- **WHEN** the owner has fewer than 5 nights of watch sleep in the last 28 days and a night has neither watch nor inferred sleep
- **THEN** 22:00–06:00 SHALL count as sleep when computing awake time

### Requirement: Exercise Requires Effort

Chronicler SHALL count a Google Health workout as Exercise only when it is effortful. A workout is effortful when the owner recorded or started it, when it is not a walk, or when it averages at least 4 km/h for at least 10 minutes. A workout that is not effortful SHALL add nothing to any lane, so the place or movement block around it decides that time. During a trip, workouts SHALL count as Travel.

ID: REQ-butler-chronicler-019
Source: bu-r3fmja.6; deep dive 2026-11-03 [Observed] dev database (82 of 124 auto-detected walks in 28 days average under 2.5 km/h, 76 of 95 h); owner decision 2026-11-03 (the whole trip is Travel)
Scope: v1-mandatory

#### Scenario: A slow auto-detected walk is not exercise
- **WHEN** Google Health passively detects a walk of 88 minutes covering 1.7 km at an unregistered place, with no overlapping movement episode
- **THEN** its `workout_episode` SHALL be `layer = evidence` with `payload.effort = "low"`
- **AND** the `outing_block` over the same time SHALL count it as Social

#### Scenario: A brisk or owner-started workout is exercise
- **WHEN** a workout was recorded manually or started by the owner on a device (`detection` is `manual` or `active`), or is not a walk, or averages at least 4 km/h over at least 10 minutes
- **THEN** outside a trip span, an `exercise_block` SHALL count it in the Exercise lane

#### Scenario: History agrees with new rows
- **WHEN** the change is deployed
- **THEN** existing `workout_episode` rows SHALL be re-layered to evidence with `effort` computed by the speed and type rules

### Requirement: Steam In-Game Status Times Play

Chronicler SHALL time Play from Steam's in-game status changes when they exist, and SHALL fall back to the daily playtime total only for dates that have none. Play SHALL come from Steam alone. Chronicler SHALL NOT infer Play from PC power, home presence, media players or music. Play SHALL NOT be counted while the owner is known to be away from home.

ID: REQ-butler-chronicler-020
Source: bu-r3fmja.7; owner decision 2026-11-03 ("only Steam means play"); deep dive [Observed] Nov 1 daily total placed at 21:38–01:56 while in-game changes ran about 13:15–16:51 and 21:06–01:56
Scope: v1-mandatory

#### Scenario: In-game changes become a timed session
- **WHEN** Steam status history shows game G from a 13:15 change until a 16:51 change that shows no game
- **THEN** Chronicler SHALL project a `play_session` evidence episode spanning 13:15–16:51 with minute precision
- **AND** a `play_block` SHALL count that span in the Play lane while the owner is at home or their location is unknown

#### Scenario: Play displaces the home block
- **WHEN** a `play_block` lies inside a home stay
- **THEN** the `home_block` SHALL NOT cover any minute of the `play_block`

#### Scenario: An open session is provisional
- **WHEN** the latest status change shows game G and no later change exists
- **THEN** the session SHALL end at the earlier of now and its start + 12 hours, marked `provisional`

#### Scenario: Away from home is not Play
- **WHEN** part of a `play_session` overlaps a where-segment away from home
- **THEN** that part SHALL produce no `play_block`

#### Scenario: Daily totals step aside for timed sessions
- **WHEN** a date has at least one `play_session`
- **THEN** the date's `steam.play_history` `play_episode` SHALL be `layer = evidence`
- **AND** a date with no session SHALL keep its daily-total `play_episode` at `layer = activity`

#### Scenario: Neither PC power nor music makes Play
- **WHEN** a PC draws over 300 W at home, or Spotify is playing, with no Steam in-game status
- **THEN** no Play time SHALL be counted
- **AND** Spotify listening episodes SHALL be `layer = evidence`

### Requirement: Trip Days Count as Travel

Chronicler SHALL count the owner's awake time on a trip away from home as Travel. A trip is established by location alone: the span runs from the departure fix before the owner goes more than 50 km from home until the first fix back within 50 km. The gap interview SHALL ask nothing about a day spent mostly on a trip. This requirement replaces the rule that a stay more than 50 km from home produces no situational block.

ID: REQ-butler-chronicler-021
Source: bu-r3fmja.8; owner decision 2026-11-03 ("the whole trip is Travel"); deep dive [Observed] Oct 21–30 trip mostly unaccounted
Scope: v1-mandatory

#### Scenario: A trip by location is Travel
- **WHEN** an accepted fix within 50 km of home is followed by fixes more than 50 km from home, then by a fix within 50 km of home
- **THEN** Chronicler SHALL emit a `trip_block` in the Travel lane from the last near fix before departure to the first near fix on return
- **AND** the block SHALL exclude only sleep and owner answers
- **AND** no outing, home, work, play or exercise block SHALL be emitted inside the span

#### Scenario: A long silence on a trip
- **WHEN** two consecutive far fixes are more than 72 hours apart
- **THEN** the trip span SHALL end at the earlier fix and resume at the later one

#### Scenario: The calendar does not make a trip
- **WHEN** calendar events name flights or hotel stays but no fix is more than 50 km from home
- **THEN** no `trip_block` SHALL be emitted

#### Scenario: No gap interview on a trip day
- **WHEN** more than half of a local day's awake time lies inside a trip span
- **THEN** the gap interview SHALL ask nothing for that day

### Requirement: Home Is Never Work

Chronicler SHALL count Work only at the registered work place. Occupation, focus and screen episodes SHALL add nothing to the Work lane where they overlap a span when the owner is at home. PC power and home presence sensors SHALL NOT be Work sources.

ID: REQ-butler-chronicler-022
Source: bu-r3fmja.9; owner decision 2026-11-03 ("keep office-only": home is never Work)
Scope: v1-mandatory

#### Scenario: An inferred occupation block at home
- **WHEN** an `occupation_block` overlaps a home stay
- **THEN** the overlapping part SHALL add nothing to the Work lane, and the home stay's other rules SHALL decide that time

#### Scenario: Study PC on during the workday
- **WHEN** the owner is at home on a weekday from 09:00 to 18:00 and a home PC draws steady power
- **THEN** no Work time SHALL be counted for those hours
