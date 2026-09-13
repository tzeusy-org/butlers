## ADDED Requirements

### Requirement: Canonical Connector Dashboard API Namespace

Every dashboard connector read or settings update SHALL use the
`/api/ingestion/connectors` namespace. The role-aware `summaries` response is
the sole connector-list source for the Timeline, roster, and System topology;
detail, statistics, and settings use the corresponding canonical connector
resource routes. The dashboard SHALL NOT fall back to or request
`/api/switchboard/connectors`.

#### Scenario: Timeline attention uses runtime-authoritative summaries

- **WHEN** the Timeline, its channel picker, or its verdict needs connector
  state
- **THEN** it reads `GET /api/ingestion/connectors/summaries`
- **AND** checkpoint rows and archived identities do not contribute to its
  attention list or channel choices
- **AND** an unavailable summaries source is rendered as unavailable rather
  than replaced by a raw-registry fallback

#### Scenario: Connector detail uses canonical subresources

- **WHEN** the owner opens a connector detail route
- **THEN** its detail reads use
  `GET /api/ingestion/connectors/{type}/{identity}`
- **AND** its histogram reads
  `GET /api/ingestion/connectors/{type}/{identity}/stats`
- **AND** a settings update uses
  `PATCH /api/ingestion/connectors/{type}/{identity}/settings`
- **AND** the detail, statistics, and settings responses retain their existing
  envelope, credential-masking, and archived-history semantics

#### Scenario: Legacy connector namespace is absent

- **WHEN** a client requests any `/api/switchboard/connectors` path after the
  migration
- **THEN** the dashboard API does not expose a route, redirect, alias, or
  compatibility wrapper at that path
- **AND** clients use the canonical ingestion connector routes instead

## MODIFIED Requirements

### Requirement: Connector Archive Review Queue

The connectors surface SHALL offer a flag-only archive REVIEW QUEUE that
suggests superseded endpoint identities for archiving, without ever
auto-archiving them. `GET /api/ingestion/connectors/summaries` SHALL compute a
read-only `archive_candidate` boolean per connector, `true` only for an active
(non-archived) identity that BOTH:

- last heartbeated strictly more than 30 days ago, AND
- has at least one other identity of the same `connector_type` that is currently
  `online` and not archived (a "newer online sibling").

The queue is a SUGGESTION and SHALL NOT change the fleet signal:

- `archive_candidate` SHALL NOT contribute to the fleet-health rollup
  `GET /api/ingestion/connectors/cross-summary` or to alerting — those exclude
  only `archived` identities.
- A candidate SHALL remain in the active roster with its true (offline) liveness
  and SHALL NOT be filed as merely an archive candidate; a genuinely-failing
  live connector (not offline for 30+ days) SHALL never be flagged.
- The degraded-mode envelope (`aggregates_available` /
  `device_liveness_available` / `hourly_events_available`) SHALL keep its
  existing shape and genuine-failure-only semantics.

The dashboard SHALL surface candidates as a review queue distinct from the
active roster and the archived section, each candidate offering a one-click
archive that reuses the existing audit-logged archive endpoint
(`POST /api/ingestion/connectors/{type}/{identity}/archive`) — archival stays a
human action.

#### Scenario: Offline identity with a newer online sibling is a candidate

- **WHEN** an active identity last heartbeated more than 30 days ago **AND**
  another identity of the same `connector_type` is currently `online`
- **THEN** the `summaries` endpoint flags that identity `archive_candidate: true`
- **AND** the roster lists it in the archive review queue with a one-click
  archive action wired to the archive endpoint
- **AND** the identity still appears in the active roster with its true offline
  liveness

#### Scenario: Quiet identity with no online sibling is not a candidate

- **WHEN** an active identity is offline for more than 30 days but no other
  identity of the same `connector_type` is currently `online`
- **THEN** it is NOT flagged `archive_candidate`, so a merely-quiet connector is
  never suggested for archiving

#### Scenario: Review queue does not affect fleet health

- **WHEN** the fleet-health rollup endpoint aggregates connector liveness
- **THEN** `archive_candidate` has no effect on the online/stale/offline counts
- **AND** the degraded-mode envelope flags are unchanged by the candidate
  computation

#### Scenario: Exactly 30 days offline is not yet a candidate

- **WHEN** an active identity's last heartbeat is exactly 30 days old
- **THEN** it is NOT flagged `archive_candidate` (the offline-age test is strict
  `> 30d`)

### Requirement: Archived Connector Identities

Superseded or dead connector endpoint identities SHALL be archivable via a soft
`archived_at` state on `connector_registry`, distinct from the `deleted_at`
disconnect soft-delete. An archived identity is retained (never deleted, because
ingestion history still references it) but is separated from the active fleet:

- The `/ingestion/connectors` roster SHALL group archived identities into a
  collapsed "archived" section, distinct from the active roster and the dormant
  section, with each archived row linking to that identity's connector detail so
  its history stays reachable.
- Archived identities SHALL NOT contribute to the active roster's attention
  strip or KPI band.
- The fleet-health rollup `GET /api/ingestion/connectors/cross-summary` SHALL
  exclude archived identities from its online/stale/offline counts, so a
  permanently-offline superseded identity stops dragging fleet health down.
- Archiving SHALL be reversible (an unarchive path restores the identity to the
  active roster) and SHALL be a distinct state from `degraded`/`offline`:
  archiving SHALL NOT be applied to, and SHALL NOT mask, a genuinely-failing
  *live* connector, which remains in the active roster.

#### Scenario: Archived identity is grouped, not deleted

- **WHEN** a connector endpoint identity has `archived_at` set and `deleted_at`
  is null
- **THEN** the `summaries` endpoint still returns it, flagged `archived: true`
  with its `archived_at` timestamp
- **AND** the roster renders it in a collapsed "archived" section rather than an
  active row
- **AND** its row links to the connector detail route so events and incidents
  remain reachable

#### Scenario: Archived identities do not drag fleet health down

- **WHEN** the fleet-health rollup endpoint aggregates connector liveness
- **THEN** archived identities are excluded from the online/stale/offline counts
- **AND** a genuinely-failing live connector is NOT archived and still counts
  toward (and surfaces in) the fleet-health signal

#### Scenario: Degraded-mode envelope is unchanged by archiving

- **WHEN** the `summaries` or `cross-summary` endpoints respond
- **THEN** the `aggregates_available` / `device_liveness_available` /
  `hourly_events_available` degraded-mode flags keep their existing shape and
  genuine-failure-only semantics
- **AND** archiving never causes a genuinely-unreachable source to render as an
  honest empty/all-clear result
