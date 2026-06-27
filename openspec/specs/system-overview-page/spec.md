# System Overview Page

## Purpose

The System Overview page (`/system`) is the dashboard surface where the owner sees their
instance as infrastructure they own. It surfaces five ownership-fact domains: software
version and uptime, database state, backup state, data egress catalog, and per-butler
heartbeats. This page exists because the doctrine in
`about/heart-and-soul/vision.md` Non-Negotiable Rule 1 is not visible anywhere else in the
dashboard: "You own the instance, the data, the credentials, and the agents."

The page is operator-grade read-only. It contains no write operations, no approvals,
and no administrative actions. It is the answer to: "What is my system, and where has
my data been?"

## Requirements

### Requirement: System Route and Navigation

The dashboard SHALL expose a `/system` route accessible from the sidebar under the
Telemetry section. The route renders a System Overview page inside the standard shell.

#### Scenario: Route registration

- **WHEN** the React Router config is initialized
- **THEN** a `/system` route is registered alongside the existing Telemetry routes
  (`/timeline`, `/notifications`, `/issues`, `/audit-log`)
- **AND** the route renders `SystemPage` inside the root layout shell

#### Scenario: Navigation entry

- **WHEN** the sidebar renders its Telemetry section
- **THEN** a "System" entry appears in the Telemetry nav group, linking to `/system`
- **AND** the entry is always visible (no butler-presence filter; the System page
  aggregates across all butlers and does not require a specific butler to be registered)

### Requirement: Instance Identity Facts

The `/api/system/instance` endpoint SHALL return the software version of the running
Butlers package, the process uptime in seconds, and the UTC timestamp at which the
process started.

#### Scenario: Instance endpoint returns version and uptime

- **WHEN** `GET /api/system/instance` is called
- **THEN** the response body contains:
  - `version: string` -- the `__version__` from the `butlers` Python package
    (e.g., `"0.14.2"`)
  - `uptime_seconds: number` -- seconds elapsed since the FastAPI lifespan started
  - `started_at: string` -- ISO 8601 UTC timestamp of process start, matching
    `now() - uptime_seconds`
- **AND** the response wraps in the standard `ApiResponse<InstanceFacts>` envelope

#### Scenario: Version source is the package metadata

- **WHEN** the version string is resolved
- **THEN** it is read from `importlib.metadata.version("butlers")` or the
  `src/butlers/__init__.py` `__version__` constant -- never from an environment
  variable or a hardcoded literal in the router
- **AND** if the version cannot be resolved, the field returns `"unknown"` rather than
  raising a 500

### Requirement: Database State Facts

The `/api/system/database` endpoint SHALL return the total size of the `butlers`
PostgreSQL database in bytes, a per-schema breakdown, and a disk-size ranking of the
largest tables. Growth-rate and row-count-estimate fields are reserved for a future
extension (row counts from `pg_stat_user_tables` require elevated permissions not
guaranteed on the dashboard API role).

#### Scenario: Database size query

- **WHEN** `GET /api/system/database` is called
- **THEN** the response body contains:
  - `total_size_bytes: number` -- result of `pg_database_size(current_database())`
  - `schemas: SchemaSize[]` -- per-butler-schema breakdown, each entry having
    `schema_name: string`, `size_bytes: number`, and `table_count: number`
  - `largest_tables: TableSize[]` -- up to 10 tables ranked by `pg_total_relation_size`,
    each having `schema_name: string`, `table_name: string`, and `size_bytes: number`
  - `growth_rate_bytes_per_day: null` -- reserved for v2; always null in v1
- **AND** the response wraps in the standard `ApiResponse<DatabaseFacts>` envelope

#### Scenario: Schema enumeration uses the roster

- **WHEN** the per-schema breakdown is assembled
- **THEN** only schemas corresponding to registered butler names (from the roster) are
  included
- **AND** the `public` schema is excluded from the per-butler breakdown (it is a
  cross-cutting schema, not a butler-owned schema)
- **AND** schemas with `size_bytes = 0` are included with a zero value, not omitted

#### Scenario: Database access failure is surfaced

- **WHEN** the catalog query fails (permission denied, connection error)
- **THEN** the endpoint returns HTTP 503 with an `ErrorResponse` body rather than a
  partial or stale response

### Requirement: Backup State Facts

The `/api/system/backups` endpoint SHALL return the recency and size of the most recent
database backup, plus a short history of recent backup events.

#### Scenario: Backup endpoint returns recency

- **WHEN** `GET /api/system/backups` is called
- **THEN** the response body contains:
  - `last_backup_at: string | null` -- ISO 8601 UTC timestamp of the most recent
    successful backup, or `null` if no backup has been recorded or the backup source
    is unreachable
  - `last_backup_size_bytes: number | null` -- size of the most recent backup in bytes,
    or `null`
  - `backup_source_reachable: boolean` -- `true` if the backup metadata source
    (Minio/S3 bucket or filesystem) responded to the health check, `false` otherwise
  - `backup_history: BackupEvent[]` -- up to 7 most recent backup events, each having
    `completed_at: string`, `size_bytes: number`, and `status: "success" | "failed"`
- **AND** the response wraps in the standard `ApiResponse<BackupFacts>` envelope

#### Scenario: Unavailable backup source degrades gracefully

- **WHEN** the backup metadata source (Minio/S3, filesystem) is unreachable
- **THEN** `last_backup_at` and `last_backup_size_bytes` are `null`
- **AND** `backup_source_reachable` is `false`
- **AND** `backup_history` is an empty array
- **AND** the response is HTTP 200 with the degraded payload -- not HTTP 503
- **AND** the frontend renders a "backup status unavailable" indicator rather than an
  error state

### Requirement: Data Egress Catalog

The `/api/system/egress` endpoint SHALL return a catalog of external actor endpoints
that have received data from this instance, derived from the existing audit log. This is
the "your data has been seen by these endpoints" surface.

#### Scenario: Egress catalog endpoint returns actor list

- **WHEN** `GET /api/system/egress` is called
- **THEN** the response body contains:
  - `actors: EgressActor[]` -- list of external actor endpoints, ordered by
    `last_seen_at` descending (most recent first)
  - `catalog_covers_from: string | null` -- ISO 8601 UTC timestamp of the oldest
    audit log entry used to build this catalog, so the owner knows the window
    the catalog reflects
- **AND** each `EgressActor` entry contains:
  - `actor_id: string` -- stable identifier for the actor (e.g., `"anthropic.claude"`,
    `"google.calendar"`, `"telegram.api"`)
  - `display_name: string` -- human-readable name (e.g., `"Anthropic Claude API"`,
    `"Google Calendar API"`, `"Telegram Bot API"`)
  - `last_seen_at: string` -- ISO 8601 UTC timestamp of the most recent recorded
    egress event for this actor
  - `total_calls: number` -- count of recorded egress events for this actor
    within the audit window
  - `data_types: string[]` -- array of coarse data type labels observed in the
    egress events (e.g., `["session_prompt", "calendar_event", "message_text"]`)
- **AND** the response wraps in the standard `ApiResponse<EgressCatalog>` envelope

#### Scenario: Egress catalog is derived from the audit log

- **WHEN** the egress catalog is assembled
- **THEN** it reads exclusively from the canonical audit log table
  (`public.audit_log`) -- no new write path is introduced. The legacy
  `switchboard.dashboard_audit_log` rows were backfilled into `public.audit_log` by
  migration `core_124` and the UNION arm was removed; there is no
  `audit.events` table. Actor identity is derived from the `action` column (aliased
  `operation`, with `ts` aliased `created_at`) via the server-side actor registry.
  (`request_summary` JSONB is not used for actor derivation in v1; the registry maps
  `operation` strings directly to actor identifiers and display names.)
- **AND** only records whose `operation` value maps to an external actor in the
  actor registry are included (e.g., `"llm_api_call"`, `"telegram_send"`,
  `"google_calendar_write"`, `"gmail_send"`); the implementation bead MUST define
  and document this naming convention in `AGENTS.md`
- **AND** the implementation bead SHALL verify audit log coverage for each egress
  path (LLM API calls, Telegram outbound, Google APIs, Gmail SMTP) and file
  follow-up beads for any paths not captured

#### Scenario: Egress catalog access is owner-only in v1

- **WHEN** `GET /api/system/egress` is called
- **THEN** the endpoint SHALL assert that the requesting session corresponds to the
  owner contact -- resolved by joining `public.contacts c` to `public.entities e` on
  `c.entity_id = e.id` and asserting `'owner' = ANY(e.roles)`. Note:
  `public.contacts.roles` was dropped in migration `core_016`; role lookups MUST use
  `public.entities.roles` via this JOIN.
- **AND** if the owner assertion fails, the endpoint returns HTTP 403
- **AND** in v1, no other contact type is permitted to retrieve the egress catalog
- **AND** the forward path (family-member access, delegated view) is answered in the
  design doc (Q4): egress catalog is hidden entirely from non-owner contacts until a
  separate spec change introduces per-contact capability gates

#### Scenario: Egress catalog actor enumeration is bounded to known actor identifiers

- **WHEN** the egress catalog is assembled
- **THEN** only actors from a registered actor registry (a server-side constant or
  configuration file, not a free-text DB field) are surfaced with their
  `display_name`
- **AND** unrecognized actor identifiers in the audit log are grouped into an
  `"other"` bucket with a display name of `"Other / Unrecognized"`
- **AND** the actor registry is the authoritative list of actor identifiers and
  display names; the implementation bead is responsible for populating it

### Requirement: Per-Butler Heartbeat Facts

The `/api/system/butlers/heartbeat` endpoint SHALL return the last-known heartbeat
timestamp and session activity summary for each registered butler.

#### Scenario: Heartbeat endpoint returns per-butler status

- **WHEN** `GET /api/system/butlers/heartbeat` is called
- **THEN** the response body contains:
  - `butlers: ButlerHeartbeat[]` -- one entry per registered butler, ordered by
    butler name ascending
- **AND** each `ButlerHeartbeat` entry contains:
  - `name: string` -- butler name (e.g., `"general"`, `"health"`)
  - `last_heartbeat_at: string | null` -- ISO 8601 UTC timestamp of the most recent
    liveness heartbeat recorded in the switchboard registry, or `null` if the butler
    has never registered
  - `last_session_at: string | null` -- ISO 8601 UTC timestamp of the most recent
    completed session for this butler, derived from `{schema}.sessions WHERE
    completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 1`. The `IS NOT NULL`
    filter is required because active sessions have `completed_at = NULL` and
    PostgreSQL sorts NULLs last by default in DESC -- omitting the filter risks
    returning an active (incomplete) session as the "last" session.
  - `active_session_count: number` -- count of sessions where `completed_at IS NULL`
    in `{schema}.sessions` at query time. Note: the sessions table has no `status`
    column; active sessions are identified by `completed_at IS NULL` (see
    `src/butlers/core/sessions.py` `sessions_active` implementation)
  - `heartbeat_age_seconds: number | null` -- seconds since `last_heartbeat_at`,
    or `null`; the frontend uses this to classify freshness without client-side math
- **AND** the response wraps in the standard `ApiResponse<HeartbeatFacts>` envelope

#### Scenario: Heartbeat data is read from the registry, not from live MCP calls

- **WHEN** the heartbeat endpoint assembles its response
- **THEN** it reads liveness data from the switchboard's liveness registry table
  (the same source the butler list page uses)
- **AND** it does NOT issue live MCP `status` tool calls to any butler
- **AND** if a butler's liveness entry is missing from the registry (never started or
  deregistered), `last_heartbeat_at` is `null` and `heartbeat_age_seconds` is `null`

#### Scenario: Session facts are read via the dashboard API's existing DB fan-out

- **WHEN** the heartbeat endpoint reads per-butler session data
- **THEN** it uses the `DatabaseManager` fan-out pattern that the dashboard API already
  uses for cross-butler queries (not new ad-hoc SQL per butler)
- **AND** if a butler's schema is unreachable, that butler's `last_session_at` and
  `active_session_count` are `null` and 0 respectively, and the entry is still
  included in the response with an `error: "schema_unreachable"` flag

### Requirement: System Page Privacy Contract

The System page and all `/api/system/*` endpoints SHALL operate under a strict access
contract. The egress catalog in particular is sensitive: it reveals which external actors
have processed data from this instance. The access contract governs who can see the page
and who can be enumerated in the egress catalog. Non-owner access to the egress catalog
MUST be denied in v1.

#### Scenario: Dashboard session boundary governs page visibility

- **WHEN** the `/system` route is rendered in the dashboard
- **THEN** access is governed by the same session/cookie boundary that protects every
  other dashboard route -- no additional gate is required at the page level for v1
- **AND** the dashboard's existing session boundary is owner-only in v1; no other
  contact has dashboard credentials

#### Scenario: Egress catalog is owner-contact-only in v1

- **WHEN** `GET /api/system/egress` is called
- **THEN** the endpoint performs an owner-contact assertion before returning data
  (see Egress Catalog access-is-owner-only scenario above)
- **AND** the assertion is performed by joining `public.contacts c` to
  `public.entities e` on `c.entity_id = e.id` and asserting `'owner' = ANY(e.roles)`
  (`public.contacts.roles` was dropped in migration `core_016`; the JOIN to
  `public.entities` is required)

#### Scenario: Non-owner access returns 403 for egress catalog

- **WHEN** a request to `GET /api/system/egress` arrives from a session that cannot
  be mapped to the owner contact
- **THEN** the endpoint returns HTTP 403 with `ErrorResponse.error.code = "forbidden"`
- **AND** the response does NOT include any partial egress data

#### Scenario: All other system endpoints are not additionally gated in v1

- **WHEN** `GET /api/system/instance`, `/api/system/database`, `/api/system/backups`,
  or `/api/system/butlers/heartbeat` is called
- **THEN** no owner-contact assertion beyond the dashboard session boundary is required
  in v1
- **AND** this contract is explicitly noted as a v1 simplification; if the dashboard
  gains non-owner viewers, these endpoints SHALL require a capability review before
  being exposed to non-owner sessions

## Source References

- Non-Negotiable Rule 1 (`about/heart-and-soul/vision.md`): user-federated, one user,
  full sovereignty -- this page makes sovereignty visible.
- `about/heart-and-soul/security.md` L19-28: "The owner has full access to everything.
  There is no access control within the system that restricts the owner." -- the egress
  catalog is the owner seeing their own data flows, not a permission gate on them.
- `about/heart-and-soul/security.md` L168-185: Sensitive Data Categories -- the egress
  catalog is an aggregation view, not a new sensitive-data store; the trust model
  governing the data it references is the same trust model that governs all data.
- `about/heart-and-soul/design-language.md` Settled Direction #3: "Owner sovereignty
  gets its own surface."
