# Dashboard Ingestion Dispatch Console

## ADDED Requirements

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

- `archive_candidate` SHALL NOT contribute to the fleet-health rollups
  (`GET /api/ingestion/connectors/cross-summary`,
  `GET /api/switchboard/connectors/summary`) or to alerting — those exclude only
  `archived` identities.
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

- **WHEN** the fleet-health rollup endpoints aggregate connector liveness
- **THEN** `archive_candidate` has no effect on the online/stale/offline counts
- **AND** the degraded-mode envelope flags are unchanged by the candidate
  computation

#### Scenario: Exactly 30 days offline is not yet a candidate

- **WHEN** an active identity's last heartbeat is exactly 30 days old
- **THEN** it is NOT flagged `archive_candidate` (the offline-age test is strict
  `> 30d`)
