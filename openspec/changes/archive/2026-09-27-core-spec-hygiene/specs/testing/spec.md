## REMOVED Requirements

### Requirement: Adapter Integration Test Infrastructure
**Reason**: Duplicated the adapter harness contract that adapter-integration-testing "Shared Test Harness" owns.

**Migration**: See adapter-integration-testing "Shared Test Harness".

## MODIFIED Requirements

### Requirement: E2E Staging Harness Architecture
The E2E harness SHALL boot a complete disposable butler ecosystem for every test session: real ButlerDaemon processes, real PostgreSQL databases, real Alembic migrations, and real LLM calls via Haiku.

#### Scenario: Ecosystem bootstrap
- **WHEN** the E2E test session starts
- **THEN** the `butler_ecosystem` session-scoped fixture auto-discovers butlers from `roster/`, provisions one schema per butler in the shared `butlers` database, runs core and module Alembic migrations, boots each `ButlerDaemon`, starts FastMCP SSE servers on configured ports, and registers all butlers in the switchboard's `butler_registry`

#### Scenario: Butler auto-discovery
- **WHEN** a new butler is added to `roster/` with a valid `butler.toml`
- **THEN** it is automatically included in the E2E harness with zero test code changes
- **AND** it immediately participates in smoke tests (port liveness, core tables, module status)

#### Scenario: Disposable environment
- **WHEN** a test session completes (or crashes)
- **THEN** all databases are destroyed along with the testcontainer
- **AND** no state persists between runs and no test assumes state from a previous test

#### Scenario: Cost-bounded LLM usage
- **WHEN** E2E tests make LLM calls
- **THEN** they use Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) for cost efficiency
- **AND** a `cost_tracker` fixture accumulates actual token counts across all calls and prints a summary at session end
- **AND** the full suite should cost approximately $0.05 to $0.20 per run

#### Scenario: E2E infrastructure container image
- **WHEN** the E2E harness starts its PostgreSQL testcontainer
- **THEN** it uses `pgvector/pgvector:pg17` (matching production `docker-compose.yml`) for migration and extension parity
