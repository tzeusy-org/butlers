## MODIFIED Requirements

### Requirement: Activity Lane Taxonomy

Activity aggregates SHALL roll up into life-balance lanes:
`sleep`, `exercise`, `work`, `play`, `social`, `travel`, `eat`, `rest`. Source
types such as music, gaming, and calendar SHALL NOT appear as top-level lanes.
The complete mapping from existing episode types to lanes SHALL be defined and
each lane covered by a test:

| Existing episode type / source | Lane |
|---|---|
| `google_health` sleep, `chronicler.situation_inferred` `inferred_sleep_block` | `sleep` |
| `chronicler.situation_inferred` `exercise_block` (effortful workout outside a trip), inferred exercise (HR+GPS+cadence) | `exercise` |
| `chronicler.situation_inferred` `work_presence_block` (at the work place); owner occupation, focus, screen, never while the owner is at home | `work` |
| `chronicler.situation_inferred` `play_block` (Steam in-game, owner not away); Steam daily play total on a date with no in-game session | `play` |
| comms bursts (gmail/telegram/whatsapp/discord), co-presence, `chronicler.situation_inferred` `outing_block` (at an unregistered place) | `social` |
| `owntracks` movement, `chronicler.situation_inferred` `airport_block` and `trip_block` | `travel` |
| meals | `eat` |
| `chronicler.situation_inferred` `home_block` (at home with no other activity), idle/at-home presence | `rest` |
| `chronicler.owner_answer` `owner_<lane>_block` (gap-interview answer) | the lane answered |

`owntracks.place_cluster` `place_episode` rows are `evidence`: they say where
the owner was and are counted only through the situational blocks derived from
them. Spotify listening, Steam `play_session` rows and `google_health`
`workout_episode` rows are also `evidence`. Spotify counts toward no lane (owner
decision 2026-11-03: only Steam means Play). Steam sessions and workouts count
only through the `play_block` and `exercise_block` rows derived from them.

Existing projected source episodes (sleep, work, movement, meal) ARE the
`activity`-layer rows for their lane; Tier-1 projectors
do not re-emit duplicates of them — they add `confidence`/`evidence_refs` and
emit only genuinely *new* inferred activities (e.g. an exercise block inferred
from HR+GPS when no workout episode exists). Where both a source episode and an
inferred candidate cover the same block, day-close reconciliation merges them.

ID: REQ-chronicler-intent-evidence-activity-001
Source: IEA reframe tasks.md §4; bu-whhll.14; bu-r3fmja (situational blocks); owner decisions 2026-11-03 (home is never Work; only Steam means Play; trips are Travel)
Scope: v1-mandatory

#### Scenario: Music and gaming roll into Play

The title is kept for archive continuity. Since owner decision 2026-11-03, only gaming rolls into Play, and music rolls into no lane.

- **WHEN** a Steam `play_block` and an overlapping Spotify listening episode are aggregated
- **THEN** the Steam time appears under the `play` lane
- **AND** the Spotify listening adds nothing to any lane
- **AND** neither `music` nor `gaming` appears as a top-level lane

#### Scenario: Home is never Work

- **WHEN** an occupation, focus or screen episode overlaps a span where the owner is at home
- **THEN** that overlap adds nothing to the `work` lane

#### Scenario: Every lane has a covering mapping

- **WHEN** an episode of each mapped source type is aggregated
- **THEN** it rolls up under the lane named in the mapping table
- **AND** meals map to `eat`, effortful workouts (through `exercise_block`) to `exercise`, and movement to `travel`

#### Scenario: Place-derived blocks count in their own lanes

- **WHEN** situational blocks at the work place, at an unregistered place and at home are aggregated
- **THEN** they roll up under `work`, `social` and `rest` respectively
- **AND** the `place_episode` rows they were derived from add nothing to any lane
