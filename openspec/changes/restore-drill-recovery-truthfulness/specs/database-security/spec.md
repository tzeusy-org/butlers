## ADDED Requirements

### Requirement: Restore-Drill Isolated Executor Boundary
The restore drill SHALL run only in a dedicated deterministic executor using a
distinct purpose-bound `LOGIN CREATEDB` role. Privileged bootstrap SHALL
provision that role with `NOINHERIT`, `NOSUPERUSER`, `NOCREATEROLE`,
`NOREPLICATION`, no membership in a butler, connector, or migration role, and
only the narrow database interface required to read restore-drill due state and
write its result/provenance. Its password is a Tier-0 file-backed deployment
secret mounted only into the executor. Every `butler_*_rw` role,
`connector_writer`, the migration/connecting role, and dashboard-api's shared
`POSTGRES_USER` SHALL remain `NOCREATEDB`; no dashboard/API response,
`DATABASE_URL`, shared environment anchor, or normal runtime credential surface
shall expose the executor credential.

ID: REQ-database-security-006
Source: RFC 0006 § Database Connection Scoping; heart-and-soul/security.md § Schema Isolation
Scope: v1-mandatory

#### Scenario: Bootstrap provisions only the isolated executor role
- **WHEN** the managed one-shot bootstrap runs with the separately supplied
  executor secret
- **THEN** it creates or repairs the distinct executor login with the required
  `CREATEDB` attribute and narrow restore-drill interface
- **AND** every `butler_*_rw` role, `connector_writer`, migration/connecting
  role, and dashboard shared user remains `NOCREATEDB`
- **AND** the executor secret is neither written to git-tracked configuration
  nor supplied to any normal runtime process

#### Scenario: Dashboard and runtime roles cannot create a scratch database
- **WHEN** dashboard-api resolves its ordinary `db_params_from_env()` credential,
  or a connection has assumed any `butler_*_rw` role or `connector_writer`
- **THEN** an attempt to create a database is denied by PostgreSQL
- **AND** the denial does not cause the runtime connection to shed its `SET ROLE`
  restriction or acquire a broader role

#### Scenario: Executor credential is isolated at the process boundary
- **WHEN** the deployment renders dashboard-api, a butler, a connector, and the
  restore-drill executor
- **THEN** only the executor receives the file-backed credential and its
  purpose-specific endpoint configuration
- **AND** the executor does not inherit shared `POSTGRES_*`/`DATABASE_URL`
  configuration and has no general live-schema grants
- **AND** it joins only the `db` network, mounts backup artifacts read-only, and
  has no listener, Docker socket, `backend`, `frontend`, or `egress` access

#### Scenario: Privilege repair requires the managed bootstrap path
- **WHEN** an operator encounters a restore-drill `CREATEDB` prerequisite failure
- **THEN** the documented recovery path directs the operator to the managed
  bootstrap process
- **AND** it does not direct the operator to run ad hoc `ALTER ROLE`, pre-create
  a scratch database, or expose a superuser connection through the application

### Source References

- Non-Negotiable Rule 1 (`about/heart-and-soul/vision.md`): owner-controlled
  infrastructure remains recoverable without widening runtime privileges.
- Non-Negotiable Rule 3 (`about/heart-and-soul/vision.md`): database boundaries
  are structural, not a convenience bypass.
- RFC 0006 § Database Connection Scoping: runtime `SET ROLE` enforces the
  schema-scoped least-privilege model.
- RFC 0008 § Container Environment Isolation and Invariants: a purpose-bound
  secret and db-only service remain explicit deployment boundaries.
