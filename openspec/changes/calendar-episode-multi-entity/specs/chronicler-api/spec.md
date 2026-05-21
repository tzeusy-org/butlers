## MODIFIED Requirements

### Requirement: Chronicler Temporal Reads

The API SHALL expose Chronicler-owned read endpoints under `/api/chronicler/*`
for retrospective event and episode queries.

The initial endpoint set SHALL include:

- `GET /api/chronicler/events`
- `GET /api/chronicler/episodes`
- `GET /api/chronicler/episodes/{episode_id}`
- `GET /api/chronicler/episodes/{episode_id}/events`
- `GET /api/chronicler/episodes/{episode_id}/corrections`

#### Scenario: Events queried by time range

- **WHEN** a client requests Chronicler point events for a time range
- **THEN** the API SHALL read from Chronicler-owned derived event tables
- **AND** it SHALL support filtering by source, event type, confidence where
  applicable, privacy tier, source reference status, and tombstone state
- **AND** it SHALL accept `start_at`, `end_at`, `source`, `event_type`,
  `privacy_tier`, `source_ref_status`, `include_tombstoned`, `limit`, and
  `cursor` query parameters
- **AND** it SHALL return stable pagination metadata with `items`,
  `next_cursor`, and `has_more`

#### Scenario: Episodes queried by time range

- **WHEN** a client requests Chronicler episodes for a time range
- **THEN** the API SHALL include episodes whose intervals overlap the requested
  range, including open episodes where `ended_at = NULL`
- **AND** it SHALL support filtering by source, episode type, confidence,
  privacy tier, source reference status, and tombstone state
- **AND** it SHALL preserve overlapping episodes rather than collapsing them
  into one primary activity
- **AND** it SHALL accept `start_at`, `end_at`, `source`, `episode_type`,
  `min_confidence`, `privacy_tier`, `source_ref_status`, `include_open`,
  `include_tombstoned`, `limit`, and `cursor` query parameters

#### Scenario: Episodes filtered by participant entity

- **WHEN** a client requests `/api/chronicler/episodes?participant_entity_id=<uuid>`
- **THEN** the API SHALL return episodes that have at least one row in
  `chronicler.episode_entities` referencing the supplied `entity_id`,
  regardless of the role (`'owner'`, `'organizer'`, or `'participant'`)
- **AND** the response SHALL preserve the existing pagination shape
  (`items`, `next_cursor`, `has_more`)
- **AND** the API SHALL also continue to accept the existing
  `entity_id=<uuid>` parameter, which SHALL be interpreted as an
  owner-only filter against the derived `chronicler.episodes.entity_id`
  column during the transition window
- **AND** the API SHALL NOT require both parameters; passing both SHALL
  evaluate the participant-join filter and apply the owner-column
  filter as a conjunctive constraint

#### Scenario: Episode detail queried

- **WHEN** a client requests `GET /api/chronicler/episodes/{episode_id}`
- **THEN** the API SHALL return the corrected current episode view plus original
  derived identifiers, active override status, and source provenance
- **AND** it SHALL return `404` when the episode is not visible or does not
  exist

#### Scenario: Interactive reads stay inside Chronicler

- **WHEN** the API answers a Chronicler read request
- **THEN** it SHALL read Chronicler-owned derived events, episodes, links, and
  overrides
- **AND** it SHALL NOT issue arbitrary direct cross-schema reads against source
  butler tables

### Requirement: Chronicler Response Provenance

Chronicler API responses SHALL expose enough provenance for the client to
understand source evidence, uncertainty, privacy, and correction state.

#### Scenario: Episode response includes boundary semantics

- **WHEN** an episode is returned
- **THEN** the response SHALL include start and end boundary provenance,
  confidence, source references, source reference status, privacy tier,
  precision policy, retention policy, tombstone state, and active override
  status where present

#### Scenario: Episode response includes participant entity set

- **WHEN** an episode is returned by any Chronicler read endpoint
  (`GET /api/chronicler/episodes`,
  `GET /api/chronicler/episodes/{id}`, the entity-activity aggregator,
  or any future endpoint that surfaces episode rows)
- **THEN** the episode object SHALL include a `participant_entity_ids`
  field of type `array<uuid>`
- **AND** the array SHALL be the aggregated set of `entity_id` values
  from `chronicler.episode_entities` for the episode, sorted by
  role precedence (`'owner'` first, then `'organizer'`, then
  `'participant'`) with `entity_id ASC` as the deterministic tiebreak
  within a role bucket
- **AND** the array SHALL be empty (`[]`, never null) when no
  participants are linked
- **AND** the existing `entity_id` field SHALL continue to be returned
  during the transition window, equal to the owner participant when
  present, else `null`
- **AND** the array SHALL NOT include unresolved attendees (attendees
  for whom the upstream calendar module did not resolve a
  `public.entities` row)

#### Scenario: Event response includes source semantics

- **WHEN** an event is returned
- **THEN** the response SHALL include occurred-at timestamp, source channel,
  source provider, event type, source reference, privacy tier, precision policy,
  retention policy, source reference status, and tombstone state

#### Scenario: Episode-event links are available

- **WHEN** a client requests supporting evidence for an episode
- **THEN** the API SHALL expose linked point events with relation semantics such
  as `starts`, `ends`, `supports`, `occurs_during`, or `contradicts`

## ADDED Requirements

### Requirement: Episode Participant Resolution Read Path

The API SHALL expose participant entity membership as a first-class
provenance facet of every episode response. This is the read side of the
chronicler `episode_entities` storage shape introduced in
`butler-chronicler`.

#### Scenario: Aggregator endpoints surface participants

- **WHEN** any chronicler aggregator endpoint
  (`/api/chronicler/aggregate/by-category`,
  `/api/chronicler/aggregate/by-day`,
  `/api/chronicler/aggregate/day-close`) includes per-source breakdowns
  or per-episode citations
- **THEN** episode-level citation entries MAY include the
  `participant_entity_ids` array but SHALL NOT include resolved
  participant display names or any other personally identifying
  information beyond the UUID set
- **BECAUSE** display-name resolution is the relationship butler's
  responsibility; chronicler returns stable IDs only

#### Scenario: Privacy-tier filtering preserved

- **WHEN** an episode is filtered out by privacy semantics (the
  `restricted` server-side filter, or `sensitive` payload masking)
- **THEN** the `participant_entity_ids` array SHALL be omitted from the
  response together with the rest of the masked payload
- **AND** `restricted` episodes SHALL remain invisible regardless of
  the participant set; multi-entity tagging SHALL NOT create a side
  channel that reveals restricted episodes to participants

#### Scenario: Cursor-paginated participant filter is deterministic

- **WHEN** `/api/chronicler/episodes?participant_entity_id=<uuid>` is
  paginated via `cursor`
- **THEN** the underlying ORDER BY SHALL remain `start_at DESC, id DESC`
  (matching the existing list contract) so that adding the join does
  not destabilize pagination

## Source References

- Non-Negotiable Rule 1 (single-owner data sovereignty)
- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 4 (LLM reasoning is ephemeral)
- RFC 0006 (Database schema isolation)
- RFC 0007 (Dashboard and API Surface)
- RFC 0010 (Cross-Butler Briefing Exception)
- RFC 0014 (Chronicler Time Butler) §D7 API Surface
