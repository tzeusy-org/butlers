## ADDED Requirements

### Requirement: Chronicles episode drawer has a stable query door

The canonical `/chronicles` route SHALL accept an optional `episode` query
parameter containing a Chronicler episode ID. When present with a valid
`date=YYYY-MM-DD`, the existing date behavior MUST continue to select the day
and the existing `EpisodeDrawer` MUST open for the exact episode ID. Closing
the drawer MUST remove only `episode` and preserve `date` plus every unrelated
query pair.

An absent `episode` parameter MUST leave all existing date-only behavior
unchanged. An episode that is missing, unavailable, or unreadable MUST use the
existing drawer error state without replacing or hiding the selected day. This
door MUST NOT create a `/chronicles/episodes/{id}` route, duplicate the episode
detail component, write a correction, or invoke an LLM merely by opening.

#### Scenario: Shared Chronicle link opens the exact episode and date

- **WHEN** a user follows `/chronicles?date=2026-09-05&episode=65e9b763-5e57-4e48-90a8-1e49f451789b`
- **THEN** the Chronicles page MUST select `2026-09-05`
- **AND** the existing EpisodeDrawer MUST open for episode `65e9b763-5e57-4e48-90a8-1e49f451789b`

#### Scenario: Closing the episode door preserves surrounding context

- **WHEN** an open episode drawer at `/chronicles?date=2026-09-05&episode=episode-id&lens=week` is closed
- **THEN** the resulting query MUST retain `date=2026-09-05&lens=week`
- **AND** only the `episode` pair MUST be removed

#### Scenario: Existing date links are compatible

- **WHEN** `/chronicles` or `/chronicles?date=2026-09-05` is opened without `episode`
- **THEN** the current default-day or selected-day behavior MUST remain unchanged
- **AND** no episode drawer MUST open automatically

#### Scenario: Unavailable episode stays inside the drawer boundary

- **WHEN** the episode query names an episode whose detail read fails or returns not found
- **THEN** the selected day MUST remain visible and usable
- **AND** the drawer MUST render its existing failure state without fabricated episode content
- **AND** no correction, LLM, or other write path MUST run
