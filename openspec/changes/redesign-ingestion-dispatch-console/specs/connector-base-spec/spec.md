## MODIFIED Requirements

### Requirement: Statistics Pipeline (OTel/Prometheus)
Connector statistics are exported via the OTel/Prometheus metrics pipeline and consumed by the dashboard through Prometheus PromQL queries with a 60-second TTL cache. The pre-aggregated SQL rollup tables (`connector_stats_hourly`, `connector_stats_daily`, `connector_fanout_daily`) were dropped by migration sw_025 (butlers-ufzc) and SHALL NOT be re-introduced; per-request `UNION ALL` aggregation against `public.ingestion_events` and `connectors.filtered_events` at dashboard poll cadence is prohibited.

Detailed contracts for per-connector aggregates (`spark24h`, `rate1h`, `routedPct`, `filtered24h`), the 60-second TTL cache, and the degraded-mode response shape are delegated to the `connector-state-aggregates` spec. This requirement is the base contract; `connector-state-aggregates` is the binding detail.

#### Scenario: Volume metrics via Prometheus
- **WHEN** connectors emit OTel metrics
- **THEN** per-connector volume metrics (`messages_ingested`, `messages_failed`, `source_api_calls`, `dedupe_accepted`) are available in Prometheus for dashboard time-series queries

#### Scenario: Fanout metrics via Prometheus
- **WHEN** connector messages are routed by Switchboard
- **THEN** per-connector per-target-butler fanout metrics are available in Prometheus for dashboard distribution queries

#### Scenario: Dashboard aggregates served from TTL cache
- **WHEN** the dashboard requests per-connector aggregates (e.g., `spark24h`, `rate1h`, `routedPct`, `filtered24h`)
- **THEN** the API SHALL resolve them via Prometheus PromQL with a 60-second TTL cache, as defined in `connector-state-aggregates`
- **AND** the API SHALL NOT execute per-request `UNION ALL` aggregation against `public.ingestion_events` and `connectors.filtered_events` at poll cadence
- **AND** the API SHALL NOT reintroduce rollup tables equivalent to `connector_stats_hourly`/`connector_stats_daily`/`connector_fanout_daily` without an explicit superseding RFC

#### Scenario: Degraded mode when Prometheus unreachable
- **WHEN** Prometheus is unreachable during an aggregates query
- **THEN** the API SHALL return zero values with `aggregates_available: false` per `connector-state-aggregates`
- **AND** the API SHALL NOT return HTTP 500

### Requirement: Dashboard Connector Page
Dashboard frontend exposes connector fleet monitoring at `/ingestion/connectors` (the first-class sub-route introduced by the ingestion-dispatch-console redesign). Legacy `?tab=connectors` URLs SHALL 301-redirect to `/ingestion/connectors`. The connector detail page is reached at `/ingestion/connectors/<connector_type>/<endpoint_identity>`.

Breadcrumb `href` values and back-link targets across the connector detail page header, breadcrumbs, and back-link components SHALL point at `/ingestion/connectors` (NOT the legacy `?tab=connectors` query-string form). The three components MUST update atomically in the same change set. The detailed breadcrumb contract lives in `connector-detail-archetype-conformance`; this requirement establishes the canonical route.

#### Scenario: Connector overview cards
- **WHEN** the `/ingestion/connectors` page is loaded
- **THEN** each registered connector shows: type icon, endpoint identity, liveness badge, health state, uptime percentage, last heartbeat age, today's ingestion count

#### Scenario: Legacy URL redirects
- **WHEN** an operator visits `/ingestion?tab=connectors` (or any other tab-param URL covered by the redesign)
- **THEN** the dashboard SHALL serve a 301 redirect to `/ingestion/connectors`

#### Scenario: Connector detail breadcrumb target
- **WHEN** the connector detail page is rendered
- **THEN** the breadcrumb, back link, and header navigation SHALL each link to `/ingestion/connectors`
- **AND** none of them SHALL link to `?tab=connectors`
- **AND** the three components SHALL be updated atomically in the same change set (see `connector-detail-archetype-conformance` for the detailed contract)

#### Scenario: Volume time series chart
- **WHEN** a time period is selected (24h/7d/30d)
- **THEN** a chart shows ingestion volume per connector
- **AND** the underlying data is served from the Prometheus + 60s TTL aggregates path defined in `connector-state-aggregates`

#### Scenario: Fanout distribution matrix
- **WHEN** fanout data is viewed
- **THEN** a table/heatmap shows connector × butler routing distribution
- **AND** the underlying data is served from the Prometheus + 60s TTL aggregates path

#### Scenario: Error log view
- **WHEN** the error log is viewed
- **THEN** recent connector errors are shown with timestamp, identity, state, and error message

## ADDED Requirements

### Requirement: Connector Lifecycle Ceremony — Per-Action Gate Matrix
Connector lifecycle actions exposed by the dashboard SHALL be gated according to the per-action matrix below. The matrix is the binding base contract; detailed semantics (payload shapes, credential masking, soft-delete behavior, Approvals wiring, reauth blocking until `connector-oauth-scope-surface/spec` exists) are delegated to `connector-lifecycle-ceremony/spec`.

| Action | Gate | Mutation | Notes |
|---|---|---|---|
| `pause` | audit-log-only (no Approvals) | `connector_registry` paused-state flag set; ingest loop suspended | Reversible via `run-now` / resume |
| `run-now` | audit-log-only (no Approvals) | clears paused-state flag; triggers an immediate poll | Resume semantics for a paused connector |
| `disconnect` | Approvals-gated (MCP `is_sensitive=True`) | soft-delete: `connector_registry.deleted_at = now()`; row preserved for history | NOT a hard delete |
| `rotate-token` | Approvals-gated (MCP `is_sensitive=True`) | credential refresh in secret store; response NEVER returns the credential body | Returns success + timestamp only |
| `reauth` | Approvals-gated; additionally blocked until `connector-oauth-scope-surface/spec` is ratified | OAuth re-consent flow | Hard block — handler returns HTTP 503 with `reason = "reauth_spec_pending"` until the gating spec ships |

Every lifecycle action (audit-only or Approvals-gated) MUST emit an `audit.append()` entry with actor, action, connector identity, reason, and `request_id`. Approvals-gated actions MUST additionally route through the Approvals module at the MCP server level — the dashboard MUST NOT bypass Approvals by calling DB primitives directly.

#### Scenario: Pause action — audit only
- **WHEN** an operator invokes `pause` on a connector via the dashboard
- **THEN** the handler SHALL update `connector_registry` to mark the connector paused (suspending its ingest loop) without invoking the Approvals module
- **AND** an `audit.append()` entry SHALL be written with action `connector.pause`, the connector identity, actor, and `request_id`

#### Scenario: Run-now action — audit only
- **WHEN** an operator invokes `run-now` on a paused connector
- **THEN** the handler SHALL clear the paused-state flag and signal an immediate poll without invoking the Approvals module
- **AND** an `audit.append()` entry SHALL be written with action `connector.run_now`

#### Scenario: Disconnect action — Approvals-gated soft-delete
- **WHEN** an operator invokes `disconnect` on a connector
- **THEN** the handler SHALL require Approvals confirmation (MCP `is_sensitive=True`) before mutating any row
- **AND** on approval, the handler SHALL set `connector_registry.deleted_at = now()` and leave the row in place (no hard delete)
- **AND** an `audit.append()` entry SHALL be written with action `connector.disconnect`

#### Scenario: Rotate-token action — credential never in body
- **WHEN** an operator invokes `rotate-token` on a connector
- **THEN** the handler SHALL require Approvals confirmation (MCP `is_sensitive=True`)
- **AND** the handler SHALL refresh the credential in the secret store
- **AND** the response body SHALL contain only `{ "status": "ok", "rotated_at": "<timestamp>" }` and SHALL NOT contain the new credential value
- **AND** an `audit.append()` entry SHALL be written with action `connector.rotate_token`

#### Scenario: Reauth action blocked until OAuth scope spec exists
- **WHEN** an operator invokes `reauth` on a connector and `connector-oauth-scope-surface/spec` has not been ratified
- **THEN** the handler SHALL return HTTP 503 with body `{ "error": "reauth flow gated", "reason": "reauth_spec_pending" }`
- **AND** no OAuth flow SHALL be initiated
- **AND** an `audit.append()` entry MAY be written for visibility but no mutation occurs

#### Scenario: Approvals bypass prohibited
- **WHEN** any code path attempts to perform `disconnect`, `rotate-token`, or `reauth` without going through the Approvals module
- **THEN** the design SHALL be treated as a doctrine violation
- **AND** the gated lifecycle handlers SHALL be the only mutation surface for these actions

### Requirement: Available Connectors Discovery Endpoint Contract
The connector-base contract SHALL include a discovery endpoint at `GET /api/ingestion/connectors/available` returning the connector types and providers the framework can deploy, independent of any rows currently in `connector_registry`. This is the base-spec contract corollary to the ingestion-event-registry amendment that adds the same endpoint — it exists here so that every connector profile (`connector-gmail`, `connector-telegram-bot`, etc.) inherits the obligation to appear in the discovery list.

#### Scenario: Discovery list includes all framework-known connectors
- **WHEN** `GET /api/ingestion/connectors/available` is called
- **THEN** the response SHALL include one entry per connector type known to the framework, regardless of whether an instance is registered
- **AND** each entry SHALL include `connector_type`, `channel`, `provider`, `display_name`, and `supports_backfill`
- **AND** new connector profiles added to the codebase SHALL be reflected in this endpoint without manual registration

#### Scenario: Discovery independent of registry rows
- **WHEN** no `connector_registry` row exists for a particular `connector_type`
- **THEN** the discovery endpoint SHALL still return that type (so the dashboard can offer it as an "add connector" option)
- **AND** the response SHALL NOT join against `connector_registry` for inclusion logic
