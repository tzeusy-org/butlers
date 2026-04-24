# Butler Chronicler

## Purpose

Chronicler reconstructs the owner's lived/past time from persisted evidence
streams. It maintains a derived model of overlapping episodes and point events,
preserves source provenance and boundary uncertainty, supports owner correction,
and answers explicit retrospective time-review requests.

## ADDED Requirements

### Requirement: Chronicler Butler Identity

The system SHALL define Chronicler as a domain butler responsible for
retrospective lived-time reconstruction.

#### Scenario: Chronicler is a domain butler

- **WHEN** the roster is loaded
- **THEN** Chronicler is registered as a butler, not a staffer
- **AND** Chronicler is eligible for explicit user-message routing
- **AND** Chronicler is governed by a manifesto describing its retrospective time domain

#### Scenario: Chronicler does not own infrastructure routing

- **WHEN** Chronicler runs
- **THEN** it SHALL NOT route messages, broker notifications, own connector lifecycle, or enforce ingestion policy

### Requirement: Retrospective Scope

Chronicler SHALL model lived/past time only. This change SHALL be treated as a
proposed scope expansion until Heart and Soul v1 scope is explicitly amended.

#### Scenario: Completed calendar event eligible

- **WHEN** a calendar event's end time is before the projection job's evaluation time
- **THEN** Chronicler MAY project it as evidence or an episode

#### Scenario: Future calendar event excluded

- **WHEN** a calendar event's start or end time is in the future
- **THEN** Chronicler SHALL NOT project it as a lived-time episode

#### Scenario: Open current evidence remains provisional

- **WHEN** source evidence indicates an activity may still be ongoing
- **THEN** Chronicler MAY keep the episode open with `ended_at = NULL`
- **AND** the episode SHALL NOT be treated as finalized until source-specific closure rules resolve it

### Requirement: Point Events

Chronicler SHALL store point-in-time events as first-class temporal records.

#### Scenario: Point event projected

- **WHEN** a source adapter observes a timestamped fact such as an email arrival, track change, achievement unlock, or butler session start
- **THEN** Chronicler SHALL upsert a point event with `occurred_at`, source channel/provider, event type, title or summary, source reference, and metadata

#### Scenario: Point event idempotency

- **WHEN** the same source point event is projected more than once
- **THEN** Chronicler SHALL update or no-op the existing event via a stable idempotency key
- **AND** it SHALL NOT create duplicate records

### Requirement: Overlapping Episodes

Chronicler SHALL store bounded or open interval episodes and SHALL allow episodes to overlap.

#### Scenario: Overlapping episodes allowed

- **WHEN** a Spotify listening episode overlaps an OwnTracks commute episode
- **THEN** both episodes SHALL be stored independently
- **AND** neither episode SHALL overwrite, truncate, or suppress the other

#### Scenario: No forced primary activity

- **WHEN** multiple episodes overlap in the same time range
- **THEN** Chronicler SHALL NOT assign a single primary activity during projection
- **AND** any primary-activity label SHALL be a later presentation or summary choice with provenance preserved

### Requirement: Episode Boundary Provenance

Chronicler SHALL record boundary provenance for every episode.

#### Scenario: Explicit boundary recorded

- **WHEN** a source provides a reliable start or end timestamp
- **THEN** Chronicler SHALL mark the corresponding boundary as `explicit`

#### Scenario: Inferred boundary recorded

- **WHEN** Chronicler infers a start or end from polling gaps, inactivity thresholds, or later source observations
- **THEN** Chronicler SHALL mark the boundary as `inferred` or `timeout` as appropriate
- **AND** the episode SHALL include confidence reflecting the weaker evidence

#### Scenario: Unknown end allowed

- **WHEN** an activity has started but no closure evidence exists
- **THEN** Chronicler SHALL allow `ended_at = NULL`
- **AND** mark `end_boundary = "unknown"`

### Requirement: Episode-Event Links

Chronicler SHALL link point events to episodes when the relationship is known or inferred.

#### Scenario: Event occurs during episode

- **WHEN** a point event timestamp falls inside an episode interval
- **THEN** Chronicler MAY link the event to the episode with relation `occurs_during`

#### Scenario: Event supports episode

- **WHEN** a point event contributes evidence for an episode's existence or boundary
- **THEN** Chronicler SHALL link it with relation `supports`, `starts`, or `ends`

#### Scenario: Event contradicts episode

- **WHEN** a point event provides evidence against an episode's inferred interpretation
- **THEN** Chronicler MAY link it with relation `contradicts`

### Requirement: Sparse Interpretation Cost Boundary

Chronicler SHALL use deterministic projection by default and SHALL NOT invoke an LLM for every ingestion event.

#### Scenario: Tier 0 direct projection

- **WHEN** a typed source event already contains enough temporal structure
- **THEN** Chronicler SHALL project it without invoking an LLM

#### Scenario: Tier 1 deterministic aggregation

- **WHEN** source records can be grouped by time, source, thread, place, or account using deterministic rules
- **THEN** Chronicler SHALL aggregate them without invoking an LLM

#### Scenario: Tier 2 sparse interpretation

- **WHEN** the user requests a drilldown, a day-close summary runs, an ambiguity needs resolution, or a correction requires assistance
- **THEN** Chronicler MAY invoke an LLM
- **AND** the invocation SHALL operate on a bounded batch, not on the full ingestion stream

### Requirement: Switchboard Routing Boundary

Switchboard SHALL route to Chronicler only for explicit time-review requests.

#### Scenario: Explicit time-review request routed

- **WHEN** the user asks "what did I do yesterday afternoon?"
- **THEN** Switchboard SHALL route the request to Chronicler

#### Scenario: Passive Spotify event not routed by classification

- **WHEN** a Spotify playback event enters the system
- **THEN** Switchboard SHALL NOT route it to Chronicler merely because it is timestamped
- **AND** Chronicler SHALL consume it later through projection

#### Scenario: Domain overlap preserved

- **WHEN** a Steam play session is relevant to Lifestyle and Chronicler
- **THEN** Lifestyle MAY receive or use the activity as hobby/taste evidence
- **AND** Chronicler SHALL project it as temporal evidence without competing for the same classification decision

### Requirement: Corrections and Overrides

Chronicler SHALL support owner corrections without mutating source evidence.

#### Scenario: Episode type corrected

- **WHEN** the owner corrects an episode from `mobility.commute` to `mobility.walking_to_lunch`
- **THEN** Chronicler SHALL create an override or superseding derived episode
- **AND** the original source evidence SHALL remain unchanged and queryable

#### Scenario: Episode boundary corrected

- **WHEN** the owner corrects an episode end time
- **THEN** corrected views SHALL use the override value
- **AND** provenance SHALL indicate that the boundary was owner-corrected

#### Scenario: Correction history retained

- **WHEN** multiple corrections are applied
- **THEN** Chronicler SHALL retain correction history with timestamps and notes
- **AND** override records SHALL inherit the stricter of the target episode/source privacy, precision, and retention policies

### Requirement: Initial Source Adapters

Chronicler's initial release SHALL include deterministic projection adapters only for sources with durable time evidence contracts.

#### Scenario: Core sessions projected

- **WHEN** canonical butler/agent session records are available
- **THEN** Chronicler SHALL project session lifecycle events and work episodes
- **AND** Chronicler SHALL NOT depend on TTL diagnostic process logs as source truth

#### Scenario: Completed calendar instance projected

- **WHEN** a calendar instance has `ends_at < evaluation_time`
- **AND** its status is not cancelled
- **THEN** Chronicler MAY project it as a completed scheduled block with confidence and source metadata
- **AND** provider-duplicated projections SHALL be deduplicated using the calendar workspace deduplication strategy

#### Scenario: Spotify session summary projected

- **WHEN** durable Spotify session-summary evidence is available with start and end semantics
- **THEN** Chronicler SHALL project a listening episode with source references and boundary provenance

#### Scenario: Deferred source lacks contract

- **WHEN** Steam, OwnTracks, communication-burst, fine-grained Spotify track, Home Assistant, live-listener, wellness/Google Health, or Fitbit-like evidence lacks a Chronicler compatibility contract
- **THEN** Chronicler SHALL NOT claim deterministic projection support for that source

### Requirement: Evidence Access Topology

Chronicler SHALL import source evidence only through deterministic projection jobs using canonical evidence records or migration-tracked read-only views/grants.

#### Scenario: Projection job reads through approved surface

- **WHEN** a Chronicler source adapter reads source evidence outside the Chronicler schema
- **THEN** it SHALL read through a public canonical evidence contract or a migration-created read-only view/grant
- **AND** optional source tables SHALL be guarded with `to_regclass(...)` or equivalent schema-existence checks

#### Scenario: Source read surface exists before adapter activation

- **WHEN** a source adapter is enabled for core sessions, completed calendar
  instances, Spotify summaries, or any future source
- **THEN** its durable source table, canonical evidence record, or
  migration-created read-only view/grant SHALL exist before the adapter is
  allowed to project records
- **AND** missing optional source schemas SHALL degrade the adapter to skipped or
  stale state rather than failing Chronicler startup

#### Scenario: Interactive requests read Chronicler tables only

- **WHEN** a Chronicler API request, dashboard request, or LLM session answers a retrospective question
- **THEN** it SHALL read Chronicler-owned derived events, episodes, links, and overrides
- **AND** it SHALL NOT run arbitrary direct cross-schema queries against source butler schemas

#### Scenario: Ingestion events are lineage only

- **WHEN** a projection adapter needs semantic source facts such as coordinates, track names, or play deltas
- **THEN** it SHALL NOT treat `public.ingestion_events` alone as sufficient evidence
- **AND** it SHALL name the actual durable source table, raw payload store, or canonical temporal evidence record used for projection

#### Scenario: Bootstrap grants exist before roster startup

- **WHEN** Chronicler is added to the runtime roster
- **THEN** the deployment bootstrap SHALL have created the `chronicler` schema,
  `butler_chronicler_rw` runtime role, managed-schema registration, and required
  schema/table/default privileges
- **AND** tests SHALL verify the daemon can set the Chronicler runtime role

### Requirement: Projection Checkpoints and Adapter State

Chronicler SHALL track deterministic projection progress and adapter health per source adapter.

#### Scenario: Adapter checkpoint persisted after successful projection

- **WHEN** a source adapter completes a projection batch
- **THEN** Chronicler SHALL persist adapter name, source surface identity, checkpoint cursor or high-water mark, last successful run timestamp, projected record counts, and status
- **AND** subsequent runs SHALL resume from the persisted checkpoint when source semantics allow incremental projection

#### Scenario: Adapter failure preserves retryable state

- **WHEN** a source adapter fails during projection
- **THEN** Chronicler SHALL record failure status, error class/message, failure timestamp, and retry metadata
- **AND** it SHALL NOT advance the checkpoint past unprojected source evidence

#### Scenario: Adapter staleness observable

- **WHEN** an adapter has not completed projection within its expected freshness window
- **THEN** Chronicler SHALL expose stale or failed adapter state to operator-facing status surfaces
- **AND** API responses MAY indicate incomplete source coverage for affected time ranges

### Requirement: Privacy and Retention Inheritance

Chronicler SHALL preserve source privacy and retention constraints in derived temporal records.

#### Scenario: Projected record carries privacy metadata

- **WHEN** Chronicler projects an event or episode
- **THEN** the derived record SHALL include privacy tier, source reference, and retention/precision metadata

#### Scenario: Sensitive source retention respected

- **WHEN** source evidence is sensitive, such as location or communication metadata
- **THEN** Chronicler SHALL NOT retain derived records at higher precision or for longer duration than the source contract permits unless an explicit Chronicler retention policy is accepted

#### Scenario: Source evidence purged

- **WHEN** source evidence is purged or becomes non-dereferenceable
- **THEN** Chronicler SHALL either tombstone affected source references or retain only lower-precision derived fields allowed by the source privacy policy

#### Scenario: Correction override purged or tombstoned

- **WHEN** an episode is tombstoned or lower-precision-retained due to source retention policy
- **THEN** related correction override notes and corrected fields SHALL be tombstoned or reduced under the same effective policy

### Requirement: Chronicler Compatibility Notes for Future Sources

The specification workflow SHALL require future timestamped source proposals to
declare Chronicler compatibility or explicitly state why the source has no
time-bearing evidence.

#### Scenario: New timestamped source declares compatibility

- **WHEN** a new source capability such as Fitbit is proposed
- **THEN** its proposal/spec SHALL include a Chronicler compatibility note or an explicit "not time-bearing" statement
- **AND** the compatibility note SHALL define source name, source kind, supported outputs, time fields, boundary semantics, source reference format, taxonomy mapping, confidence semantics, privacy tier, idempotency key, and projection path

#### Scenario: Compatible source can be consumed without LLM interpretation

- **WHEN** a source declares a compatible evidence contract
- **THEN** Chronicler SHALL be able to project its evidence through a deterministic adapter
- **AND** a canonical evidence path SHALL require a separate RFC/spec before use
- **AND** routine projection SHALL NOT require LLM interpretation

#### Scenario: Source truth remains owned by source domain

- **WHEN** a source such as Fitbit provides health activity evidence
- **THEN** the source or Health butler SHALL remain the owner of health truth
- **AND** Chronicler SHALL own only the lived-time projection derived from that evidence

## Source References

- Non-Negotiable Rule 1 (single-owner data sovereignty)
- Non-Negotiable Rule 2 (modules only add tools)
- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 6 (manifestos govern scope)
- Non-Negotiable Rule 7 (transport is connector responsibility)
- RFC 0006 (Database schema isolation)
- RFC 0007 (Dashboard and API Surface)
- RFC 0009 (Situational Context Bus)
- RFC 0010 (Cross-Butler Briefing Exception)
- RFC 0014 (Chronicler Time Butler, Draft)
