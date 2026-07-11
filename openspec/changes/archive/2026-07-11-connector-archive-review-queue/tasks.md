## 1. API

- [x] 1.1 `GET /api/ingestion/connectors/summaries` computes a read-only
  `archive_candidate` boolean per connector: active (non-archived) AND last
  heartbeat strictly >30d old AND a newer online sibling of the same
  `connector_type` exists. No new query, no storage, no migration.
- [x] 1.2 The flag is a suggestion only — not folded into `cross-summary` /
  `/switchboard/connectors/summary` rollups; degraded-mode envelope unchanged.

## 2. Dashboard

- [x] 2.1 An `ArchiveCandidatesList` review-queue section below the roster
  listing `archive_candidate` identities, each linking to connector detail.
- [x] 2.2 One-click archive per row reusing the existing audit-logged archive
  endpoint (`useArchiveConnector` → `POST …/archive`), with pending + error
  states; no auto-archive, no dead onClick.
- [x] 2.3 Candidates also remain in the active roster with their true offline
  liveness (suggestion overlay, not a filter).

## 3. Tests

- [x] 3.1 Backend: candidate-rule unit tests (exactly 30d boundary, just over /
  under, never-heartbeated, no sibling, sibling offline, already archived,
  archived sibling not counted) + endpoint surfacing.
- [x] 3.2 Frontend: review-queue rendering, one-click archive wiring, pending +
  error states, and candidate-stays-in-active-roster.
