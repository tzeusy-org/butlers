## MODIFIED Requirements

### Requirement: E2E Assertion Strategy
LLM behavior is non-deterministic. The E2E harness SHALL separate infrastructure assertions (exact) from LLM-dependent assertions (loose). Goal-ledger records replace routing-log entries as the structural record of how an ingested message was handled.

Source: RFC 0038 D1 and D17
Scope: v1-mandatory

#### Scenario: Structural assertions are exact
- **WHEN** validating infrastructure behavior
- **THEN** assertions are deterministic and exact: table existence checks, port liveness, goal and invocation row existence in the goal ledger, admission records, module status values

#### Scenario: Content assertions are loose
- **WHEN** validating LLM-dependent outcomes
- **THEN** assertions use set membership (the expected domain appears among the goal's broker-forwarded calls), case-insensitive containment (`ILIKE '%weight%'`), numeric range checks, and existence checks (at least one matching row)
- **AND** assertions never match on exact text output from the LLM

### Requirement: E2E Declarative Scenario Framework
Simple input-output test cases SHALL be defined as `Scenario` dataclass instances in `tests/e2e/scenarios.py`. A parametrized test runner SHALL auto-generate one pytest test case per scenario. A scenario's expected domain names the domain whose capabilities the admitted goal is expected to call, not a classification target.

Source: RFC 0038 D1 and D5
Scope: v1-mandatory

#### Scenario: Scenario dataclass
- **WHEN** a new scenario is defined
- **THEN** it specifies: `id` (unique identifier), `description` (human-readable), `envelope` (ingest.v1 payload dict built via the `email_envelope()` / `telegram_envelope()` factory functions), `expected_routing` (the domain whose capability the admitted goal is expected to call, or None when several domains are expected), `expected_tool_calls` (subset-matched tool names), `db_assertions` (list of DbAssertion), `tags` (list, for pytest `-k` filtering), and `timeout_seconds` (default 60)

#### Scenario: DbAssertion dataclass
- **WHEN** database side effects are specified
- **THEN** each `DbAssertion` carries four fields: `butler` (str — whose DB to query), `query` (str — raw SQL to execute), `expected` (int|dict|list[dict]|None — see below), and `description` (str — human-readable label for test output)
- **AND** the `expected` field controls validation behavior:
  - **int**: the query must return a single row with a `count` column equal to this value (COUNT queries)
  - **dict**: the query must return a single row whose columns match all key/value pairs in the dict
  - **list[dict]**: the query must return multiple rows whose full list of dicts matches exactly
  - **None**: the query must return no rows (assert absence)

#### Scenario: Automatic test generation
- **WHEN** a new `Scenario` is added to `scenarios.py`
- **THEN** the parametrized runner in `test_scenario_runner.py` automatically generates a pytest test case for it with no new test functions required

### Requirement: E2E Complex Flow Tests
Multi-step scenarios that go beyond the declarative pattern SHALL be implemented as dedicated test modules. Flow tests SHALL exercise ingress, goal admission, goal execution and brokered capability calls; message classification and route dispatch no longer exist.

Source: RFC 0038 D1, D2, D3, D5 and D17
Scope: v1-mandatory

#### Scenario: Flow test module naming
- **WHEN** a complex flow test is needed
- **THEN** it is created as `tests/e2e/test_{butler}_flow.py` or `tests/e2e/test_{concern}_flow.py`
- **AND** it uses the `butler_ecosystem` fixture to access the goal runner, DB pools, and MCP tools

#### Scenario: Smoke tests (test_ecosystem_health.py)
- **WHEN** the E2E session starts
- **THEN** smoke tests run first with zero LLM calls, validating: every butler's SSE endpoint responds to HTTP, core tables (`state`, `scheduled_tasks`, `sessions`) exist in every database, butler-specific domain tables exist, `butler_registry` in switchboard DB has all butlers, and expected modules report correct status

#### Scenario: Switchboard flow tests (test_switchboard_flow.py)
- **WHEN** switchboard ingress and goal admission are tested end-to-end
- **THEN** the test module validates: an ingested envelope admits exactly one goal whose origin and exposure come from server-held context, a second ingest of the same envelope returns `duplicate=True` with the same `request_id` and the existing goal, and the goal reaches a final state recorded in the goal ledger

#### Scenario: Cross-butler flow tests (test_cross_butler.py)
- **WHEN** the full message pipeline is tested end-to-end
- **THEN** the test validates: mock Telegram `IngestEnvelopeV1` is ingested, admission creates a goal, the goal runner launches the lead on the session host, the lead or a delegated child calls domain capabilities through the broker, domain tools write to database, and assertions span both switchboard DB (goal, invocation and effect rows in the goal ledger) and target butler DB (domain table)

### Requirement: E2E Security Domain
E2E security tests SHALL validate credential isolation, MCP config lockdown, database isolation, secret detection, and inter-butler communication boundaries. MCP lockdown means an invocation reaches only the broker and only its grant's capabilities.

Source: RFC 0038 D3, D7 and D17
Scope: v1-mandatory

ID: REQ-testing-062

#### Scenario: Credential sandbox testing
- **WHEN** testing environment variable isolation
- **THEN** a canary env var (`TEST_SECRET_CANARY`) is set, a goal is admitted, and the test asserts its invocations cannot access the undeclared variable
- **AND** each invocation's environment holds only `PATH`, its staged model credential, its MCP config and adapter-owned non-secret pointers to files staged inside its sandbox, and the session host holds no domain, database or connector credential
- **AND** one invocation cannot read another invocation's staged credential or files
- **AND** runtime authentication uses CLI-level OAuth tokens, not API keys injected via env vars

#### Scenario: MCP config lockdown testing
- **WHEN** testing MCP tool scope
- **THEN** an invocation's MCP config names only the broker, and `tools/list` returns only its grant's capabilities under namespaced names plus the goal tools
- **AND** calling a capability outside the grant is refused by the broker, and a domain refuses a model-reachable call without a valid broker assertion
- **AND** the deleted `classify_message`, `route`, `trigger` and `delegate_*` tools are listed to no invocation

#### Scenario: Database isolation testing
- **WHEN** testing cross-database boundaries
- **THEN** health butler tools do not produce rows in the relationship database
- **AND** each butler's database has its own domain tables (health has `measurements`, relationship does not)
- **AND** connection pools are scoped per-butler database

#### Scenario: Log redaction testing
- **WHEN** testing secret leakage prevention
- **THEN** `ANTHROPIC_API_KEY` value does not appear in any captured log messages after a full pipeline run
- **AND** neither the goal ledger nor any session record contains credential values
- **AND** tool span attributes contain identifiers and digests only, never argument or result values

### Requirement: E2E Data Contract Validation Domain
E2E contract tests SHALL validate the typed data contracts between pipeline stages: IngestEnvelopeV1 and SpawnerResult. The classification response, FanoutPlan and route contract no longer exist.

Source: RFC 0038 D1, D5 and D7
Scope: v1-mandatory

#### Scenario: IngestEnvelopeV1 contract
- **WHEN** a well-formed envelope is submitted
- **THEN** it is accepted with `status="accepted"`
- **AND** wrong schema version, invalid channel/provider pair, naive datetime, and extra fields are rejected with Pydantic validation errors
- **AND** duplicate idempotency keys return `duplicate=True` with the same `request_id`

#### Scenario: Classification response contract
- **WHEN** an ingested envelope is admitted
- **THEN** no classification response is produced or parsed: admission reads origin and cause from server-held context, never from message text or model output
- **AND** a duplicate submission of the same envelope returns the existing goal rather than admitting a second one

#### Scenario: SpawnerResult contract
- **WHEN** a spawner invocation completes
- **THEN** `session_id` is always set, `duration_ms` is always non-negative, `success` is true iff output is non-empty without exception, `tool_calls` is a list of `{name, arguments, result}` dicts, and token counts are set when the adapter reports usage

#### Scenario: Session persistence contract
- **WHEN** a spawner invocation occurs
- **THEN** exactly two database writes happen: `session_create()` before invocation (status="running") and `session_complete()` after with final status, duration, tokens, tool calls, and output

### Requirement: E2E Observability Domain
E2E observability tests SHALL validate distributed tracing, tool span instrumentation, goal lineage on spans, session log completeness, and cost tracking.

Source: RFC 0038 D7 and D17
Scope: v1-mandatory

#### Scenario: Trace context propagation
- **WHEN** a goal's capability call traverses the broker to the owning domain
- **THEN** the same `trace_id` appears in spans from both the switchboard and the owning domain
- **AND** the domain's tool span has the broker's forwarding span as its parent (`parent_span_id`)
- **AND** both spans carry the call's `goal_id`, `invocation_id`, `depth` and `epoch`

#### Scenario: In-memory trace capture
- **WHEN** E2E tests run without a Grafana/Tempo endpoint
- **THEN** traces are captured in-process using an `InMemorySpanExporter` for assertion without external infrastructure

#### Scenario: Tool span instrumentation
- **WHEN** an MCP tool is invoked
- **THEN** a span is emitted with attributes: `tool.name`, `tool.butler`, `tool.module`, `tool.result.status`, and `tool.duration_ms`
- **AND** the span carries identifiers and digests only, never argument or result values

#### Scenario: Session log completeness
- **WHEN** a spawner invocation completes
- **THEN** the `sessions` table row contains all required fields: `session_id` (UUID), `butler_name`, `trigger_source`, `prompt`, `model`, `status`, `created_at`, `completed_at` (for completed/error), `duration_ms`, `tool_calls` (JSONB), `input_tokens`, `output_tokens`, `trace_id`, and `error` (for error status)

#### Scenario: Cost tracking
- **WHEN** the E2E session completes
- **THEN** the `cost_tracker` fixture reports total LLM calls, input tokens, output tokens, and estimated cost
- **AND** total session cost must be under $0.20 (conservative ceiling)
- **AND** no single scenario exceeds 10,000 input tokens

### Requirement: E2E Resilience Domain
E2E resilience tests SHALL validate graceful degradation under failure at every layer: infrastructure, daemon, MCP transport, goal runner, and cross-butler.

Source: RFC 0038 D1, D10, D11 and D12
Scope: v1-mandatory

#### Scenario: Butler kill and recovery
- **WHEN** a butler daemon is killed mid-operation
- **THEN** a broker-forwarded call to the killed butler fails without executing, and an effect dispatched without a settled receipt is recorded `unknown`
- **AND** after the butler is restarted, brokered calls to it succeed again
- **AND** other butlers are unaffected during the outage

#### Scenario: Serial dispatch lock contention
- **WHEN** two goals that call the same domain are admitted concurrently
- **THEN** both reach a final state
- **AND** no domain spawner lock serializes them, because domain daemons run no reasoning sessions

#### Scenario: Classification failure fallback
- **WHEN** a message is ingested while the model provider is unavailable
- **THEN** admission still records the goal, because admission makes no model call and never falls back to routing to `general`
- **AND** the lead invocation's failure is recorded in the goal ledger

#### Scenario: Partial dispatch failure
- **WHEN** a goal has delegated several children and one child's domain is unavailable
- **THEN** the other children continue normally
- **AND** the failed child's outcome is returned to the lead and recorded in the goal ledger

#### Scenario: Module startup failure isolation
- **WHEN** a module fails during startup (e.g., invalid credentials)
- **THEN** the butler starts successfully with remaining modules
- **AND** failed module's tools are not registered
- **AND** modules that depend on the failed module are marked `cascade_failed`

#### Scenario: Timeout cascade behavior
- **WHEN** a timeout fires at one layer
- **THEN** an invocation that exceeds its limits is terminated with its whole process domain and recorded `timed_out` in the goal ledger
- **AND** a goal that exhausts its budget or deadline refuses new work and ends `budget_exhausted` with whatever results and effects exist

#### Scenario: Connection pool exhaustion
- **WHEN** the database connection pool is exhausted
- **THEN** tool calls queue on the pool rather than crashing
- **AND** after connections are returned, subsequent calls succeed

### Requirement: E2E Message Flow Domain
E2E flow tests SHALL validate the complete message pipeline from ingestion through goal admission, goal execution, brokered tool execution, and database persistence.

Source: RFC 0038 D1, D3, D5, D7 and D9
Scope: v1-mandatory

#### Scenario: Canonical message flow
- **WHEN** a test exercises the full pipeline
- **THEN** the flow is: (1) build `IngestEnvelopeV1`, (2) `ingest_v1()` validates and persists to `message_inbox`, (3) admission creates a goal with origin, exposure, ceiling and budget from server-held context, (4) the goal runner launches the lead on the session host with an MCP config naming only the broker, (5) the lead or a delegated child calls domain capabilities through the broker, which forwards each call with a signed assertion, (6) domain tools write to their database, (7) test validates goal-ledger rows in the switchboard database and domain rows in the target butler database

#### Scenario: Declarative scenario validation
- **WHEN** a scenario specifies `expected_routing` and `db_assertions`
- **THEN** the runner ingests its envelope, waits for the admitted goal to reach a final state, checks that the goal called a capability of the expected domain, and executes each `DbAssertion` by running its raw SQL `query` against the named butler's DB pool and comparing the result to `expected`

#### Scenario: Health butler flow
- **WHEN** "Log my weight: 80kg" is sent through the pipeline
- **THEN** the admitted goal calls the health domain's `measurement_log` capability through the broker
- **AND** the `measurements` table contains a row with type matching "weight" (case-insensitive)

#### Scenario: Relationship butler flow
- **WHEN** "Add Sarah Johnson as a new contact" is sent through the pipeline
- **THEN** the admitted goal calls the relationship domain's `contact_add` or `contact_create` capability through the broker
- **AND** the `contacts` table contains a row with name matching "Sarah" (case-insensitive)

#### Scenario: Multi-domain decomposition flow
- **WHEN** a compound message like "I saw Dr. Smith and need to send her a thank-you card" is sent
- **THEN** one goal is admitted and reaches capabilities owned by both `health` and `relationship`
- **AND** any child it delegates receives a self-contained brief

### Requirement: E2E Scheduling Domain
E2E scheduling tests SHALL validate the TOML schedule sync, tick dispatch, cron rearm, interleaving of scheduled and owner admissions, schedule CRUD through the control plane, and tick idempotency. A scheduled prompt SHALL become an obligation admission; the daemon SHALL NOT spawn a reasoning session for it.

Source: RFC 0038 D2, D5, D7 and D18
Scope: v1-mandatory

#### Scenario: TOML schedule sync
- **WHEN** a butler daemon starts
- **THEN** the `scheduled_tasks` table contains rows matching every `[[butler.schedule]]` entry in `butler.toml`
- **AND** syncing is idempotent (restarting does not duplicate rows)

#### Scenario: Due task triggers
- **WHEN** `_tick()` finds a task with `due_at` in the past
- **THEN** the task status transitions from `pending` to `running` to `completed` (or `error`)
- **AND** `due_at` advances to the next cron cycle after completion

#### Scenario: Dual-mode dispatch
- **WHEN** a scheduled task fires
- **THEN** native-mode tasks (with `dispatch_mode = "job"` and `job_name`) execute deterministic Python job handlers directly without the scheduler automatically spawning a runtime instance
- **AND** a native handler MAY explicitly invoke the daemon Spawner only to launch an engineering executor, while preserving the Spawner's model-catalog and timeout contracts
- **AND** prompt-carrying tasks submit an obligation admission to the control plane with the capability set declared in git, and the daemon spawns no runtime instance

#### Scenario: Timer and external trigger interleaving
- **WHEN** an owner message and an obligation occurrence for the same domain are admitted concurrently
- **THEN** both goals reach a final state
- **AND** neither admission source is starved under repeated alternating admissions

#### Scenario: Schedule CRUD via MCP tools
- **WHEN** `schedule_create`, `schedule_list`, `schedule_update`, and `schedule_delete` are called through the broker within a goal's grant, or through the control plane's service listener for the dashboard
- **THEN** schedules are created in the `scheduled_tasks` table, listed, updated (cron and enabled fields), and deleted
- **AND** creating a same-named task twice results in an error or upsert, not a duplicate
- **AND** a prompt-carrying `schedule_create` is obligation creation, an `administer` capability whose capability set is at most its creator's grant

#### Scenario: Tick idempotency
- **WHEN** `_tick()` runs twice in quick succession
- **THEN** only one job run or obligation admission occurs for a given task occurrence (because `due_at` is advanced after the first tick, and a duplicate submission of the same obligation occurrence returns the existing goal)

#### Scenario: Cross-butler cron staggering
- **WHEN** multiple butlers have the same cron expression
- **THEN** their `next_due_at` timestamps are deterministically staggered per butler name to reduce synchronized LLM bursts
- **AND** the stagger offset is bounded to at most 15 minutes and always less than the cron interval

### Requirement: E2E Performance Domain
E2E performance tests SHALL validate concurrent goal execution under load, connection pool saturation, MCP transport overhead, pipeline latency budgets, and cost scaling.

Source: RFC 0038 D1, D7 and D10
Scope: v1-mandatory

#### Scenario: Serial dispatch under load
- **WHEN** 5 goals are admitted concurrently
- **THEN** all reach a final state
- **AND** no deadlock occurs under 10 concurrent goals
- **AND** no goal runs more concurrent children than its budget allows

#### Scenario: Lock released on error
- **WHEN** an invocation errors out or times out
- **THEN** its invocation token is revoked and its process domain is terminated
- **AND** subsequently admitted goals still launch

#### Scenario: Connection pool saturation
- **WHEN** many concurrent MCP tool calls exhaust the asyncpg pool
- **THEN** calls queue on the pool gracefully without crashing
- **AND** after connections are returned, subsequent calls succeed

#### Scenario: MCP client caching
- **WHEN** the broker forwards two calls to the same butler
- **THEN** the second call reuses the cached `MCPClient` (faster than creating a new one)
- **AND** if the cached client is stale (butler restarted), a new client is created automatically

#### Scenario: Pipeline latency budget
- **WHEN** the full pipeline (ingest, admission, lead launch, brokered tool execution) runs
- **THEN** it completes within 120 seconds
- **AND** a direct tool call completes within 1 second

#### Scenario: Cost scales linearly
- **WHEN** N messages are processed through the full pipeline
- **THEN** cost per message remains roughly constant (no prompt bloat)
- **AND** cost per message is under $0.02

### Requirement: E2E Infrastructure Domain
E2E infrastructure tests SHALL validate the staging environment: PostgreSQL testcontainer provisioning, database isolation, port allocation, Docker requirements, module degradation, and CI/CD exclusion.

ID: REQ-testing-061
Source: RFC 0038 D2
Scope: v1-mandatory

#### Scenario: Per-butler schema provisioning
- **WHEN** the E2E ecosystem bootstraps
- **THEN** all butlers share a single `butlers` database within the testcontainer, and each butler gets its own PostgreSQL schema (e.g., `switchboard`, `health`, `relationship`) plus the shared `public` schema
- **AND** each schema has core tables (`state`, `scheduled_tasks`, `sessions`) plus butler-specific domain tables

#### Scenario: Offset port allocation
- **WHEN** E2E butlers start
- **THEN** each butler's production base port is shifted by `E2E_PORT_OFFSET` (11000), so the E2E stack can run alongside a live production stack without port conflicts
- **AND** the `switchboard_url` of non-switchboard butlers is patched to the switchboard's offset port

#### Scenario: Docker requirements check
- **WHEN** the E2E session starts
- **THEN** it validates: Docker daemon is running, `ANTHROPIC_API_KEY` is set, `claude` CLI is on PATH, and Python 3.12+ is available
- **AND** missing prerequisites result in `pytest.skip()` with a clear message, not a confusing traceback

#### Scenario: Missing claude binary skips the whole E2E session
- **WHEN** the `claude` binary is not found on `PATH` (`shutil.which("claude")` is empty)
- **THEN** the session-scoped autouse fixture `require_claude_binary` in `tests/e2e/conftest.py` skips every E2E test by raising `pytest.skip(..., allow_module_level=True)` from the fixture
- **AND** the session is reported as skipped, not failed

#### Scenario: A new roster butler boots with no test edits
- **WHEN** a directory containing a `butler.toml` is added under `roster/`
- **THEN** the E2E ecosystem bootstrap discovers it through `list_butlers()` and provisions its schema, runs its migrations, assigns its offset port, starts it and tears it down with the other butlers
- **AND** no change to the bootstrap code or to existing E2E tests is required to boot it
- **AND** this covers only the bootstrap: declarative scenarios in `tests/e2e/scenarios.py` and the per-butler pool convenience fixtures are written by hand and are not generated from the roster

#### Scenario: LLM output is asserted loosely and routing exactly
- **WHEN** a declarative E2E scenario is verified
- **THEN** assertions on LLM-driven behavior are loose: expected tool calls are checked as a subset of the tool names actually called (set containment, not exact match), and a scenario that exceeds its timeout is recorded as a timeout without failing the whole run
- **AND** the routing target must equal the expected butler exactly
- **AND** database assertions are evaluated and logged, not enforced: a failed assertion is logged as a warning and does not fail the scenario, and dict rows are compared on the expected key subset only

#### Scenario: Module degradation during E2E
- **WHEN** modules lack external service credentials (Telegram, Email, Calendar, Memory)
- **THEN** they fail gracefully during daemon startup
- **AND** each butler retains full functionality for core MCP tools and roster-defined domain tools, and goals can still reach those capabilities through the broker
- **AND** smoke tests verify that failed modules report the correct status and failure phase (e.g., `telegram.status = "failed"`, `telegram.phase = "credentials"`)

#### Scenario: E2E CI/CD exclusion
- **WHEN** CI/CD runs the test suite
- **THEN** E2E tests are excluded via three independent mechanisms: pytest marker (`@pytest.mark.e2e`), environment guard (session-scoped autouse fixture skips when `ANTHROPIC_API_KEY` is not set), and explicit `--ignore=tests/e2e` in CI workflow

## REMOVED Requirements

### Requirement: Route-Inbox Recovery Smoke Test
**Reason**: `route_inbox` is the domain-side durable inbox of `route.execute` session dispatch, which is deleted at cutover; domain daemons no longer spawn reasoning sessions (RFC 0038 D2, D7).
**Migration**: Accepted ingest stays durable in Switchboard's buffer and feeds goal admission (`REQ-goal-orchestration-008`); a goal whose lead lease lapses is recovered by lead replacement from the goal ledger (RFC 0038 D14), and that recovery is the successor to prove.
