## MODIFIED Requirements

### Requirement: Deployment Ledger Facts

ID: REQ-system-overview-page-001
Source: bu-dss7he.14; bu-viat6h.5
Scope: v1-mandatory

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
    `butlers up` process boot or a Helm release; `null` means the row
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

- **WHEN** the Helm release records its success or failure
- **THEN** its ledger row has `source="deploy"`, `serving_mode="image"`, and
  `serving_worktree=null`, because the Helm release records the deployed image
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
  development `./scripts/compose.sh` flow

#### Scenario: migration_head is a representative snapshot, not a cross-schema drift proof

- **WHEN** `migration_head` is recorded at boot time (`butlers up`)
- **THEN** it is read from a single representative schema's `alembic_version` table
  (the first-started butler daemon, conventionally `switchboard` per the existing
  `_PRIORITY_BUTLERS` start-order convention) rather than reconciling every butler
  schema's head
- **AND WHEN** it is recorded by the `butlers deployments record` verb, it is resolved by scanning
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
