## MODIFIED Requirements

### Requirement: API Endpoint Inventory
The Traces section is removed from the endpoint inventory. A new Ingestion Events section is added, providing request-ID-anchored lineage queries.

#### Scenario: Traces endpoints removed
- **WHEN** the API server starts
- **THEN** `GET /api/traces` and `GET /api/traces/{traceId}` are NOT registered
- **AND** any frontend code that called these endpoints is updated to use the ingestion events lineage endpoints

#### Scenario: Ingestion events endpoints registered
- **WHEN** the API server starts
- **THEN** the following Ingestion Events endpoints are registered:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/ingestion/events` | Paginated ingestion event list (limit, offset, source_channel filter) |
| GET | `/api/ingestion/events/{requestId}` | Single ingestion event detail |
| GET | `/api/ingestion/events/{requestId}/sessions` | All sessions attributed to this request ID across all butlers |
| GET | `/api/ingestion/events/{requestId}/rollup` | Token/cost/butler topology rollup for this request ID |

## ADDED Requirements

### Requirement: Ingestion Timeline Tab Frontend Hooks
TanStack Query hooks for the Timeline tab on the Ingestion page, following the same cache-key and stale-time conventions as existing ingestion hooks.

#### Scenario: Ingestion events list hook
- **WHEN** the Timeline tab renders on the Ingestion page
- **THEN** `useIngestionEvents(filters)` fetches from `GET /api/ingestion/events` with a 30s stale time
- **AND** the cache key hierarchy is `["ingestion", "events", filters]`

#### Scenario: Request lineage hook
- **WHEN** a user selects a specific ingestion event on the Timeline tab
- **THEN** `useIngestionEventLineage(requestId)` fetches sessions and rollup data in parallel
- **AND** the sessions cache key is `["ingestion", "events", requestId, "sessions"]`
- **AND** the rollup cache key is `["ingestion", "events", requestId, "rollup"]`
- **AND** both use a 30s stale time (no auto-refresh interval; use staleTime only, same as session/trace detail)

## REMOVED Requirements

### Requirement: Traces API
**Reason**: Trace-ID-based lookup is replaced by request-ID-anchored lineage queries under the ingestion namespace, providing a unified view from inbound event through all downstream sessions and OTel spans.
**Migration**: Replace `GET /api/traces` with `GET /api/ingestion/events`. Replace `GET /api/traces/{traceId}` with `GET /api/ingestion/events/{requestId}/sessions` (which includes `trace_id` on each session row for OTel deep-links).
