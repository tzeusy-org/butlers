## MODIFIED Requirements

### Requirement: Conftest Fixture Hierarchy
Shared fixture definitions SHALL have one canonical module and one project-wide pytest registration layer. Nested and test-tree conftest files SHALL NOT re-register those project-wide shared fixtures; they MAY own fixtures, hooks, and helpers scoped to their tree. Root conftest SHALL retain its in-tree package-source guard before the first Butlers import and every canonical fixture, fake-engine/cache lifetime, marker, database and finalizer contract. It SHALL NOT eagerly invoke full module discovery, preload roster job sets or discover every roster router just to enable imports. Owning package import resolution SHALL make the existing supported roster namespaces available on demand without needing conftest execution.

ID: REQ-testing-039
Source: bu-ly3lv5.4 released by bu-7lh5ew; performance-discipline; actual owning source and existing contract
Scope: v1-mandatory

#### Scenario: Root conftest (conftest.py)
- **WHEN** any test in the project runs
- **THEN** `SpawnerResult`, `MockSpawner`, and `mock_spawner` are defined canonically in `src/butlers/testing/shared_fixtures.py` and imported and exported exactly once by the root `conftest.py`
- **AND** pytest makes `mock_spawner` available to both configured test trees, `tests/` and `roster/`, through that root registration
- **AND** root `conftest.py` provides the `docker_available` flag (checks `shutil.which("docker")`), `postgres_container` session-scoped fixture (`pgvector/pgvector:pg17` testcontainer), and `provisioned_postgres_pool` fixture (creates a fresh database with unique name per test invocation)

#### Scenario: Roster conftest (roster/conftest.py)
- **WHEN** tests under `roster/` run
- **THEN** `roster/conftest.py` auto-applies the `integration` marker and Docker-skip behavior to tests in that directory tree via `pytest_collection_modifyitems`, unless the test already declares an explicit `@pytest.mark.unit` (via `item.get_closest_marker("unit")`), in which case the auto-mark is skipped and the test keeps its declared `unit` taxonomy
- **AND** a meta-test (`roster/test_conftest_marker_taxonomy.py`) pins this exception so it cannot silently regress

#### Scenario: Root setup remains lightweight and complete
- **WHEN** root conftest is imported in a fresh process without a requested roster body
- **THEN** full registry, job and router discovery has not executed
- **AND** its source guard and canonical shared-fixture registration remain active

#### Scenario: A real roster request still resolves
- **WHEN** a supported roster module, job, router or models import is requested without prior root-conftest preload
- **THEN** it resolves from the actual checkout and preserves the owning discovery and registration behavior
- **AND** an unrelated roster body is not eagerly executed

## ADDED Requirements

### Requirement: Fresh-Process Import Boundary and Calibrated Budget
Import-boundary contracts SHALL run in a fresh own-source interpreter and assert the actual forbidden module roots absent before demand. The root-conftest import budget SHALL use actual elapsed-time samples and a recorded numeric cap, with an independent child timeout. Original eager-source restoration and a positioned slow-import control SHALL prove the semantic and timed assertions can fail. Measurements SHALL identify exact source, interpreter, lock, command, corpus and cache context; historical/estimated gains SHALL NOT be presented as current measured improvements.

ID: REQ-testing-040
Source: bu-ly3lv5.4 released by bu-7lh5ew; performance-discipline; testing-and-verification; original no-wall-clock-gain acceptance route
Scope: v1-mandatory

#### Scenario: Parent fake cannot conceal an eager child import
- **WHEN** a fresh subprocess imports the named unused memory, approvals, daemon/API or conftest boundary
- **THEN** absence is measured from that child's sys.modules and own-source identity
- **AND** restoring the actual eager embedding import makes the semantic contract fail

#### Scenario: Numeric budget is causally enforced
- **WHEN** a measured root-conftest import exceeds its recorded calibrated cap
- **THEN** the timed contract fails independently of the absent-stack assertion
- **AND** a positioned slow-import control and restored positive establish reachability

#### Scenario: Gain claim follows its selected evidence route
- **WHEN** completion reports a quantified affected-job wall-clock gain
- **THEN** at least five named comparable before and five named after CI runs supply the p50 comparison
- **AND** otherwise the report explicitly states no wall-clock gain is claimed, preserving the original allowed route
