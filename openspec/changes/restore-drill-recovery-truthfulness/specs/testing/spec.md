## ADDED Requirements

### Requirement: Restore-Drill PostgreSQL Integration Evidence
The isolated restore-drill executor command path SHALL have integration coverage
against a real PostgreSQL testcontainer using real PostgreSQL client tooling.
The evidence must exercise `pg_dump`, gzip, `createdb`, `psql`, and `dropdb`
against the container; a mocked subprocess-only test is insufficient proof of
recovery. It SHALL also prove that the dashboard-style shared credential remains
`NOCREATEDB` while the distinct executor credential is delivered only through
its private file-backed boundary.

ID: REQ-testing-031
Source: Non-Negotiable Rule 4; craft-and-care/testing-and-verification.md § New Feature; system-overview-page REQ-system-overview-page-006
Scope: v1-mandatory

#### Scenario: Real dump restores and leaves no scratch database
- **WHEN** the integration test seeds a PostgreSQL testcontainer, creates a
  compressed plain-SQL dump with real client tooling, and runs the
  restore-drill command path
- **THEN** the drill reports a passing result after restoring and verifying
  non-system data from that dump
- **AND** the test confirms the named scratch database is absent after the
  command completes
- **AND** the test does not print dump contents, connection passwords, or a
  credential-bearing connection string

#### Scenario: Shared NOCREATEDB credential has a stable classified failure
- **WHEN** the integration test runs the command path using a dashboard-style
  shared login role with `NOCREATEDB` against the PostgreSQL testcontainer
- **THEN** the drill reports `result="fail"`, `failure_stage="create"`, and
  `failure_code="createdb_permission_denied"`
- **AND** the test confirms no scratch database remains after the failed attempt

#### Scenario: Executor credential is private and succeeds through its boundary
- **WHEN** the integration/configuration tests run the command path using the
  distinct executor login and render the deployment services
- **THEN** the executor can complete the bounded scratch lifecycle without
  giving dashboard-api the same credential
- **AND** rendered dashboard, butler, and connector services do not receive the
  executor secret or a `CREATEDB`-capable shared credential

#### Scenario: Cleanup failure remains observable as a failed result
- **WHEN** the integration test induces a scratch cleanup failure in a controlled
  disposable environment
- **THEN** the drill reports a non-passing result with the cleanup stage/code
- **AND** it never records a pass solely because restore verification succeeded

#### Scenario: Integration prerequisites are explicit
- **WHEN** Docker, the PostgreSQL testcontainer, or a required PostgreSQL client
  binary is intentionally unavailable in the test environment
- **THEN** the integration test is skipped with an explicit prerequisite message
- **AND** it does not silently replace the real command path with a mock or a
  weaker unit assertion

### Source References

- Non-Negotiable Rule 4 (`about/heart-and-soul/vision.md`): deterministic
  infrastructure must be testable, debuggable, and predictable.
- `about/craft-and-care/testing-and-verification.md` § New Feature: verify the
  feature at the layer where its promised behavior is defined.
- `openspec/specs/testing/spec.md` § PostgreSQL Testcontainer Infrastructure:
  integration tests use disposable PostgreSQL containers for real DB evidence.
