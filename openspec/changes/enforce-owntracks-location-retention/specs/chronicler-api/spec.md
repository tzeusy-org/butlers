## MODIFIED Requirements

### Requirement: Chronicler Response Provenance

- Chronicler API responses SHALL expose enough provenance for the client to understand source evidence, uncertainty, privacy, and correction state.
- For OwnTracks sources, response provenance SHALL additionally expose canonical retention policy version/conditional expiry and typed spatial precision/source-purge receipt state. A returned ID/digest SHALL be a locator/provenance token, never authority or anonymity. Summaries SHALL distinguish prepared/coarsened/raw-ACK-pending from genuinely source-forgotten; no raw geometry may be reconstructed from expired evidence. Existing privacy and participant-set behavior SHALL remain.

ID: REQ-chronicler-api-002
Source: bu-s11n0s.7 original S2/S3; RFC 0014 D4/D7; openspec/specs/chronicler-api/spec.md; proposed location-retention amendment
Scope: v1-mandatory

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

#### Scenario: Chronicler Response Provenance preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** For OwnTracks sources, response provenance SHALL additionally expose canonical retention policy version/conditional expiry and typed spatial precision/source-purge receipt state. A returned ID/digest SHALL be a locator/provenance token, never authority or anonymity. Summaries SHALL distinguish prepared/coarsened/raw-ACK-pending from genuinely source-forgotten; no raw geometry may be reconstructed from expired evidence. Existing privacy and participant-set behavior SHALL remain.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof

### Requirement: Chronicler Corrections

- The API SHALL support owner corrections by creating override or superseding records without mutating source evidence.
- The initial correction endpoint set SHALL include:
- `POST /api/chronicler/episodes/{episode_id}/corrections`
- `POST /api/chronicler/gap-interview/resolve`
- `POST /api/chronicler/gap-interview/resolve` is a connector-facing internal endpoint that applies one one-tap day-close gap-interview answer. It SHALL delegate to the shared gap-interview resolver so its override/reinforce write shape and idempotency are identical to the `chronicler_resolve_gap_interview` MCP tool that calls the same resolver. It exists because the `telegram_bot` connector runs as the restricted `connector_writer` role and cannot write the chronicler schema itself; this endpoint runs with the chronicler pool that can.
- OwnTracks correction writes/history SHALL inherit the permanent source retention/spatial floor. Replayed/upserted or corrected titles/payloads SHALL not restore erased source coordinates, old exact centroids or raw point events; minimal scrubbed audit/provenance shall remain. Explicit independent owner-authored reference/configuration is separate provenance and is not permission to relabel observed raw geometry as a static reference. Genuine nonexpired/unrelated source and valid independent corrections SHALL keep existing behavior.

ID: REQ-chronicler-api-003
Source: bu-s11n0s.7 original S2/S3; RFC 0014 D4/D7; openspec/specs/chronicler-api/spec.md; proposed location-retention amendment
Scope: v1-mandatory

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

#### Scenario: Gap-interview one-tap answer resolved

- **WHEN** a connector (or the `chronicler_resolve_gap_interview` MCP tool)
  submits a one-tap day-close gap-interview answer to
  `POST /api/chronicler/gap-interview/resolve` with an `interview_id` and an
  `answer` of `confirm`, `correct`, or `dismiss`
- **THEN** the API SHALL delegate to the shared gap-interview resolver so the
  override/reinforce write is identical across the HTTP endpoint and the MCP
  tool
- **AND** the resolution SHALL be idempotent — a duplicate tap for an
  already-answered interview SHALL NOT write a second override or re-nudge the
  routine
- **AND** the endpoint SHALL always return HTTP `200` with a `status` field the
  caller can surface as a toast (`applied`, `already_answered`, or `error`)
- **AND** an unknown or expired `interview_id`, or an unparseable `answer`,
  SHALL return `200` with an `error` status rather than raising a server error

#### Scenario: Chronicler Corrections preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** OwnTracks correction writes/history SHALL inherit the permanent source retention/spatial floor. Replayed/upserted or corrected titles/payloads SHALL not restore erased source coordinates, old exact centroids or raw point events; minimal scrubbed audit/provenance shall remain. Explicit independent owner-authored reference/configuration is separate provenance and is not permission to relabel observed raw geometry as a static reference. Genuine nonexpired/unrelated source and valid independent corrections SHALL keep existing behavior.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof

### Requirement: Chronicler Source State Visibility

- The API SHALL expose a read-only endpoint that lists the runtime state of every registered source adapter, so that dashboard surfaces can render disabled-lane affordances and operational diagnostics without querying the database directly.
- The endpoint SHALL be:
- `GET /api/chronicler/source-state`
- The GET-only source-state response SHALL include a typed OwnTracks retention substate composed from actual current policy/attempt/committed receipt and lag counts, while preserving active/read-surface/compatibility/checkpoint semantics. Missing source, missing receipt, stale6h-plus1h completion, busy/incomplete holders, failure and genuine complete no_work SHALL be distinct. No client-supplied state mutation or old-success freshness SHALL manufacture healthy forgetting. Standalone unavailable raw data SHALL not be reported as a complete empty source.

ID: REQ-chronicler-api-007
Source: bu-s11n0s.7 original S2/S3; RFC 0014 D4/D7; openspec/specs/chronicler-api/spec.md; proposed location-retention amendment
Scope: v1-mandatory

#### Scenario: Source state listed with checkpoint diagnostics

- **WHEN** a client requests `GET /api/chronicler/source-state`
- **THEN** the API SHALL return one record per row in
  `chronicler.source_adapter_state`
- **AND** each record SHALL include `source_name`,
  `chronicler_compatibility`, `read_surface`, `boundary_semantics`,
  `optional_schema`, `active`, `inactive_reason`, and the **latest**
  `last_run_at` and `last_error` from `chronicler.projection_checkpoints`
  rows for that `source_name` across all `subsource` values (per-schema
  watermarks per migration `002_per_schema_watermarks.py`); when
  per-subsource detail is relevant, the record MAY include an optional
  `subsource_checkpoints` array enumerating each `(subsource,
  last_run_at, last_error)` tuple
- **AND** the records SHALL be returned in `source_name ASC` order

#### Scenario: Empty source-state on cold boot

- **WHEN** the daemon has just started and `source_adapter_state` has
  not yet been populated by the boot-time seeding
- **THEN** the endpoint SHALL respond `200 OK` with `data: []`
- **AND** it SHALL NOT respond `404` or any error code

#### Scenario: Optional-schema degradation surfaced

- **WHEN** a source has `optional_schema = true` AND its read surface
  is missing in this deployment
- **THEN** the record SHALL show `active = false` AND
  `inactive_reason` containing the specific missing schema or table
- **AND** the dashboard caller SHALL render the corresponding lane as
  disabled with the inactive reason as a tooltip

#### Scenario: Read-only contract

- **WHEN** the endpoint is invoked with any HTTP verb other than `GET`
- **THEN** the API SHALL respond `405 Method Not Allowed`
- **AND** no path on `/api/chronicler/source-state` SHALL accept
  client-supplied state mutations

#### Scenario: Chronicler Source State Visibility preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** The GET-only source-state response SHALL include a typed OwnTracks retention substate composed from actual current policy/attempt/committed receipt and lag counts, while preserving active/read-surface/compatibility/checkpoint semantics. Missing source, missing receipt, stale6h-plus1h completion, busy/incomplete holders, failure and genuine complete no_work SHALL be distinct. No client-supplied state mutation or old-success freshness SHALL manufacture healthy forgetting. Standalone unavailable raw data SHALL not be reported as a complete empty source.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof

### Requirement: Activity Evidence Chain Endpoint

- A read endpoint SHALL return the evidence chain for an activity — each corroborating signal with its source — so a client can answer "why?".
- For a source-forgotten OwnTracks event, evidence-chain SHALL return a typed expired reference/receipt with an opaque event identifier and bounded reduced-evidence descriptor, never its old coordinate-bearing title. Default raw events remain absent, removed FK links SHALL be honest, evidence_refs stays list[str], and expired evidence SHALL not masquerade as fresh corroboration. Existing live evidence links and unrelated activity/privacy behavior SHALL remain.

ID: REQ-chronicler-api-016
Source: bu-s11n0s.7 original S2/S3; RFC 0014 D4/D7; openspec/specs/chronicler-api/spec.md; proposed location-retention amendment
Scope: v1-mandatory

#### Scenario: Evidence chain returned for an activity

- **WHEN** a client requests the evidence chain for an activity id
- **THEN** the response lists each `evidence_ref` with its source name and a
  human-readable descriptor
- **AND** the activity's confidence is included

#### Scenario: Activity Evidence Chain Endpoint preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** For a source-forgotten OwnTracks event, evidence-chain SHALL return a typed expired reference/receipt with an opaque event identifier and bounded reduced-evidence descriptor, never its old coordinate-bearing title. Default raw events remain absent, removed FK links SHALL be honest, evidence_refs stays list[str], and expired evidence SHALL not masquerade as fresh corroboration. Existing live evidence links and unrelated activity/privacy behavior SHALL remain.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof
