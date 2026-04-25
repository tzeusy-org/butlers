# Chronicler API

## Purpose

Defines the backend API contract for Chronicler-owned retrospective time reads
and owner corrections. This capability intentionally does not define dashboard
UX or claim the existing operational `/timeline` route.

## ADDED Requirements

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

#### Scenario: Event response includes source semantics

- **WHEN** an event is returned
- **THEN** the response SHALL include occurred-at timestamp, source channel,
  source provider, event type, source reference, privacy tier, precision policy,
  retention policy, source reference status, and tombstone state

#### Scenario: Episode-event links are available

- **WHEN** a client requests supporting evidence for an episode
- **THEN** the API SHALL expose linked point events with relation semantics such
  as `starts`, `ends`, `supports`, `occurs_during`, or `contradicts`

### Requirement: Chronicler Corrections

The API SHALL support owner corrections by creating override or superseding
records without mutating source evidence.

The initial correction endpoint set SHALL include:

- `POST /api/chronicler/episodes/{episode_id}/corrections`

#### Scenario: Episode correction submitted

- **WHEN** the owner submits a correction for an episode type, title, start time,
  end time, or explanatory note
- **THEN** the API SHALL create an override or superseding derived record
- **AND** it SHALL retain the original source evidence and original derived
  record for audit
- **AND** corrected read views SHALL prefer the active correction
- **AND** the request body SHALL allow only correction fields owned by
  Chronicler: corrected type, title, start time, end time, note, and active
  status

#### Scenario: Correction policy inherited

- **WHEN** a correction is created for an episode or event
- **THEN** the correction record SHALL inherit the stricter effective privacy,
  precision, and retention policy from the target record and source contract

#### Scenario: Correction history returned

- **WHEN** a client requests correction history for an episode
- **THEN** the API SHALL return the ordered correction audit trail without
  exposing source fields that have been tombstoned or precision-reduced

#### Scenario: Invalid correction rejected

- **WHEN** a correction request has invalid timestamps, attempts to change source
  evidence, or violates privacy/retention policy
- **THEN** the API SHALL reject it with a structured `400` response
- **AND** it SHALL NOT create a partial override record

### Requirement: Operational Timeline Route Preserved

The Chronicler API change SHALL NOT repurpose the existing operational
`/timeline` dashboard/API route.

#### Scenario: Chronicler API namespace used

- **WHEN** Chronicler read or correction endpoints are added
- **THEN** they SHALL live under `/api/chronicler/*`
- **AND** any future user-facing timeline route SHALL require a separate
  dashboard/API spec amendment before replacing or reusing the operational
  `/timeline` concept

## Source References

- Non-Negotiable Rule 1 (single-owner data sovereignty)
- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 4 (LLM reasoning is ephemeral)
- RFC 0006 (Database schema isolation)
- RFC 0007 (Dashboard and API Surface)
- RFC 0010 (Cross-Butler Briefing Exception)
- RFC 0014 (Chronicler Time Butler, Draft)
