## MODIFIED Requirements

### Requirement: Map Render Privacy Contract

The map widget and Gantt swimlane SHALL enforce privacy and tombstone
rules at render time. Default API parameters SHALL produce a
privacy-safe view; the frontend SHALL NOT relax defaults without an
explicit user-toggle gated by the `Per-Recipient Masking Toggle`
requirement.

The classification of a row as `sensitive` is a source-level decision
made by the projection adapter — it does NOT imply that the dashboard
viewer is untrusted. Per the owner-view doctrine in
`about/heart-and-soul/security.md` L168–185, the Butlers instance has
a single trusted viewer (the owner) and "the system does not apply
differential privacy, anonymization, or special-purpose encryption to
any data category." Adapters SHOULD therefore default to
`privacy=normal` for owner-originated data; the `sensitive` tier exists
for rows whose payload masks make sense for shared, screenshot, or
third-party views once the per-recipient toggle is implemented.

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

## ADDED Requirements

### Requirement: Per-Recipient Masking Toggle

The Chronicles dashboard SHALL gate the relaxation of `sensitive`-tier
masking on an explicit viewer-context signal. The default rendering
posture SHALL be fail-safe-closed: in the absence of a viewer-context
that identifies the viewer as the owner, all `sensitive`-tier episodes
and their derived map coordinates SHALL be rendered as masked
envelopes per the `Sensitive episodes masked` scenario.

This requirement is forward-looking. The current dashboard runs only
for the owner behind session-cookie auth, so today the viewer is
unconditionally the owner and `sensitive` masking is effectively
inactive (because no in-tree adapter emits `sensitive` rows by
default). The requirement codifies the contract the dashboard MUST
satisfy *if* shared-link, screenshot-publish, or third-party viewer
flows are added in the future.

The shape of the viewer-context plumbing (session role enum, share-link
tokens, screenshot-mode flag) is OUT OF SCOPE for this requirement —
those decisions belong to the implementing change.

#### Scenario: Owner viewer renders sensitive rows fully

- **WHEN** the dashboard renders for a viewer whose viewer-context
  identifies them as the owner
- **AND** an episode has `privacy_tier = sensitive`
- **THEN** the Gantt bar and map coordinates for that episode SHALL
  render with full title and payload, exactly as if the episode were
  `privacy_tier = normal`
- **AND** no per-row toggle SHALL be required for the owner to see
  their own data

#### Scenario: Non-owner viewer triggers fail-safe masking

- **WHEN** the dashboard renders for a viewer whose viewer-context
  does NOT identify them as the owner (e.g. a share-link viewer, a
  screenshot-publish render, a future third-party viewer)
- **AND** an episode has `privacy_tier = sensitive`
- **THEN** the Gantt SHALL render the lane bar as a generic masked
  entry (no title, no payload contents) per the `Sensitive episodes
  masked` scenario
- **AND** the map SHALL NOT plot any coordinates derived from that
  episode or its linked point events
- **AND** there SHALL be no frontend escape hatch — relaxation of the
  mask SHALL require an explicit owner-side configuration change, not
  a viewer-side toggle

#### Scenario: Absent viewer-context is treated as non-owner

- **WHEN** the dashboard renders without a resolvable viewer-context
  (e.g. unauthenticated request, missing session, malformed token)
- **THEN** the page SHALL apply the non-owner masking posture
  (fail-safe-closed)
- **AND** the page MAY additionally redirect to authentication, but
  SHALL NOT render unmasked `sensitive` content while the
  viewer-context is unresolved

#### Scenario: Toggle state is observable for audit

- **WHEN** the dashboard renders for any viewer
- **THEN** the rendered page SHALL expose the resolved viewer-context
  classification (owner / non-owner / unresolved) in a way an
  end-to-end test or audit log can read (e.g. a `data-viewer-role`
  attribute on a stable container element)
- **AND** the audit signal SHALL NOT itself be a vector for relaxing
  the mask — reading the role does not change it
