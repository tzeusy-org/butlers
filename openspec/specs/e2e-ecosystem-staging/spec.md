# E2E Ecosystem Staging

## Purpose
Defines the phased bootstrap, provisioning, port isolation, and teardown contract for the disposable butler ecosystem used by end-to-end tests.

## Requirements

### Requirement: Phased ecosystem bootstrap
The ecosystem fixture SHALL execute in five sequential phases: Provision, Configure, Authenticate, Boot, Validate. Each phase MUST complete successfully before the next begins. If any phase fails, the fixture SHALL report which phase failed and tear down any resources from completed phases.

#### Scenario: Full bootstrap succeeds
- **WHEN** all roster butlers have valid configurations and OAuth credentials are cached
- **THEN** the fixture provisions PostgreSQL, configures all butlers with E2E port offsets, skips OAuth prompts (cached), boots all daemons, and smoke-tests each `/sse` endpoint

#### Scenario: OAuth credentials missing
- **WHEN** a butler with OAuth-dependent modules (calendar, email) has no cached credentials
- **THEN** the fixture pauses at the Authenticate phase, prints instructions for the specific provider, and blocks until the user completes the OAuth flow

#### Scenario: OAuth credentials expired
- **WHEN** cached OAuth tokens exist but are expired or invalid
- **THEN** the fixture detects the invalid tokens during the Authenticate phase (not just file existence) and prompts for re-authentication

#### Scenario: Boot phase health check failure
- **WHEN** a butler daemon fails to respond on its `/sse` endpoint within the health check timeout
- **THEN** the fixture reports which butler(s) failed to start and tears down all resources

### Requirement: PostgreSQL provisioning with migrations
The Provision phase SHALL start a testcontainer PostgreSQL instance and run all Alembic migrations to create the full schema (per-butler schemas + `shared`).

#### Scenario: Fresh database provisioned
- **WHEN** the ecosystem fixture starts
- **THEN** a `pgvector/pgvector:pg17` testcontainer is started, all migrations run, and per-butler schemas plus `shared` schema are created

#### Scenario: Partitioned tables initialized
- **WHEN** migrations complete
- **THEN** the `message_inbox` table has at least the current month's partition created

### Requirement: Port isolation
All butler daemons SHALL run on isolated ports using a fixed offset from production ports to avoid conflicts.

#### Scenario: Port offset applied
- **WHEN** butlers are configured for E2E
- **THEN** each butler's port is offset by `E2E_PORT_OFFSET` (e.g., 41100 becomes 51100)

#### Scenario: Switchboard URL patching
- **WHEN** non-switchboard butlers are configured
- **THEN** their `switchboard_url` is patched to point at the switchboard's E2E port

### Requirement: Teardown and cleanup
The fixture SHALL clean up all resources on teardown, regardless of test outcome.

#### Scenario: Normal teardown
- **WHEN** the test session completes (pass or fail)
- **THEN** all butler daemons are stopped, database connections are closed, and the PostgreSQL container is removed

#### Scenario: Interrupted teardown
- **WHEN** the test session is interrupted (KeyboardInterrupt, SIGTERM)
- **THEN** teardown still executes via try/finally, stopping daemons and removing containers
