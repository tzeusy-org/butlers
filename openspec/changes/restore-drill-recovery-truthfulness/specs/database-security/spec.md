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
- **AND** it joins only a dedicated Docker `internal` relay network whose sole
  peer is an uncredentialed relay; it mounts backup artifacts read-only and has
  no listener, Docker socket, `backend`, `frontend`, or `egress` access; the
  root-owned executor-bridge policy default-denies every non-relay peer,
  including Docker gateway and host traffic, because `internal` alone does not
  supply that host boundary
- **AND** the relay alone joins the separate non-internal restore-drill egress
  bridge, whose outbound policy default-denies every destination except the
  configured PostgreSQL endpoint and port across both forwarded and
  bridge-to-host traffic; the same wrapper derives and fences the executor
  bridge so the credentialed process may reach only that created relay peer
- **AND** the executor reads its credential only after a root-owned,
  per-created-generation prepared capability binds the current boot, project,
  nonce, executor
  container generation, and relay/network topology; stale wrapper versions or
  same-boot manual down/recreate state cannot replay that capability
- **AND** direct stop/start of that unchanged, already-fenced generation is
  not a new authorization; a down/recreate or topology change must repeat the
  canonical prepare/create/fence sequence
- **AND** its connection identity is an untrimmed DNS hostname, never
  `localhost` or a numeric IPv4 spelling, and remains the executor's TLS/SNI
  identity while resolving only to the relay's internal-network alias; the
  separately resolved IPv4 is a relay/firewall route only, never an executor
  direct route
- **AND** only that separately supplied/resolved relay/firewall target accepts
  canonical dotted-decimal remote-unicast IPv4 and canonical ASCII-decimal port
  matching `[1-9][0-9]{0,4}` in `1..65535`, rejecting noncanonical, loopback,
  unspecified, link-local, multicast, documentation, and policy-reserved
  routes while allowing RFC1918, CGNAT/tailnet, and valid public unicast;
  legacy decimal, octal, hexadecimal, and abbreviated `inet_aton` spellings
  are rejected before DNS resolution. The pre-source endpoint-literal grammar
  supports simple `KEY=value` or `export KEY=value` with optional leading
  spaces/tabs; raw RHS whitespace is rejected before sourcing. Other Bash
  command forms are outside this pre-source endpoint-literal grammar; their
  resulting endpoint values are validated without trimming or reinterpretation
- **AND** the relay immediately rejects admission beyond its fixed small cap,
  keeps a bounded listener backlog, and has finite connect, idle, and total
  session deadlines with cancellation-safe cleanup; each side gets at most one
  second for graceful close before forced transport abort
- **AND** its security-definer result writer discards caller-supplied backup
  names, free-form diagnostics, and table-count compatibility values before
  durable or audit/API-visible persistence, and rejects null or non-`pass`/
  `fail` result values even for a direct executor credential call
- **AND** an executor-owner private result ledger is the sole restore-drill
  due-check and dashboard-read authority; the fixed `public.audit_log`
  projection is telemetry only, so a normal-role or administrative public audit
  spoof cannot manufacture an API pass or affect due state
- **AND** the projection is written only through a separate NOLOGIN
  `restore_drill_executor_audit_writer` security-definer with fixed
  `pg_catalog, pg_temp` search path, public-audit INSERT/sequence capability, and no
  private-ledger schema, table, function, or owner-membership privilege
- **AND** a hostile `public.audit_log` trigger therefore runs as that constrained
  audit writer and cannot write the ledger or call its reader; if it rejects the
  projection, the enclosing result transaction fails and rolls back rather than
  retaining an unprojected authoritative result
- **AND** the executor receives only the fixed due/result-writer functions,
  the shared dashboard/migration login receives only the fixed result-reader
  function, and neither has direct private-ledger table privileges

#### Scenario: Admin bootstrap provenance cannot be substituted
- **WHEN** the shared migration/dashboard role pre-creates
  `restore_drill_executor_admin`, zero-argument SECURITY DEFINER
  `install_interface()`/`finalize_interface()` no-ops, and grants itself their
  execution before the managed bootstrap
- **THEN** privileged `init-db.sql` refuses that non-superuser-owned schema
  before `CREATE OR REPLACE`, ownership transfer, or interface grant, and a
  direct no-op invocation cannot produce a trusted result authority
- **AND** `core_196` independently verifies that the admin schema and both
  exact zero-argument definer functions share a cluster-superuser bootstrap
  owner before it invokes the installer
- **AND** a clean first install, retry, and privileged rerun with that trusted
  provenance complete normally, while a shared-owned precreation remains a
  fail-closed operator-repair condition

#### Scenario: Shared authority staging cannot be blessed
- **WHEN** a shared migration/dashboard credential retains a legacy
  protected-schema `CREATE` grant and finalizer execution, then pre-creates a
  compatible `restore_drill_executor.restore_drill_results` relation, trigger,
  and every canonical interface signature before `core_196` runs
- **THEN** the bootstrap-owned fixed no-argument installer and finalizer reject
  that untrusted state atomically before any owner transfer, executor/reader
  grant, or trusted authority handoff, including during a privileged
  `init-db.sql` rerun
- **AND** the shared credential receives neither protected-schema `CREATE` nor
  finalizer execution on the managed path; the boundary does not trust object
  shape or a caller-controlled marker
- **AND** a first install or retry with every authority object absent creates
  the exact ledger and functions inside the `core_196` migration transaction
  and completes normally

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
