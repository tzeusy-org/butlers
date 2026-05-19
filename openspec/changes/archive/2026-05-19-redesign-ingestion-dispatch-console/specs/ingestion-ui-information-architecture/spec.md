# Ingestion UI Information Architecture

## Purpose
Defines the route hierarchy, URL contracts, sub-route wrappers, and rendering contracts for the `/ingestion` dashboard surface. Replaces the previous single-route `?tab=` URL parameter model with first-class sub-routes that match the Dispatch design language and the connector-detail-archetype-conformance breadcrumb model. This capability owns information architecture only — data contracts live in `ingestion-event-registry`, policy in `ingestion-policy`, and lifecycle in `connector-lifecycle-ceremony`.

## ADDED Requirements

### Requirement: Sub-route hierarchy
The system SHALL expose four first-class routes under `/ingestion`: `/ingestion` (Timeline root), `/ingestion/connectors` (connector roster list), `/ingestion/filters` (filter rule management), and `/ingestion/history` (backfill and replay history). Each route SHALL render its own page-level component and SHALL NOT depend on a `?tab=` query parameter to determine which view is active.

#### Scenario: Timeline route renders at /ingestion
- **WHEN** a user navigates to `/ingestion`
- **THEN** the Timeline page renders as the root view
- **AND** no `?tab=` query parameter is required

#### Scenario: Connectors route renders at /ingestion/connectors
- **WHEN** a user navigates to `/ingestion/connectors`
- **THEN** the connector roster list page renders
- **AND** the breadcrumb resolves to `/ingestion/connectors` (not `?tab=connectors`)

#### Scenario: Filters route renders at /ingestion/filters
- **WHEN** a user navigates to `/ingestion/filters`
- **THEN** the filters page renders by wrapping the existing `FiltersTab` component with no rewrite of the inner component

#### Scenario: History route renders at /ingestion/history
- **WHEN** a user navigates to `/ingestion/history`
- **THEN** the history page renders by wrapping the existing `BackfillHistoryTab` component with no rewrite of the inner component

### Requirement: 301 redirects from legacy tab parameters
The router SHALL issue HTTP 301 (permanent) client-side redirects from legacy `?tab=` URLs to their new sub-route equivalents. This SHALL apply to `?tab=connectors`, `?tab=filters`, and `?tab=history`. Bookmarks, deep links, and external references to the legacy URL shape MUST continue to resolve to the correct surface after the redesign ships.

#### Scenario: Legacy connectors tab redirects
- **WHEN** a user opens `/ingestion?tab=connectors`
- **THEN** the router issues a 301 redirect to `/ingestion/connectors`
- **AND** any additional query parameters (e.g. filters) are preserved

#### Scenario: Legacy filters tab redirects
- **WHEN** a user opens `/ingestion?tab=filters`
- **THEN** the router issues a 301 redirect to `/ingestion/filters`

#### Scenario: Legacy history tab redirects
- **WHEN** a user opens `/ingestion?tab=history`
- **THEN** the router issues a 301 redirect to `/ingestion/history`

#### Scenario: Unrecognized tab parameter falls through
- **WHEN** a user opens `/ingestion?tab=unknown`
- **THEN** the router strips the unknown `tab` parameter and renders the Timeline root

### Requirement: Sub-route wrappers preserve existing components
Sub-route pages for filters and history SHALL be thin wrappers around the existing `FiltersTab` and `BackfillHistoryTab` components. The redesign SHALL NOT require rewriting either inner component to ship the route promotion. Wrappers SHALL only add page-level chrome (heading, breadcrumb, layout container) around the existing component.

#### Scenario: FiltersTab is reused without rewrite
- **WHEN** the `/ingestion/filters` page is implemented
- **THEN** it imports and mounts the existing `FiltersTab` component verbatim
- **AND** any data fetching, state, and interaction behaviour inside `FiltersTab` is unchanged

#### Scenario: BackfillHistoryTab is reused without rewrite
- **WHEN** the `/ingestion/history` page is implemented
- **THEN** it imports and mounts the existing `BackfillHistoryTab` component verbatim

### Requirement: Connector roster list summary-only polling
The connector roster list view at `/ingestion/connectors` SHALL NOT mount the `useConnectorDetail` hook (or any equivalent per-connector detail fetch) for items in the list. Roster polling SHALL use summary endpoints only at a cadence no faster than 60 seconds. Detail data SHALL load only when a user navigates to a specific connector's detail page.

#### Scenario: useConnectorDetail is prohibited on list
- **WHEN** the connector roster list renders
- **THEN** `useConnectorDetail` is not mounted for any row
- **AND** only summary fields available from the roster summary endpoint are displayed

#### Scenario: Summary polling cadence
- **WHEN** the connector roster list polls for updates
- **THEN** the polling interval SHALL be 60 seconds or longer
- **AND** the request fetches summary aggregates only (no per-connector detail payload)

### Requirement: AttentionStrip dependency declaration
The connector AttentionStrip component SHALL depend on either the bu-ju4kh attention primitive (when shipped) or an explicitly extracted attention primitive in the shared component library. The AttentionStrip SHALL NOT be implemented inline in the `/ingestion` surface as a bespoke component without that shared primitive available.

#### Scenario: AttentionStrip resolves via shared primitive
- **WHEN** the AttentionStrip is rendered on `/ingestion/connectors` or in connector detail
- **THEN** it imports its core attention-state rendering from the shared primitive (bu-ju4kh or its extracted equivalent)

#### Scenario: Bead dependency is declared
- **WHEN** an implementation bead introduces AttentionStrip onto the ingestion surface
- **THEN** the bead description references the bu-ju4kh dependency or the extracted-primitive substitute and links to its tracking bead

### Requirement: Resolved contact rendering in event drawer
The event drawer SHALL resolve `sender_identity` for each event using `resolve_contact_by_channel()` from the contacts identity surface. When a contact resolves, the drawer SHALL display the canonical contact name. When no contact resolves, the drawer SHALL display the raw sender identity value alongside a visual "unresolved" indicator that is distinguishable from a resolved name.

#### Scenario: Resolved contact shows canonical name
- **WHEN** an event drawer is opened for an event whose `sender_identity` matches a row in `public.contact_info`
- **THEN** the drawer displays the resolved canonical contact name
- **AND** the raw `sender_identity` value is available on hover or in a secondary display

#### Scenario: Unresolved contact shows raw value with indicator
- **WHEN** an event drawer is opened for an event whose `sender_identity` does not match any row in `public.contact_info`
- **THEN** the drawer displays the raw `sender_identity` value
- **AND** a visual "unresolved" indicator is rendered alongside the value

#### Scenario: Resolution error falls back to raw value
- **WHEN** `resolve_contact_by_channel()` raises or returns a database error
- **THEN** the drawer displays the raw `sender_identity` value
- **AND** the "unresolved" indicator is rendered
- **AND** the failure is logged without surfacing an error toast (fail-open render)

### Requirement: Connector-scoped rule UI restricts action to block
The filter management UI SHALL restrict the available `action` options for connector-scoped rules to `block` only. Other actions (`skip`, `metadata_only`, `low_priority_queue`, `pass_through`, `route_to:<butler>`) SHALL NOT be selectable when the rule scope is `connector:*`. This aligns the UI control with the handler-level enforcement in `ingestion-policy`.

#### Scenario: Connector scope shows only block action
- **WHEN** a user creates or edits a rule with scope `connector:<type>:<identity>` in the filter UI
- **THEN** the action selector exposes only `block` as a selectable option

#### Scenario: Global scope shows full action set
- **WHEN** a user creates or edits a rule with scope `global` in the filter UI
- **THEN** the action selector exposes `skip`, `metadata_only`, `low_priority_queue`, `pass_through`, and `route_to:<butler>`

### Requirement: Filter control contract on sub-routes
Filter controls rendered on a sub-route SHALL preserve their selected filter state in the URL query string (e.g. `?status=filtered&source_channel=email`) so that links can be shared and browser back/forward navigation works as expected. State that would conflict with the legacy `?tab=` parameter SHALL NOT reuse the `tab` key name.

#### Scenario: Filter state preserved in URL
- **WHEN** a user selects a `status` filter on `/ingestion`
- **THEN** the URL updates to include the selected value as a query parameter
- **AND** reloading the page restores the same filter state

#### Scenario: Tab key is not reused for filter state
- **WHEN** filter controls write filter values to the URL
- **THEN** the parameter name `tab` SHALL NOT be reused for any new filter, sort, or pagination control

### Requirement: Channel defaults data model and REST API
The system SHALL store per-channel default policy in a `channel_defaults` table in the `public` schema and SHALL expose a small REST surface for reading and updating channel defaults from the dashboard.

Table schema:
- `channel` TEXT PRIMARY KEY (e.g. `email`, `telegram`, `home-assistant`)
- `default_policy_json` JSONB NOT NULL — opaque JSON document interpreted by per-channel evaluators; structure MUST be validated against a per-channel schema at PATCH time (reject 400 on failure)
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
- `updated_by` TEXT NOT NULL

Endpoints:
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ingestion/channel-defaults/{channel}` | Read the default-policy document for a single channel. Returns 404 if no row exists. |
| PATCH | `/api/ingestion/channel-defaults/{channel}` | Upsert the default-policy document. Body validated against the per-channel schema. Creates the row if missing. |

Retention: the table has no TTL; entries persist indefinitely until explicitly overwritten by PATCH. There SHALL be no DELETE endpoint exposed.

#### Scenario: GET returns the channel defaults
- **WHEN** GET `/api/ingestion/channel-defaults/email` is called and a row exists
- **THEN** the response body is `{channel, default_policy_json, updated_at, updated_by}`

#### Scenario: GET returns 404 for missing channel
- **WHEN** GET `/api/ingestion/channel-defaults/<unknown>` is called and no row exists
- **THEN** the response is HTTP 404

#### Scenario: PATCH upserts and validates
- **WHEN** PATCH `/api/ingestion/channel-defaults/email` is called with a body matching the per-channel schema
- **THEN** the row is upserted with `updated_at = NOW()` and `updated_by` set to the authenticated actor
- **AND** the response returns the updated document

#### Scenario: PATCH rejects invalid schema
- **WHEN** PATCH `/api/ingestion/channel-defaults/email` is called with a body that fails per-channel schema validation
- **THEN** the response is HTTP 400 with the validation error
- **AND** no row is mutated

#### Scenario: No DELETE surface
- **WHEN** any caller attempts DELETE on `/api/ingestion/channel-defaults/<channel>`
- **THEN** the response is HTTP 405 (Method Not Allowed)

### Requirement: Mutation audit emission
Every mutation initiated from the `/ingestion` UI (rule create/update/delete, connector lifecycle action, priority contact add/remove, channel default update, bulk replay submission) SHALL emit `audit.append()` to `public.audit_log` with `actor`, `action`, `target`, `reason`, and `request_id`. Audit entries SHALL be retained indefinitely and SHALL NOT be deleted by any UI surface.

#### Scenario: Rule create emits audit entry
- **WHEN** a user creates a new ingestion rule from the UI
- **THEN** an `audit.append()` entry is written to `public.audit_log` with the actor's identity, `action="ingestion.rule.create"`, the rule id as `target`, a `reason` (free text or empty), and the originating `request_id`

#### Scenario: Audit entries are never deleted
- **WHEN** any UI surface attempts to delete an audit log entry
- **THEN** no DELETE endpoint is exposed
- **AND** soft-delete semantics do not apply to `public.audit_log`
