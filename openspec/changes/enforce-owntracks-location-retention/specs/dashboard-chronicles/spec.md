## MODIFIED Requirements

### Requirement: Map Render Privacy Contract

- The map widget and Gantt swimlane SHALL enforce privacy and tombstone rules at render time. Default API parameters SHALL produce a privacy-safe view; the frontend SHALL NOT relax defaults without an explicit user-toggle gated by the `Per-Recipient Masking Toggle` requirement.
- The classification of a row as `sensitive` is a source-level decision made by the projection adapter — it does NOT imply that the dashboard viewer is untrusted. Per the owner-view doctrine in `about/heart-and-soul/security.md` L168–185, the Butlers instance has a single trusted viewer (the owner) and "the system does not apply differential privacy, anonymization, or special-purpose encryption to any data category." Adapters SHOULD therefore default to `privacy=normal` for owner-originated data; the `sensitive` tier exists for rows whose payload masks make sense for shared, screenshot, or third-party views once the per-recipient toggle is implemented.
- OwnTracks expiry SHALL be enforced by owning projection/storage and source retention, not a separate frontend age filter. For a genuine completed source-forgetting range, raw trail/heatmap/playhead inputs SHALL be empty while reduced legitimate legs/visits survive. If projection/copy lag blocks forgetting, the source strip/plaque SHALL display the true conditional pending state, never fake completion. Server policy/freshness and strict privacy/tombstone defaults govern cache/map invalidation; source-derived exact titles/carryovers/caches SHALL not reintroduce geometry.

ID: REQ-dashboard-chronicles-009
Source: bu-s11n0s.7 original S3 and map acceptance criterion; RFC 0014 D7; openspec/specs/dashboard-chronicles/spec.md; proposed location-retention amendment
Scope: v1-mandatory

#### Scenario: Restricted episodes excluded entirely

- **WHEN** an episode or point event has `privacy_tier = restricted`
- **THEN** the page SHALL NOT render it on the Gantt or the map
- **AND** the underlying API request SHALL omit `restricted` from the
  `privacy_tier` query parameter unless explicitly overridden
- **AND** this default-exclusion of `restricted` SHALL apply to the
  page's calls to existing `Chronicler Temporal Reads` endpoints
  (`/api/chronicler/episodes`, `/api/chronicler/events`) as well as the
  new aggregate endpoints, even though the upstream `Chronicler Temporal
  Reads` Requirement does not impose this default at the API layer

#### Scenario: Sensitive episodes masked

- **WHEN** an episode has `privacy_tier = sensitive`
- **AND** the dashboard is rendering for the owner with no per-recipient
  masking toggle engaged
- **THEN** the Gantt SHALL render the lane bar as a generic masked
  entry (no title, no payload contents)
- **AND** the map SHALL NOT plot any coordinates derived from that
  episode or its linked point events
- **AND** the spec MAKES NO CLAIM about which adapters emit `sensitive`
  rows by default — that decision lives with each projection adapter
  per the owner-view doctrine. As of `core_086`, no in-tree adapter
  defaults to `sensitive`; rows reach this tier only via per-row
  corrections or future adapter changes.

#### Scenario: Tombstoned data excluded by default

- **WHEN** the page issues an aggregate, episode, or point-event
  request
- **THEN** it SHALL omit `include_tombstoned` (default `false`) so
  that tombstoned rows are excluded
- **AND** any future operator-visible "show tombstoned" toggle SHALL
  surface a clear visual indicator that tombstoned data is rendered

#### Scenario: Retention enforcement is upstream

- **WHEN** retention windows expire on a source (e.g. OwnTracks
  default 30-day retention per `about/heart-and-soul/security.md`
  L172–175)
- **THEN** the projection adapter and storage layer SHALL drop expired
  rows
- **AND** the map widget SHALL NOT add a separate retention filter

#### Scenario: Map Render Privacy Contract preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** OwnTracks expiry SHALL be enforced by owning projection/storage and source retention, not a separate frontend age filter. For a genuine completed source-forgetting range, raw trail/heatmap/playhead inputs SHALL be empty while reduced legitimate legs/visits survive. If projection/copy lag blocks forgetting, the source strip/plaque SHALL display the true conditional pending state, never fake completion. Server policy/freshness and strict privacy/tombstone defaults govern cache/map invalidation; source-derived exact titles/carryovers/caches SHALL not reintroduce geometry.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof

### Requirement: Where-You-Went Map Trail

- The day view SHALL render the day's movement as a map trail, subject to the existing map privacy contract.
- The map SHALL show its actual canonical policy: Exact trail expires after N days once projection completes, lower-precision summaries remain, increasing this window does not restore forgotten points and already-prepared decisions may finish. The protected versioned setter SHALL accept only actual server-validated policy, visibly retain unknown/failed/lag states and update current/archive contexts. The raw trail SHALL use actual raw API events only; no interpolation through coarsened endpoints may invent purged exact travel. Cache invalidation SHALL remove old markers/trail/heatmap/playhead after actual commits.

ID: REQ-dashboard-chronicles-019
Source: bu-s11n0s.7 original S3 and map acceptance criterion; RFC 0014 D7; openspec/specs/dashboard-chronicles/spec.md; proposed location-retention amendment
Scope: v1-mandatory

#### Scenario: Movement rendered as a trail

- **WHEN** the day has movement evidence and the map privacy contract permits
- **THEN** a trail of the day's locations is rendered

#### Scenario: Where-You-Went Map Trail preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** The map SHALL show its actual canonical policy: Exact trail expires after N days once projection completes, lower-precision summaries remain, increasing this window does not restore forgotten points and already-prepared decisions may finish. The protected versioned setter SHALL accept only actual server-validated policy, visibly retain unknown/failed/lag states and update current/archive contexts. The raw trail SHALL use actual raw API events only; no interpolation through coarsened endpoints may invent purged exact travel. Cache invalidation SHALL remove old markers/trail/heatmap/playhead after actual commits.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof
