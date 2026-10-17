## MODIFIED Requirements

### Requirement: Token Usage Ledger Schema
The system SHALL maintain a `public.token_usage_ledger` table as an append-only record of token-consumption evidence per catalog entry and invoked dispatch attempt. The table is range-partitioned on `recorded_at` with monthly partitions managed by pg_partman (90-day retention). Each new row for an invoked dispatch attempt SHALL reference `public.model_dispatch_attempts.id` through nullable `attempt_id` and SHALL classify its evidence with `usage_source IN ('measured', 'unmeasurable')`; historical rows remain `usage_source='measured'` with `attempt_id=NULL` because their attempt link cannot be reconstructed honestly. A row written for a goal invocation SHALL carry that invocation's `goal_id` and `invocation_id` and SHALL record the invocation role (`lead`, `child` or `monitor`) as `butler_name`, because goal invocations are not spawned by a butler; `session_id` SHALL be set only for historical rows and for model use outside goals that still has a session.

Source: RFC 0038 D2, D16, D17
Scope: v1-mandatory

#### Scenario: Ledger entry structure
- **WHEN** a token usage record is written to the ledger
- **THEN** it contains: `id` (UUID, part of composite PK), `catalog_entry_id` (UUID FK to model_catalog.id ON DELETE CASCADE, NOT NULL), `butler_name` (text, NOT NULL), `session_id` (UUID, nullable), `goal_id` (UUID, nullable), `invocation_id` (UUID, nullable), `input_tokens` (integer, NOT NULL, default 0), `output_tokens` (integer, NOT NULL, default 0), `recorded_at` (timestamptz, NOT NULL, default now(), part of composite PK)

#### Scenario: Goal invocation usage carries invocation lineage
- **WHEN** the goal runner records token usage for a lead or child invocation
- **THEN** the row carries the invocation's `goal_id` and `invocation_id`
- **AND** `butler_name` is the invocation role used as the model-resolution key
- **AND** `session_id` is NULL

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
- **WHEN** a catalog entry is deleted from `public.model_catalog`
- **THEN** all corresponding ledger rows are deleted via `ON DELETE CASCADE`

#### Scenario: Delete and recreate resets usage history
- **WHEN** a catalog entry is deleted and a new entry is created with the same alias
- **THEN** the new entry has a new UUID and zero usage history (all prior ledger rows were cascaded)
- **AND** any previously configured limits are also deleted

#### Scenario: Discretion calls have no session
- **WHEN** a discretion dispatcher call records token usage
- **THEN** `session_id` is NULL
- **AND** `butler_name` is the dispatcher's butler name (defaults to `"__discretion__"`)

### Requirement: Pre-Spawn Quota Check
The system SHALL provide a `check_token_quota(pool, catalog_entry_id)` function that evaluates whether a catalog entry's usage is within its configured limits for both rolling windows. The function SHALL let its caller distinguish a check that could not complete from an allowed result. The goal runner SHALL fail closed on a check that could not complete and SHALL NOT launch the invocation on that basis, because every goal budget check fails closed; every other caller (spawner, connector discretion screening) fails closed as well, skipping the candidate (bu-svqpzo.3).

Source: RFC 0038 D10, D18
Scope: v1-mandatory

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

#### Scenario: Quota check error refuses a goal invocation launch
- **WHEN** `check_token_quota()` fails due to a database error (timeout, connection refused, missing partition, etc.) while the goal runner prepares a lead or child invocation
- **THEN** the goal runner does not launch the invocation
- **AND** the failure is logged as a warning with the exception details
- **AND** the refusal is not reported as an allowed quota result

#### Scenario: Fail-closed on quota check error
- **WHEN** `check_token_quota()` fails due to a database error (timeout, connection refused, missing partition, etc.)
- **THEN** the function returns `QuotaStatus(allowed=False, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None, unavailable=True)`
- **AND** the failure is logged as an error with the exception details
- **AND** the spawner SHALL NOT invoke the candidate's adapter and SHALL record a `quota_skip` attempt whose `failure_reason` begins `Token quota unavailable for catalog entry`
- **AND** the spawner and discretion dispatcher SHALL continue their existing same-tier candidate loop, hard-blocking only when no candidate remains
- **AND** a readable catalog entry with no `token_limits` row remains unlimited (an unreadable budget is not an absent one)

### Requirement: Post-Spawn Ledger Recording
The system SHALL record token usage to the ledger whenever an adapter reports token consumption, regardless of whether the invocation completed successfully or failed. Tokens are consumed by the upstream API provider on invocation — a failed invocation still costs tokens and MUST count against the quota. For goal invocations the goal runner SHALL record this usage, metered from the runtime's usage stream, with the invocation's lineage; engineering executors outside goals keep their session-keyed recording.

Source: RFC 0038 D10, D17, D18
Scope: v1-mandatory

#### Scenario: Spawner records usage after successful session
- **WHEN** a spawner session completes successfully and the adapter reports `input_tokens` and `output_tokens`
- **AND** `catalog_entry_id` is available (model was resolved from the catalog)
- **THEN** a row is inserted into `public.token_usage_ledger` with the session's `catalog_entry_id`, `butler_name`, `session_id`, `input_tokens`, and `output_tokens`

#### Scenario: Spawner records usage after failed session
- **WHEN** a spawner session fails (runtime error, timeout, model returns unhelpful response) but the adapter DID report `input_tokens` and `output_tokens` before or during the failure
- **AND** `catalog_entry_id` is available
- **THEN** a row is inserted into the ledger with the reported token counts
- **AND** the usage counts against the quota (tokens were consumed by the provider regardless of session outcome)

#### Scenario: Goal runner records usage after a successful invocation
- **WHEN** a lead or child invocation completes successfully and the adapter reports `input_tokens` and `output_tokens`
- **AND** `catalog_entry_id` is available (model was resolved from the catalog)
- **THEN** a row is inserted into `public.token_usage_ledger` with the invocation's `catalog_entry_id`, role as `butler_name`, `goal_id`, `invocation_id`, `input_tokens`, and `output_tokens`

#### Scenario: Goal runner records usage after a failed invocation
- **WHEN** a lead or child invocation fails (runtime error, timeout, model returns unhelpful response) but the adapter DID report `input_tokens` and `output_tokens` before or during the failure
- **AND** `catalog_entry_id` is available
- **THEN** a row is inserted into the ledger with the reported token counts and the invocation's lineage
- **AND** the usage counts against the quota (tokens were consumed by the provider regardless of invocation outcome)

#### Scenario: Failover storm retains every measured attempt
- **WHEN** three failover-eligible invocations of one engineering-executor session report usage before failing and a fourth invocation succeeds with reported usage
- **THEN** the spawner SHALL write four `usage_source='measured'` ledger rows with four distinct non-null `attempt_id` values
- **AND** month-to-date measured spend SHALL equal the priced sum of all four attempts
- **AND** for a goal invocation, which relaunches at most once, the failed attempt and its relaunch each get their own measured row with its own `attempt_id` and `invocation_id`

### Requirement: Purpose-Tagged Spend Attribution
`public.token_usage_ledger` SHALL carry a nullable `purpose` column recording a coarse "why" dimension for each row, independent of `butler_name` (who spent) and the cache-aware token buckets (what was spent). `record_token_usage()` SHALL accept an optional `purpose` keyword argument and write it through unchanged; omitting it SHALL record `NULL`, never a fabricated default. For goal invocations the purpose SHALL be the goal's origin, because the trigger sources that named routes, classification and the `trigger` tool no longer start sessions.

Source: RFC 0038 D5, D17, D18
Scope: v1-mandatory

#### Scenario: Spawner stamps purpose from trigger_source
- **WHEN** `core.spawner._run()` records ledger usage for a completed or failed engineering executor session
- **THEN** the row's `purpose` is set to that session's `trigger_source` (e.g. `healing`, `qa`)
- **AND** no row carries `route`, `classification`, `schedule` or `trigger` as `purpose` for new usage, because those paths no longer start sessions

#### Scenario: Goal runner stamps purpose from goal origin
- **WHEN** the goal runner records ledger usage for a completed or failed lead or child invocation
- **THEN** the row's `purpose` is that goal's origin (`owner`, `obligation`, `third_party` or `staffer`)

#### Scenario: Discretion dispatcher stamps purpose and per-connector identity
- **WHEN** `DiscretionDispatcher.call()` records ledger usage
- **THEN** the row's `purpose` is `"discretion"`
- **AND** the row's `butler_name` is the caller-supplied `identity` (e.g. `"tg:<chat_id>"`) when provided, falling back to the dispatcher's constructor `butler_name` (default `"__discretion__"`) otherwise — replacing the prior behavior where every discretion call shared the same opaque `"__discretion__"` identity regardless of which connector triggered it

#### Scenario: Purpose omitted defaults to NULL, not a fabricated category
- **WHEN** a caller of `record_token_usage()` does not pass `purpose`
- **THEN** the inserted row's `purpose` is `NULL`
- **AND** `/spend` consumers MUST treat `NULL` as "unknown", never render it as a synthesized category

#### Scenario: Adapter invocation fails before returning usage
- **WHEN** the adapter raises an exception before returning any usage data (e.g., connection refused, immediate timeout)
- **THEN** the spawner SHALL write one `usage_source='unmeasurable'` ledger row linked to that dispatch attempt
- **AND** all four token buckets SHALL be `NULL`, never fabricated zeroes

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
- **WHEN** an invoked spawner attempt returns `None` or `{}` for usage
- **THEN** the spawner SHALL write one `usage_source='unmeasurable'` row for that attempt
- **AND** non-spawner callers that have no dispatch-attempt identity MAY retain their existing no-row behavior
