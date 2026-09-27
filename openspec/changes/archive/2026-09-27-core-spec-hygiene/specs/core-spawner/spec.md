## MODIFIED Requirements

### Requirement: Dynamic Model Resolution at Spawn Time
The spawner SHALL resolve the model dynamically at spawn time using the model catalog instead of reading a static model from `butler.toml`. The `trigger()` method gains a `complexity` parameter that drives model selection. The spawner MAY use same-tier failover only after the initial catalog candidate has been selected.

#### Scenario: Trigger with complexity parameter
- **WHEN** `trigger(prompt, trigger_source, complexity="reasoning")` is called
- **THEN** the spawner calls `resolve_model(butler_name, "reasoning")` to determine the runtime type, model ID, and extra args

#### Scenario: Trigger without complexity parameter
- **WHEN** `trigger(prompt, trigger_source)` is called without a complexity parameter
- **THEN** the complexity defaults to `workhorse`

#### Scenario: Catalog resolution selects the invocation model
- **WHEN** `resolve_model()` returns a result
- **THEN** the returned `runtime_type`, `model_id`, and `extra_args` are used for the invocation

#### Scenario: Catalog empty fails closed
- **WHEN** `resolve_model()` returns `None` (no matching entries) or fails
- **THEN** a live Spawner with a database pool returns `ModelResolutionError: catalog_unavailable` or `ModelResolutionError: no_eligible_catalog_entries` before invocation because catalog-keyed permission, quota, ceiling, breaker, and provenance gates cannot run
- **AND** an explicit pool-free direct-adapter harness may invoke `DEFAULT_RUNTIME_TYPE` with no explicit model only after its adapter baseline satisfies the dispatch intent
- **AND** it never pairs a hard-coded provider model with a different provider's runtime

#### Scenario: Populated catalog with no fitting candidate fails closed
- **WHEN** intent-aware resolution returns no selection and its receipt contains excluded catalog candidates
- **THEN** the spawner returns a pre-invocation `ModelResolutionError` naming the bounded failure class and required capability findings
- **AND** the failed `SpawnerResult` retains the prompt-free resolution receipt
- **AND** no runtime adapter, speculative failover candidate, or fake model dispatch attempt is invoked

#### Scenario: Unregistered catalog runtime fails closed
- **WHEN** a selected initial or failover catalog entry names an unregistered runtime type
- **THEN** an initial selection returns `ModelResolutionError: unregistered_runtime_type` before invocation
- **AND** an unregistered failover candidate records a non-invoked `runtime_failure` attempt, excludes that catalog entry, and continues the bounded same-tier search
- **AND** if no registered same-tier candidate remains, the logical session ends with ordinary failover exhaustion
- **AND** it does not substitute the default runtime while retaining the incompatible catalog model

#### Scenario: Post-resolution overrides preserve original intent fit
- **WHEN** a spend rule or private-content policy replaces the initially selected catalog entry
- **THEN** the spawner verifies that the replacement was fit-eligible for the original dispatch intent and effective tier before receipt projection, prewarm, session creation, or runtime invocation
- **AND** a replacement recorded as `excluded_hard_fit` returns `ModelResolutionError: post_resolution_selection_unfit`
- **AND** its original capability exclusions remain present in the failed result's resolution receipt

#### Scenario: Runtime args sourced only from the catalog
- **WHEN** catalog resolution returns `extra_args`
- **THEN** the catalog `extra_args` are forwarded verbatim to the adapter as `runtime_args`
- **AND** there is no butler-scoped args fallback; when the catalog returns no args, the kwarg is omitted

#### Scenario: Session record includes model resolution metadata
- **WHEN** a session is created via `session_create()`
- **THEN** the session record includes: the resolved `model` (catalog model ID, or NULL in explicit pool-free direct-adapter mode), `runtime_type`, `complexity` tier, and resolution source (`catalog` or `direct_runtime`)

#### Scenario: Initial catalog candidate establishes failover tier
- **WHEN** `resolve_model()` returns a catalog result for a trigger
- **THEN** the spawner SHALL treat that result's effective complexity tier as the
  failover tier for the logical session
- **AND** subsequent automatic failover attempts SHALL use only that exact tier

#### Scenario: Catalog resolution failure fails closed
- **WHEN** initial catalog resolution returns `None` for every eligible tier or raises
  before a catalog candidate is selected
- **THEN** a live pooled spawner SHALL refuse invocation with a `ModelResolutionError`
- **AND** same-tier model failover SHALL NOT run because no catalog tier was established
- **AND** explicit pool-free direct-adapter mode remains a test-harness path, not a live fallback

### Requirement: Spawner resolves hot config fields per-spawn from the model catalog
The Spawner SHALL resolve the hot fields (model, runtime_type, args, session_timeout_s) on every `trigger()` call rather than reading them from the static `ButlerConfig`. These fields live on `public.model_catalog` (resolved per complexity tier), not on the `runtime_config` table. The Spawner calls `resolve_model_with_effective_tier()` (`src/butlers/core/model_routing.py`) to obtain the catalog entry id, runtime_type, args, and session_timeout_s for the chosen tier. The `RuntimeConfigAccessor` is still consulted, but only for cold fields (core_groups, max_concurrent, max_queued).

Source: RFC 0001 §Trigger Pipeline, RFC 0002 §Core Tools, migration core_073

#### Scenario: Model resolved from the catalog
- **WHEN** `trigger()` is called
- **THEN** the Spawner SHALL resolve the model from `public.model_catalog` via `resolve_model_with_effective_tier()`
- **AND** if catalog resolution fails or returns no result, a live Spawner with a database pool SHALL return a pre-invocation `ModelResolutionError`
- **AND** only explicit pool-free direct-adapter mode may invoke the registered runtime with no explicit model after capability fit
- **AND** a populated no-winner receipt SHALL return a pre-invocation `ModelResolutionError`

#### Scenario: Runtime type from the catalog
- **WHEN** `trigger()` is called
- **THEN** the Spawner SHALL use the catalog entry's `runtime_type` to select the runtime adapter

#### Scenario: Args from the catalog
- **WHEN** `trigger()` is called
- **THEN** the Spawner SHALL merge the catalog entry's `args` with any per-trigger args

#### Scenario: Session timeout from the catalog
- **WHEN** `trigger()` is called
- **THEN** the Spawner SHALL use the catalog entry's `session_timeout_s` for the `asyncio.wait_for` timeout
- **AND** the Spawner SHALL forward that same timeout value into `runtime.invoke(...)`

#### Scenario: Session timeout is per invocation only
- **WHEN** `trigger()` is called by a higher-level workflow orchestrator
- **THEN** `session_timeout_s` limits only that spawned runtime session
- **AND** any broader workflow deadline is enforced by the caller, not by the Spawner

#### Scenario: Dashboard model change takes effect on the next spawn
- **WHEN** a user changes the model for a complexity tier via the Models tab (`PATCH /api/model-settings`, backed by `public.model_catalog`)
- **THEN** new sessions spawned afterward SHALL use the updated catalog entry, since the model is resolved from the catalog per `trigger()`

#### Scenario: Accessor DB failure during trigger — use stale cache
- **WHEN** `accessor.get()` is called during `trigger()` but the DB query fails
- **AND** the accessor has a previously cached value
- **THEN** the Spawner SHALL proceed with the stale cached config
- **AND** log a warning about the stale config

### Requirement: Cold fields read at construction only
The Spawner SHALL read `max_concurrent` and `max_queued` from the accessor once at construction time. These values are used to size the asyncio.Semaphore and queue limit.

Source: RFC 0001 §Concurrency Control

#### Scenario: Concurrency limit from DB
- **WHEN** the Spawner is constructed
- **THEN** the asyncio.Semaphore capacity SHALL be set from `accessor.get().max_concurrent`

#### Scenario: Queue limit from DB
- **WHEN** the Spawner is constructed
- **THEN** the max queued sessions limit SHALL be set from `accessor.get().max_queued`

#### Scenario: Concurrency change requires restart
- **WHEN** a user changes `max_concurrent` via the dashboard
- **THEN** the change SHALL NOT take effect until the daemon is restarted
