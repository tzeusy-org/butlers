## 1. Schema + seed

- [x] 1.1 Switchboard migration `sw_022`: add `connector_registry.archived_at`
  `TIMESTAMPTZ NULL` + `ix_connector_registry_live` partial index (not deleted
  AND not archived).
- [x] 1.2 Idempotent data-seed in `sw_022` archiving the four dead identities
  (UUID-suffixed google_health matched by owner prefix).

## 2. API

- [x] 2.1 `GET /api/ingestion/connectors/summaries` returns `archived` /
  `archived_at` per connector (archived rows still included).
- [x] 2.2 `GET /api/ingestion/connectors/cross-summary` excludes
  `archived_at IS NULL` from the health rollup.
- [x] 2.3 `GET /api/switchboard/connectors/summary` excludes archived from the
  fleet-health rollup.
- [x] 2.4 Audit-only `POST …/{type}/{identity}/archive` and `…/unarchive`
  endpoints (no Approvals gate; emit `connector.archive` / `connector.unarchive`
  audit entries).

## 3. Dashboard

- [x] 3.1 `ConnectorSummary` type gains `archived` / `archived_at`.
- [x] 3.2 Roster splits archived out of the active list (no attention/KPI).
- [x] 3.3 Collapsed "archived · superseded" section linking each row to detail.

## 4. Tests

- [x] 4.1 Backend unit: summaries archived flag; archive/unarchive endpoints
  (200 + audit action, 404).
- [x] 4.2 Backend integration (real Postgres): `sw_022` column/index, seed
  archives exactly the four, idempotency, downgrade.
- [x] 4.3 Frontend vitest: archived section collapse/expand, exclusion from
  active roster + attention, detail links.

## 5. Follow-up (not in this change)

- [ ] 5.1 Auto-archive review queue for offline >30d identities sharing a
  `connector_type` with a newer online identity (proposed in proposal.md).
