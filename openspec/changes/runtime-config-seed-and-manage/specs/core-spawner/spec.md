## MODIFIED Requirements

### Requirement: Spawner reads hot config fields per-spawn
The Spawner SHALL accept a `RuntimeConfigAccessor` and read hot fields (model, runtime_type, args, session_timeout_s) from it on every `trigger()` call instead of from the static `ButlerConfig`.

Source: RFC 0001 §Trigger Pipeline, RFC 0002 §Core Tools
Scope: v1-mandatory

#### Scenario: Model resolved from accessor fallback
- **WHEN** `trigger()` is called and catalog model resolution fails or returns no result
- **THEN** the Spawner SHALL use `accessor.get().model` as the fallback (not the toml config)

#### Scenario: Runtime type from accessor
- **WHEN** `trigger()` is called
- **THEN** the Spawner SHALL use `accessor.get().runtime_type` to select the runtime adapter

#### Scenario: Args from accessor
- **WHEN** `trigger()` is called
- **THEN** the Spawner SHALL merge `accessor.get().args` with any per-trigger args

#### Scenario: Session timeout from accessor
- **WHEN** `trigger()` is called
- **THEN** the Spawner SHALL use `accessor.get().session_timeout_s` for the `asyncio.wait_for` timeout

#### Scenario: Dashboard model change takes effect within 30s
- **WHEN** a user changes `model` via the dashboard PATCH endpoint
- **THEN** new sessions spawned after the accessor TTL expires (≤30s) SHALL use the updated model

#### Scenario: Accessor DB failure during trigger — use stale cache
- **WHEN** `accessor.get()` is called during `trigger()` but the DB query fails
- **AND** the accessor has a previously cached value
- **THEN** the Spawner SHALL proceed with the stale cached config
- **AND** log a warning about the stale config

### Requirement: Cold fields read at construction only
The Spawner SHALL read `max_concurrent` and `max_queued` from the accessor once at construction time. These values are used to size the asyncio.Semaphore and queue limit.

Source: RFC 0001 §Concurrency Control
Scope: v1-mandatory

#### Scenario: Concurrency limit from DB
- **WHEN** the Spawner is constructed
- **THEN** the asyncio.Semaphore capacity SHALL be set from `accessor.get().max_concurrent`

#### Scenario: Queue limit from DB
- **WHEN** the Spawner is constructed
- **THEN** the max queued sessions limit SHALL be set from `accessor.get().max_queued`

#### Scenario: Concurrency change requires restart
- **WHEN** a user changes `max_concurrent` via the dashboard
- **THEN** the change SHALL NOT take effect until the daemon is restarted
