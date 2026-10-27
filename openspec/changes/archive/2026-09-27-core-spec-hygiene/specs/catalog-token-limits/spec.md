## MODIFIED Requirements

### Requirement: Purpose-Tagged Spend Attribution
`public.token_usage_ledger` SHALL carry a nullable `purpose` column recording a coarse "why" dimension for each row, independent of `butler_name` (who spent) and the cache-aware token buckets (what was spent). `record_token_usage()` SHALL accept an optional `purpose` keyword argument and write it through unchanged; omitting it SHALL record `NULL`, never a fabricated default.

#### Scenario: Spawner stamps purpose from trigger_source
- **WHEN** `core.spawner._run()` records ledger usage for a completed or failed session
- **THEN** the row's `purpose` is set to that session's `trigger_source` (e.g. `route`, `schedule`, `classification`, `healing`, `dashboard`, `qa`, `external`, `trigger`)

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
