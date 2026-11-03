## MODIFIED Requirements

### Requirement: Activity Lane Taxonomy

Activity aggregates SHALL roll up into life-balance lanes:
`sleep`, `exercise`, `work`, `play`, `social`, `travel`, `eat`, `rest`. Source
types such as music, gaming, and calendar SHALL NOT appear as top-level lanes.
The complete mapping from existing episode types to lanes SHALL be defined and
each lane covered by a test:

| Existing episode type / source | Lane |
|---|---|
| `google_health` sleep | `sleep` |
| `google_health` workout, inferred exercise (HR+GPS+cadence) | `exercise` |
| owner occupation, focus, screen and `chronicler.situation_inferred` `work_presence_block` (at the work place) | `work` |
| Spotify listening, Steam play | `play` |
| comms bursts (gmail/telegram/whatsapp/discord), co-presence, `chronicler.situation_inferred` `outing_block` (at an unregistered place) | `social` |
| `owntracks` movement, `chronicler.situation_inferred` `airport_block` | `travel` |
| meals | `eat` |
| `chronicler.situation_inferred` `home_block` (at home with no other activity), idle/at-home presence | `rest` |
| `chronicler.owner_answer` `owner_<lane>_block` (gap-interview answer) | the lane answered |

`owntracks.place_cluster` `place_episode` rows are `evidence`: they say where
the owner was and are counted only through the situational blocks derived from
them.

Existing projected source episodes (sleep, workout, listening, play, work,
movement, meal) ARE the `activity`-layer rows for their lane; Tier-1 projectors
do not re-emit duplicates of them — they add `confidence`/`evidence_refs` and
emit only genuinely *new* inferred activities (e.g. an exercise block inferred
from HR+GPS when no workout episode exists). Where both a source episode and an
inferred candidate cover the same block, day-close reconciliation merges them.

ID: REQ-chronicler-intent-evidence-activity-001
Source: IEA reframe tasks.md §4; bu-whhll.14; bu-r3fmja (situational blocks)
Scope: v1-mandatory

#### Scenario: Music and gaming roll into Play

- **WHEN** Spotify listening and Steam play activities are aggregated
- **THEN** their time appears under the `play` lane
- **AND** neither `music` nor `gaming` appears as a top-level lane

#### Scenario: Every lane has a covering mapping

- **WHEN** an episode of each mapped source type is aggregated
- **THEN** it rolls up under the lane named in the mapping table
- **AND** meals map to `eat`, workouts to `exercise`, and movement to `travel`

#### Scenario: Place-derived blocks count in their own lanes

- **WHEN** situational blocks at the work place, at an unregistered place and at home are aggregated
- **THEN** they roll up under `work`, `social` and `rest` respectively
- **AND** the `place_episode` rows they were derived from add nothing to any lane
