## ADDED Requirements

### Requirement: Token Usage Ledger Schema
The system SHALL maintain a `shared.token_usage_ledger` table as an append-only record of token consumption per catalog entry. The table is range-partitioned on `recorded_at` with monthly partitions managed by pg_partman (90-day retention).

#### Scenario: Ledger entry structure
- **WHEN** a token usage record is written to the ledger
- **THEN** it contains: `id` (UUID, part of composite PK), `catalog_entry_id` (UUID FK to model_catalog.id ON DELETE CASCADE, NOT NULL), `butler_name` (text, NOT NULL), `session_id` (UUID, nullable), `input_tokens` (integer, NOT NULL, default 0), `output_tokens` (integer, NOT NULL, default 0), `recorded_at` (timestamptz, NOT NULL, default now(), part of composite PK)

#### Scenario: Composite primary key for partitioning
- **WHEN** the ledger table is created
- **THEN** the primary key is `(id, recorded_at)` as required by PostgreSQL range partitioning on `recorded_at`

#### Scenario: Query-optimized index
- **WHEN** the ledger table is created
- **THEN** a composite index `idx_ledger_entry_time` on `(catalog_entry_id, recorded_at)` SHALL exist to support the standard quota-check query pattern: `WHERE catalog_entry_id = $1 AND recorded_at > $2`

#### Scenario: Monthly partitioning with pg_partman
- **WHEN** the Alembic migration runs and the `pg_partman` extension is available
- **THEN** it creates range partitions for the current month and 2 months ahead
- **AND** registers the table with pg_partman for automatic monthly partition creation and 90-day retention

#### Scenario: Monthly partitioning without pg_partman
- **WHEN** the Alembic migration runs and the `pg_partman` extension is NOT available
- **THEN** it creates range partitions for the current month and 5 months ahead (wider buffer to allow time for manual intervention)
- **AND** logs a warning that pg_partman is not installed and partitions must be created manually or via a scheduled task
- **AND** the migration does NOT fail — partitioning works without pg_partman, only automated maintenance is missing

#### Scenario: Cascade on catalog entry deletion
- **WHEN** a catalog entry is deleted from `shared.model_catalog`
- **THEN** all corresponding ledger rows are deleted via `ON DELETE CASCADE`

#### Scenario: Delete and recreate resets usage history
- **WHEN** a catalog entry is deleted and a new entry is created with the same alias
- **THEN** the new entry has a new UUID and zero usage history (all prior ledger rows were cascaded)
- **AND** any previously configured limits are also deleted

#### Scenario: Discretion calls have no session
- **WHEN** a discretion dispatcher call records token usage
- **THEN** `session_id` is NULL
- **AND** `butler_name` is the dispatcher's butler name (defaults to `"__discretion__"`)

### Requirement: Token Limits Schema
The system SHALL maintain a `shared.token_limits` table storing per-catalog-entry rolling-window token budgets. Catalog entries without a row in this table are unlimited.

#### Scenario: Limits entry structure
- **WHEN** a token limit is configured for a catalog entry
- **THEN** the row contains: `id` (UUID PK), `catalog_entry_id` (UUID, UNIQUE, FK to model_catalog.id ON DELETE CASCADE, NOT NULL), `limit_24h` (bigint, nullable — NULL means unlimited for 24h window), `limit_30d` (bigint, nullable — NULL means unlimited for 30d window), `reset_24h_at` (timestamptz, nullable), `reset_30d_at` (timestamptz, nullable), `created_at` (timestamptz, NOT NULL, default now()), `updated_at` (timestamptz, NOT NULL, default now())

#### Scenario: Token counting unit
- **WHEN** limits and usage are compared
- **THEN** the unit is total tokens (`input_tokens + output_tokens`) for both the limit value and the usage aggregation

#### Scenario: No limits row means unlimited
- **WHEN** a catalog entry has no corresponding row in `shared.token_limits`
- **THEN** the entry has no token budget enforcement
- **AND** usage is still recorded to the ledger (for visibility)

#### Scenario: Cascade on catalog entry deletion
- **WHEN** a catalog entry is deleted from `shared.model_catalog`
- **THEN** the corresponding limits row is deleted via `ON DELETE CASCADE`

#### Scenario: Disabled entry with limits re-enabled via override
- **WHEN** a catalog entry is globally disabled but a butler override re-enables it
- **THEN** the global `token_limits` row still applies to that butler's usage of the entry
- **AND** quota enforcement uses the same limits regardless of whether the entry was enabled globally or via override

### Requirement: Independent Window Resets
Each rolling window (24h and 30d) SHALL have its own independent reset marker. Resetting one window does not affect the other.

#### Scenario: Reset 24h window only
- **WHEN** the operator resets the 24h window for a catalog entry
- **THEN** `reset_24h_at` is set to `now()`
- **AND** `reset_30d_at` is unchanged
- **AND** the effective 24h window start becomes `GREATEST(reset_24h_at, now() - interval '24 hours')`
- **AND** the 30d window calculation is unaffected

#### Scenario: Reset 30d window only
- **WHEN** the operator resets the 30d window for a catalog entry
- **THEN** `reset_30d_at` is set to `now()`
- **AND** `reset_24h_at` is unchanged

#### Scenario: Reset both windows
- **WHEN** the operator resets both windows for a catalog entry
- **THEN** both `reset_24h_at` and `reset_30d_at` are set to `now()`

#### Scenario: Reset on entry without limits row
- **WHEN** the operator resets a window for a catalog entry that has no `token_limits` row
- **THEN** a new `token_limits` row is created with `limit_24h = NULL`, `limit_30d = NULL`, and the appropriate `reset_*_at` set to `now()`

### Requirement: Pre-Spawn Quota Check
The system SHALL provide a `check_token_quota(pool, catalog_entry_id)` function that evaluates whether a catalog entry's usage is within its configured limits for both rolling windows.

#### Scenario: QuotaStatus return type
- **WHEN** `check_token_quota()` is called
- **THEN** it returns a `QuotaStatus` dataclass with fields: `allowed` (bool), `usage_24h` (int), `limit_24h` (int | None), `usage_30d` (int), `limit_30d` (int | None)

#### Scenario: Entry with no limits configured
- **WHEN** `check_token_quota()` is called for a catalog entry with no row in `token_limits`
- **THEN** it returns `QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)` without querying the ledger (fast path — no limits means nothing to enforce)
- **AND** actual usage figures for unlimited entries are only computed by the dashboard list endpoint's own aggregation CTE, not by the spawner hot path

#### Scenario: Usage within both limits
- **WHEN** `check_token_quota()` is called and usage is below both `limit_24h` and `limit_30d`
- **THEN** `allowed` is `True`

#### Scenario: 24h limit exceeded
- **WHEN** `check_token_quota()` is called and 24h usage equals or exceeds `limit_24h`
- **THEN** `allowed` is `False`
- **AND** `usage_24h >= limit_24h`

#### Scenario: 30d limit exceeded
- **WHEN** `check_token_quota()` is called and 30d usage equals or exceeds `limit_30d`
- **THEN** `allowed` is `False`
- **AND** `usage_30d >= limit_30d`

#### Scenario: One window unlimited, other exceeded
- **WHEN** `limit_24h` is NULL (unlimited) and `limit_30d` is exceeded
- **THEN** `allowed` is `False` (either window can block)

#### Scenario: Reset markers affect window calculation
- **WHEN** `reset_24h_at` is set and falls within the 24h window
- **THEN** usage is summed only from `GREATEST(reset_24h_at, now() - interval '24 hours')` onward
- **AND** `reset_30d_at` independently controls the 30d window start

#### Scenario: Single-query execution
- **WHEN** `check_token_quota()` is called
- **THEN** limits and both window usages are resolved in a single database round-trip (CTE-based query)

#### Scenario: Fail-open on quota check error
- **WHEN** `check_token_quota()` fails due to a database error (timeout, connection refused, missing partition, etc.)
- **THEN** the function returns `QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)`
- **AND** the failure is logged as a warning with the exception details
- **AND** the spawn proceeds (fail-open — the guardrail must never become a single point of failure)

### Requirement: Post-Spawn Ledger Recording
The system SHALL record token usage to the ledger whenever an adapter reports token consumption, regardless of whether the session completed successfully or failed. Tokens are consumed by the upstream API provider on invocation — a failed session still costs tokens and MUST count against the quota.

#### Scenario: Spawner records usage after successful session
- **WHEN** a spawner session completes successfully and the adapter reports `input_tokens` and `output_tokens`
- **AND** `catalog_entry_id` is available (model was resolved from the catalog)
- **THEN** a row is inserted into `shared.token_usage_ledger` with the session's `catalog_entry_id`, `butler_name`, `session_id`, `input_tokens`, and `output_tokens`

#### Scenario: Spawner records usage after failed session
- **WHEN** a spawner session fails (runtime error, timeout, model returns unhelpful response) but the adapter DID report `input_tokens` and `output_tokens` before or during the failure
- **AND** `catalog_entry_id` is available
- **THEN** a row is inserted into the ledger with the reported token counts
- **AND** the usage counts against the quota (tokens were consumed by the provider regardless of session outcome)

#### Scenario: Adapter invocation fails before returning usage
- **WHEN** the adapter raises an exception before returning any usage data (e.g., connection refused, immediate timeout)
- **THEN** no ledger row is written (there are no token counts to record)

#### Scenario: Discretion dispatcher records usage
- **WHEN** a discretion dispatcher call completes (successfully or with an error) and the adapter reports token usage
- **THEN** a row is inserted into the ledger with `session_id = NULL`

#### Scenario: Best-effort recording
- **WHEN** the ledger INSERT fails (e.g., missing partition, connection error)
- **THEN** the failure is logged as a warning
- **AND** the session result is still returned to the caller (never blocks)

#### Scenario: No recording for TOML-fallback resolution
- **WHEN** the spawner resolved the model from `butler.toml` (not the catalog)
- **THEN** no ledger row is written (there is no `catalog_entry_id`)

#### Scenario: No recording when adapter reports no usage
- **WHEN** the adapter returns `None` or `{}` for usage
- **THEN** no ledger row is written

### Requirement: Hard Block on Quota Exhaustion
The system SHALL hard-block session spawning when a catalog entry's token quota is exhausted. Note: the pre-spawn check and post-spawn record are not atomic, so concurrent spawns targeting the same catalog entry can overshoot the limit by up to N sessions' worth of tokens (where N is the number of concurrent spawns). This is accepted — the limit is a guardrail, not a billing boundary.

#### Scenario: Spawner blocks on quota exceeded
- **WHEN** the spawner calls `check_token_quota()` after `resolve_model()` and `allowed` is `False`
- **THEN** the spawner does NOT invoke the adapter
- **AND** returns a `SpawnerResult` with `success=False` and an error message indicating which window(s) are exhausted and current usage vs. limit

#### Scenario: Discretion dispatcher blocks on quota exceeded
- **WHEN** the discretion dispatcher resolves a model and `check_token_quota()` returns `allowed=False`
- **THEN** the dispatcher raises `RuntimeError` with a message indicating quota exhaustion

#### Scenario: Error message includes quota details
- **WHEN** a spawn is blocked due to quota exhaustion
- **THEN** the error message includes: the catalog entry alias, which window(s) are exceeded, current usage, and the configured limit

### Requirement: Token Limits API
The system SHALL provide REST API endpoints for managing token limits and viewing usage.

#### Scenario: List catalog entries with usage
- **WHEN** `GET /api/settings/models` is called
- **THEN** each entry in the response includes `usage_24h` (int), `usage_30d` (int), `limit_24h` (int | None), and `limit_30d` (int | None)
- **AND** usage is aggregated via a single CTE across all catalog entries (not N+1 queries)

#### Scenario: Set limits for a catalog entry
- **WHEN** `PUT /api/settings/models/{entry_id}/limits` is called with body `{"limit_24h": int | null, "limit_30d": int | null}`
- **THEN** the `token_limits` row is upserted for that catalog entry
- **AND** setting both limits to null deletes the `token_limits` row

#### Scenario: Limit value validation
- **WHEN** `PUT /api/settings/models/{entry_id}/limits` is called with a limit value that is not null
- **THEN** the value MUST be a positive integer (>= 1)
- **AND** a value of 0 or negative is rejected with HTTP 422 and a descriptive error message

#### Scenario: Reset usage windows
- **WHEN** `POST /api/settings/models/{entry_id}/reset-usage` is called with body `{"window": "24h" | "30d" | "both"}`
- **THEN** the corresponding `reset_24h_at` and/or `reset_30d_at` is set to `now()` on the `token_limits` row
- **AND** if no `token_limits` row exists, one is created with null limits and the appropriate reset timestamp(s)

#### Scenario: Get detailed usage for a single entry
- **WHEN** `GET /api/settings/models/{entry_id}/usage` is called
- **THEN** the response includes: `usage_24h`, `usage_30d`, `limit_24h`, `limit_30d`, `reset_24h_at`, `reset_30d_at`, `percent_24h` (float | null), `percent_30d` (float | null)
- **AND** `percent_*` is null when the corresponding limit is null

#### Scenario: Resolve-model preview includes quota status
- **WHEN** `GET /api/butlers/{name}/resolve-model?complexity=X` resolves a model
- **THEN** the response includes `quota_blocked` (bool), `usage_24h` (int), `limit_24h` (int | None), `usage_30d` (int), `limit_30d` (int | None)
- **AND** `quota_blocked` is `True` when either window's usage meets or exceeds its limit
- **AND** the endpoint always queries actual usage from the ledger (it does NOT use the `check_token_quota()` fast path that returns zeroes for unlimited entries, because the preview must show real usage to match the dashboard's `used/-` display)

### Requirement: Dashboard Usage Columns
The model catalog table on the settings page SHALL display token usage alongside configured limits for each catalog entry.

#### Scenario: Usage bar with limit configured
- **WHEN** a catalog entry has a 24h or 30d limit configured
- **THEN** the corresponding column shows a mini horizontal progress bar with a green→yellow→red gradient and text `used/limit` (e.g., "142K / 500K")

#### Scenario: Usage display without limit
- **WHEN** a catalog entry has no limit configured for a window
- **THEN** the column shows `used/-` (e.g., "42K / -") with no progress bar
- **AND** usage is still visible so the operator can monitor consumption without enforcement

#### Scenario: Color thresholds
- **WHEN** a progress bar is displayed
- **THEN** the color is green from 0–60%, yellow from 60–85%, red from 85–100%, and red with a "BLOCKED" badge above 100%

#### Scenario: Reset button per entry
- **WHEN** the operator clicks the reset icon-button next to a usage bar
- **THEN** the corresponding window's usage is reset via the `POST /api/settings/models/{entry_id}/reset-usage` endpoint
- **AND** the bar updates immediately to reflect the reset

#### Scenario: Tooltip on hover
- **WHEN** the operator hovers over a usage bar
- **THEN** a tooltip shows: exact token counts (e.g., "142,312 / 500,000 tokens"), percentage used, and the label "Rolling 24h window" or "Rolling 30d window"
- **AND** if a manual reset was applied, the tooltip also shows "Last reset: <relative time>" (e.g., "Last reset: 2h ago")

#### Scenario: Inline limit editing
- **WHEN** the operator clicks the limit portion of the `used/limit` text
- **THEN** an inline editor allows setting or changing the limit value
- **AND** saving calls `PUT /api/settings/models/{entry_id}/limits`
