# System Overview Page

## Purpose

The System Overview page (`/system`) is the dashboard surface where the owner sees their
instance as infrastructure they own. It surfaces six ownership-fact domains: software
version and uptime, deployment provenance, database state, backup state, data egress
catalog, and per-butler heartbeats. This page exists because the doctrine in
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

### Requirement: Deployment Ledger Facts

The `/api/system/deployments` endpoint SHALL return the current (most recent) deployment
and a short recent history, drawn from `public.deployments` (bu-9r3hd.2, epic bu-9r3hd
"Deploy spine"). This is the ledger the owner reads to answer "what code is actually
running, and did it survive the last deploy" -- merged-but-undeployed drift is
otherwise invisible (bd bu-zhfd0: seven merged migrations sat dark in prod with no
record of when, or whether, any deploy actually took effect).

#### Scenario: Deployments endpoint returns current and recent history

- **WHEN** `GET /api/system/deployments` is called
- **THEN** the response body contains:
  - `current: DeploymentRecord | null` -- the most recent deployment row, or `null` if
    the ledger is empty (e.g. a fresh instance that has not yet completed a boot)
  - `recent: DeploymentRecord[]` -- up to 10 most recent deployment rows, newest first
    (including `current`)
- **AND** each `DeploymentRecord` contains:
  - `id: string` -- the ledger row's UUID
  - `git_sha: string` -- the git commit the running image was built from, or
    `"unknown"` if the image was built without the `GIT_SHA` build arg
  - `migration_head: string | null` -- a representative core-chain (`core_NNN`) Alembic
    revision id (see below); `null` if it could not be read from any schema that tracks
    the core chain. `null` is an honest unknown and MUST be rendered as such by the UI,
    never as a calm/blank value
  - `started_at: string` -- ISO 8601 UTC timestamp
  - `finished_at: string | null` -- ISO 8601 UTC timestamp; in v1 this always equals
    `started_at` (see below)
  - `result: "success" | "failed"`
  - `source: "boot" | "deploy" | null` -- identifies whether the row came from a
    `butlers up` process boot or the `butlers deploy` pipeline; `null` means the row
    predates provenance tracking and MUST remain an honest unknown
  - `serving_mode: "image" | "hotreload-worktree" | null` -- the runtime serving
    mode recorded with the row; `null` means inspection could not classify it
  - `serving_worktree: string | null` -- the stable `.worktrees/<name>` label for a
    detected linked-worktree bind mount, never a host-specific absolute path
- **AND** the response wraps in the standard `ApiResponse<DeploymentFacts>` envelope

#### Scenario: One ledger row per process boot, not per butler

- **WHEN** `butlers up` starts all configured butler daemons in one process
- **THEN** exactly one row is inserted into `public.deployments` for that boot -- not
  one per butler daemon -- because every butler shares one process/container in this
  deploy topology
- **AND** `result` is `"success"` when every configured butler daemon started
  successfully, `"failed"` otherwise
- **AND** the write is best-effort: a ledger-write failure is logged and does not
  block or fail startup (mirrors the `_ensure_owner_entity` bootstrap convention)

#### Scenario: Deployment source and serving mode are recorded honestly

- **WHEN** `butlers deploy` completes or fails after it has begun its pipeline
- **THEN** its ledger row has `source="deploy"`, `serving_mode="image"`, and
  `serving_worktree=null`, because the deploy command explicitly builds and recreates
  the profile-less baked-image service set
- **AND WHEN** `butlers up` records a process boot whose `/app/src` bind mount resolves
  to a linked `.worktrees/<name>` checkout in Linux mount metadata
- **THEN** its row has `source="boot"`, `serving_mode="hotreload-worktree"`, and the
  stable `.worktrees/<name>` label in `serving_worktree`
- **AND WHEN** a boot's source is absent or is a bind mount that cannot be identified as
  a linked worktree
- **THEN** `serving_mode` and `serving_worktree` are `null`, never a fabricated
  `"image"` classification
- **AND** rows written before this capability keep all three provenance fields `null`
  rather than being backfilled with a guess

#### Scenario: Bind-mounted worktree serving is unmistakable on the System page

- **WHEN** the current deployment record has `source="boot"`,
  `serving_mode="hotreload-worktree"`, and
  `serving_worktree=".worktrees/<name>"`
- **THEN** the Deployment tile renders the exact textual clause
  `boot from bind-mounted worktree .worktrees/<name> (hotreload)` using the semantic
  red-text token
- **AND** the System verdict banner repeats that clause as a red problem even when the
  baked image SHA is current and the commits-behind comparison is zero
- **AND** the clause is conveyed in text as well as color, so it remains clear to
  assistive technology and non-color perception

#### Scenario: git_sha is threaded from the Docker build

- **WHEN** the `butlers-app` image is built (`scripts/compose.sh` or a manual
  `docker build`)
- **THEN** the build accepts a `GIT_SHA` build arg (default `"unknown"`), which the
  Dockerfile bakes into the image as an environment variable
- **AND** `scripts/compose.sh` passes `--build-arg GIT_SHA=$(git rev-parse HEAD)`
  automatically -- the operator does not need to set it by hand for the normal
  `./scripts/compose.sh` / `./scripts/compose.sh --prod` flows

#### Scenario: migration_head is a representative snapshot, not a cross-schema drift proof

- **WHEN** `migration_head` is recorded at boot time (`butlers up`)
- **THEN** it is read from a single representative schema's `alembic_version` table
  (the first-started butler daemon, conventionally `switchboard` per the existing
  `_PRIORITY_BUTLERS` start-order convention) rather than reconciling every butler
  schema's head
- **AND WHEN** it is recorded by the `butlers deploy` verb, it is resolved by scanning
  the schemas that actually carry an `alembic_version` table (never assuming `public`,
  which tracks no chain) and taking the core-chain head — recording `null` when no
  schema tracks the core chain (see the deployment-and-drift capability)
- **AND** either way the recorded value is only the core (`core_NNN`) chain's head, so
  a stale non-core module row (e.g. `mem_007`) is never surfaced as the migration head
- **AND** this endpoint does NOT itself detect drift between schemas or between the
  recorded head and the live database state -- the hourly alembic-head vs per-schema
  DB-revision vs deployed-SHA comparison surfaced as a red `/system` clause is a
  separate capability (bu-9r3hd.1); this ledger is what that sentinel (and this
  endpoint) reads to answer "what was last recorded as deployed"

#### Scenario: The Deployment tile renders a null migration_head as an explicit unknown

- **WHEN** the `/system` Deployment tile renders a `DeploymentRecord` whose
  `migration_head` is `null`
- **THEN** it shows an explicit "head unknown" state with warning (amber) emphasis,
  visually distinct from a real revision id
- **AND** it never renders the null head as a blank, calm, or all-clear value

#### Scenario: Deployments endpoint degrades gracefully

- **WHEN** the ledger table exists but has no rows yet (fresh instance, or a build
  that never ran through `butlers up`)
- **THEN** the response is HTTP 200 with `current: null` and `recent: []` -- not an
  error
- **AND** HTTP 503 is returned only when the underlying query itself fails
  (permission denied, connection error), matching the `/api/system/database` contract

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
database backup, plus a short history of recent backup events, plus a genuinely
verified health verdict for the most recent backup and the most recent weekly restore
drill. No field in this response SHALL be a hardcoded or assumed value -- every status
is derived from an actual check (bu-9r3hd.5).

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
    `completed_at: string`, `size_bytes: number`, and `status: "healthy" | "corrupt" |
    "empty"` -- a REAL, verified per-artifact verdict (never a hardcoded constant):
    `"empty"` when the file is smaller than a real dump could plausibly be, `"corrupt"`
    when the file fails a full gzip-integrity read (validating gzip's own embedded
    CRC32 + size footer), `"healthy"` otherwise. Verification is computed live per
    request but memoized per `(path, mtime, size)` so an unchanged file is never
    re-verified on every poll.
  - `last_backup_status: "healthy" | "corrupt" | "empty" | "missing"` -- convenience
    mirror of `backup_history[0].status`, or `"missing"` when there is no backup file
  - `backup_stale: boolean` -- `true` when the most recent backup's age exceeds the
    expected daily-cadence-plus-slack window (36 hours)
  - `restore_drill: RestoreDrillFacts` -- result of the most recent weekly restore-drill
    attempt (see Requirement: Weekly Restore Drill), having `checked_at: string | null`,
    `result: "pass" | "fail" | "pending" | "degraded"`, and `detail: string | null`.
    `"pending"` means the drill has never run yet -- a real "unknown" state, never
    presented as a passing drill.
- **AND** the response wraps in the standard `ApiResponse<BackupFacts>` envelope

#### Scenario: Unavailable backup source degrades gracefully

- **WHEN** the backup metadata source (Minio/S3, filesystem) is unreachable
- **THEN** `last_backup_at` and `last_backup_size_bytes` are `null`
- **AND** `backup_source_reachable` is `false`
- **AND** `backup_history` is an empty array
- **AND** `last_backup_status` is `"missing"` and `backup_stale` is `false`
- **AND** the response is HTTP 200 with the degraded payload -- not HTTP 503
- **AND** the frontend renders a "backup status unavailable" indicator rather than an
  error state

#### Scenario: Restore-drill ledger read failure degrades only that field

- **WHEN** the restore-drill ledger (`public.audit_log`) cannot be read (switchboard
  pool unavailable, or the query itself fails)
- **THEN** `restore_drill.result` is `"degraded"` with a non-null `detail`
- **AND** every other field in the response (backup recency, artifact health) is
  unaffected
- **AND** the response is still HTTP 200 -- a ledger read failure never fails the
  whole endpoint

### Requirement: Weekly Restore Drill

Once a week, the system SHALL attempt to actually restore the most recent backup
artifact into a scratch database, verify the restore produced real data, tear the
scratch database down, and record the true pass/fail result -- proving the backup is
usable, not merely present (bu-9r3hd.5).

#### Scenario: Drill succeeds

- **WHEN** the weekly restore-drill loop ticks and a backup file is present
- **THEN** it creates a scratch database, restores the backup into it via the real
  Postgres client (`psql`, required for `COPY ... FROM stdin` support that a
  SQL-statement-only executor cannot replay), asserts the restore produced at least
  one non-system table, and drops the scratch database
- **AND** it records `result: "pass"` to `public.audit_log`, readable via
  `GET /api/system/backups`'s `restore_drill` field

#### Scenario: Drill fails at any stage

- **WHEN** any stage fails -- the artifact is corrupt, `createdb` is denied by a
  Postgres role lacking `CREATEDB`, the restore itself errors, the restore produces
  zero tables, or the process times out
- **THEN** the scratch database is unconditionally dropped (cleanup always runs, even
  on failure)
- **AND** `result: "fail"` is recorded with a human-readable `detail` describing what
  failed
- **AND** the loop is never crashed by a failed tick -- it keeps retrying on the next
  weekly cadence

#### Scenario: No backup file yet

- **WHEN** the weekly restore-drill loop ticks and no backup file exists
- **THEN** the tick is skipped with no ledger write -- a legitimate absence, not a
  failure to record

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
  `/api/system/butlers/heartbeat`, or `/api/system/deployments` is called
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
