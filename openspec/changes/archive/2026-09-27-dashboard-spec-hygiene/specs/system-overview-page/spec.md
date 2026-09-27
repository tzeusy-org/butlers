## MODIFIED Requirements

### Requirement: Deployment Ledger Facts

The `/api/system/deployments` endpoint SHALL return the current (most recent) deployment
and a short recent history, drawn from `public.deployments`. This is the ledger the owner reads to answer "what code
is actually running, and did it survive the last deploy", so merged-but-undeployed drift
is visible.

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
  separate capability; this ledger is what that sentinel (and this
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
  (`public.audit_log`) -- no new write path is introduced. Actor identity is derived from the `action` column (aliased
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

### Requirement: Standing Infrastructure Conditions

The `/api/system/conditions` endpoint SHALL expose the durable infrastructure
condition ledger (`public.infra_conditions`), and the System page SHALL render
a Standing Conditions panel from it, so an escalating outage is visible on
the dashboard instead of only via direct database access.

#### Scenario: Conditions endpoint returns ledger episodes

- **WHEN** `GET /api/system/conditions` is called with optional `source`,
  `state`, `offset`, and `limit` filters
- **THEN** the response body contains `conditions: ConditionEntry[]`,
  `total: number`, and `conditions_available: boolean`, ordered by
  `first_detected_at` descending
- **AND** each `ConditionEntry` includes `source`, `fingerprint`, `episode`,
  `state` (`open` | `aging` | `resolved`), `escalation_level` (`L0`-`L3`),
  `first_detected_at`, `last_confirmed_at`, `resolved_at`, and
  `recovered_after_s`, matching the ledger's stored evidence

#### Scenario: A failed ledger query degrades honestly, never a fabricated all-clear

- **WHEN** the underlying `infra_conditions` query fails (unreachable pool,
  permission error)
- **THEN** the endpoint still returns HTTP 200 with `conditions_available:
  false` and an empty `conditions` list
- **AND** the System page renders a named "unavailable" notice, distinct from
  both an ordinary empty ledger and a transport-level error

#### Scenario: Standing Conditions panel shows escalation and recovery provenance

- **WHEN** the System page renders the Standing Conditions panel
- **THEN** each active (`open`/`aging`) episode shows its source, escalation
  level, and time since first detected
- **AND** each `resolved` episode shows when it resolved and how long the
  condition was active before recovering (`recovered_after_s`), rather than
  disappearing from the panel with no trace of the outage having occurred

#### Scenario: Standing conditions surface how many QA dispatches they suppressed

- **WHEN** an active condition has suppressed one or more QA dispatch
  decisions (Gate 5.5, `decision="infra_condition_open"`, joined on the
  shared `fingerprint` identity)
- **THEN** the panel shows a count of suppressed QA dispatches for that
  condition, so each suppression is traceable back to the condition that
  caused it
