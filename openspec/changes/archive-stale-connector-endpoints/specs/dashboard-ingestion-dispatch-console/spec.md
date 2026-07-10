# Dashboard Ingestion Dispatch Console

## ADDED Requirements

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
- The fleet-health rollups (`GET /api/ingestion/connectors/cross-summary` and
  `GET /api/switchboard/connectors/summary`) SHALL exclude archived identities
  from their online/stale/offline counts, so a permanently-offline superseded
  identity stops dragging fleet health down.
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

- **WHEN** the fleet-health rollup endpoints aggregate connector liveness
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
