# Chronicler Butler

## ADDED Requirements

### Requirement: Butler Identity and Wire Contract

The Chronicler butler SHALL expose a stable identity as a domain butler (not
a staffer). Identity details that may drift (model, cron minutes, module list,
concurrency caps) are owned by `roster/chronicler/butler.toml` per
`about/heart-and-soul/vision.md` Rule 5 and are NOT mirrored here.

#### Scenario: Stable wire identity

- **WHEN** the butler is loaded from `roster/chronicler/butler.toml`
- **THEN** it SHALL have `name = "chronicler"`
- **AND** its database binding SHALL be `[butler.db]` with `name = "butlers"` and `schema = "chronicler"`
- **AND** its runtime role SHALL resolve to `butler_chronicler_rw`

#### Scenario: Domain butler type, not staffer

- **WHEN** the daemon classifies the Chronicler butler
- **THEN** it SHALL be a butler-typed agent eligible for routing from the
  Switchboard as a retrospective-time specialist
- **AND** it SHALL NOT be a staffer (it has no ingress routing authority,
  no connector ownership, and no infrastructure role)

### Requirement: Retrospective-Only Scope

The Chronicler butler SHALL reconstruct past time from already-captured
evidence and SHALL NOT plan, schedule, dispatch, ingest externally, or notify.

#### Scenario: No ingestion ownership

- **WHEN** a passive timestamped event arrives (Spotify playback change,
  Steam game start, OwnTracks point, Home Assistant state change, Google
  Health reading)
- **THEN** Chronicler SHALL NOT receive the event directly
- **AND** the event SHALL route to its owning domain butler per existing
  Switchboard rules
- **AND** Chronicler's projection of that event SHALL run asynchronously
  on schedule, reading only from the owning domain's approved read surface

#### Scenario: No scheduling or planning

- **WHEN** a user request asks "schedule X" or "plan Y"
- **THEN** the Switchboard SHALL NOT route the request to Chronicler
- **AND** Chronicler's tool surface SHALL NOT include scheduling tools

### Requirement: Storage Shape

The Chronicler schema SHALL contain point events, episodes, episode-event
links, overrides, projection checkpoints, source adapter state, and
idempotency keys.

#### Scenario: Point events and episodes separated

- **WHEN** a source adapter projects evidence
- **THEN** instantaneous evidence SHALL be written to `point_events`
- **AND** span-shaped evidence SHALL be written to `episodes`
- **AND** every row SHALL carry `source_name`, `source_ref`, `precision`,
  `privacy`, `retention_days`, and optional `tombstone_at`

#### Scenario: Overlapping episodes permitted

- **WHEN** two episodes from different sources cover overlapping time
- **THEN** both SHALL be stored
- **AND** neither SHALL be merged or discarded
- **AND** overlap queries SHALL return both

#### Scenario: Idempotent replay

- **WHEN** a source adapter re-projects the same source record
- **THEN** the stored row SHALL be updated in place via its
  `(source_name, source_ref)` idempotency key
- **AND** no duplicate row SHALL be created

### Requirement: Correction Overlay Model

User corrections SHALL layer on top of canonical projections via an
`overrides` table without mutating or deleting the canonical rows.

#### Scenario: Correction preserves canonical row

- **WHEN** a user submits a correction for an episode
- **THEN** the canonical episode row SHALL remain unchanged
- **AND** an `overrides` row SHALL reference the canonical row with the
  corrected fields
- **AND** the corrected view SHALL surface the effective values

#### Scenario: Later override wins

- **WHEN** a user submits a second correction for the same episode
- **THEN** the later override SHALL apply on top of the earlier override
- **AND** correction history SHALL expose both via
  `GET /api/chronicler/episodes/{id}/corrections`

### Requirement: Source Compatibility Declarations

Every source adapter SHALL declare its chronicler compatibility status
before projection runs against it.

#### Scenario: Initial supported sources

- **WHEN** Chronicler starts for the first time
- **THEN** `core.sessions` SHALL be declared `supported`
- **AND** `google_calendar.completed` SHALL be declared `supported`
- **AND** `spotify.session_summary` SHALL be declared `deferred` unless a
  durable evidence surface exists
- **AND** `google_health.*` SHALL be declared `deferred`

#### Scenario: Optional schema guard

- **WHEN** a source adapter's optional schema is missing on the current
  deployment
- **THEN** the adapter SHALL mark its state as inactive with the missing-
  schema reason
- **AND** SHALL NOT raise an unhandled exception
- **AND** the missing-schema state SHALL be visible via
  `source_adapter_state`

### Requirement: No Per-Event LLM Invocation

Chronicler projection adapters SHALL NOT invoke an LLM on a per-event basis.

#### Scenario: Projection adapter path forbids LLM

- **WHEN** a projection adapter runs under its scheduled job
- **THEN** it SHALL NOT make any LLM call
- **AND** guardrail tests SHALL assert the no-LLM invariant

#### Scenario: Bounded Tier 2 interpretation

- **WHEN** a Tier 2 interpretation path runs (day-close, drilldown,
  ambiguity resolution, correction assistance)
- **THEN** its input bundle SHALL be bounded to a fixed maximum size
- **AND** the call SHALL preserve provenance in output
- **AND** the call SHALL be a single LLM invocation, not a fan-out over
  events

### Requirement: API Surface Distinct from /api/timeline

Chronicler SHALL expose its read and correction API under `/api/chronicler/*`.
The existing `/api/timeline` route SHALL remain the operational cross-butler
session/notification stream.

#### Scenario: Distinct namespace

- **WHEN** a request arrives at `/api/timeline`
- **THEN** it SHALL continue to return aggregated session + notification
  events across all butlers
- **AND** Chronicler SHALL NOT claim or modify this route

#### Scenario: Chronicler endpoints

- **WHEN** a request arrives at `/api/chronicler/events`, `/api/chronicler/episodes`,
  `/api/chronicler/episodes/{id}`, `/api/chronicler/episodes/{id}/events`,
  or `/api/chronicler/episodes/{id}/corrections`
- **THEN** the Chronicler-owned router SHALL handle it
- **AND** responses SHALL carry provenance fields (source_name, source_ref,
  precision, privacy)

### Requirement: Switchboard Routing Boundary

Switchboard routing SHALL route only explicit retrospective time-review
requests to Chronicler.

#### Scenario: Explicit retrospective routes to Chronicler

- **WHEN** a user asks "what did I do yesterday?", "how much time did I
  spend listening to music?", "when did I last go running?"
- **THEN** the Switchboard SHALL route the message to Chronicler

#### Scenario: Domain-next-action stays with owning butler

- **WHEN** a user asks "recommend me music"
- **THEN** the message SHALL route to Lifestyle, NOT Chronicler

#### Scenario: Scheduling intent stays with calendar-capable butler

- **WHEN** a user asks "schedule a meeting"
- **THEN** the message SHALL route to a calendar-capable butler, NOT
  Chronicler

#### Scenario: Passive event routes to owning butler

- **WHEN** a Spotify/Steam/OwnTracks/Google-Health event is ingested
- **THEN** it SHALL route to its owning domain butler, NOT Chronicler

## Source References

- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 5 (butler.toml is identity; specs describe behavior)
- RFC 0006 (database schema isolation)
- RFC 0009 (situational context bus — Chronicler reads but does not write)
- RFC 0010 (cross-butler read surface precedent)
- RFC 0014 (Chronicler retrospective time butler — full normative contract)
