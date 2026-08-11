## MODIFIED Requirements

### Requirement: Backup And Restore Verification Path
An always-on personal-data deployment SHALL have a documented, executable
backup-and-restore path for the PostgreSQL data plane, and that path SHALL be
verifiable by a restore drill that proves a backup can be restored to a working
state without mutating the live application database. `deploy/backup/pg_dump.sh`
produces timestamped dumps, `scripts/pg_restore.sh` restores only to a named
scratch database, and `scripts/pg_verify_restore.sh` validates schema, table,
and row-count expectations; `docs/operations/backup-restore.md` SHALL document
the isolated restore-drill executor, its file-secret boundary, the scratch
lifecycle, and rollback boundaries. Restore verification protects the owner's
irreplaceable personal data against corruption or accidental loss.

ID: REQ-deployment-hardening-007
Source: Non-Negotiable Rule 1; RFC 0006 § Database Connection Scoping; RFC 0008 § Invariants
Scope: v1-mandatory

#### Scenario: Documented restore drill exists and is verifiable
- **WHEN** an operator follows the documented backup-and-restore procedure
- **THEN** a backup of the PostgreSQL data plane can be restored to a scratch
  database and verified as intact
- **AND** the procedure identifies the scratch target as distinct from the live
  application database
- **AND** the procedure includes a verification step proving the restored data
  is intact

#### Scenario: Isolated executor prerequisite is available without a live workaround
- **WHEN** the restore-drill command needs to create its scratch database
- **THEN** the dedicated executor has received its distinct `CREATEDB`
  credential through the managed bootstrap and file-secret path before the
  drill is enabled
- **AND** dashboard-api and every shared `POSTGRES_USER` consumer remain
  `NOCREATEDB` and cannot launch the privileged command path
- **AND** the operations documentation does not instruct an operator to issue an
  ad hoc `ALTER ROLE`, manually pre-create the scratch database, or mutate the
  live application database to make a drill pass

#### Scenario: Executor deployment boundary stays narrow
- **WHEN** the restore-drill executor is deployed
- **THEN** it joins only a dedicated Docker `internal` relay network, has a
  read-only backup mount and no listener, Docker socket, `backend`, `frontend`,
  or `egress` access, and can reach PostgreSQL only through an uncredentialed
  relay on that network; the prepared executor-bridge policy default-denies
  every forward or host/gateway route except TCP to that created relay peer at
  the configured port (because Docker `internal` alone limits membership but
  does not deny host/gateway traffic)
- **AND** the relay alone joins the non-internal restore-drill egress bridge,
  receives only a required resolved PostgreSQL IPv4 and port (not a recovery
  secret, shared `POSTGRES_*`, or `DATABASE_URL`), and its outbound policy
  default-denies every destination except that endpoint at both Docker's
  `DOCKER-USER`/`FORWARD` hook and the bridge-to-host `INPUT` path; the
  root-owned wrapper derives both bridge interfaces and the created relay peer
  rather than accepting those executor-topology values from a caller
- **AND** the launcher, deploy path, and executor require an untrimmed DNS
  hostname (not `localhost` or any numeric IPv4 spelling) for the executor
  connection/TLS identity, while the firewall wrapper and relay require only
  the separately supplied/resolved canonical dotted-decimal remote-unicast IPv4
  target and canonical ASCII-decimal port matching `[1-9][0-9]{0,4}` in
  `1..65535`,
  rejecting noncanonical, loopback, unspecified, link-local, multicast,
  documentation, and policy-reserved routes while allowing RFC1918,
  CGNAT/tailnet, and valid public unicast; legacy decimal, octal, hexadecimal,
  and abbreviated `inet_aton` spellings are rejected before DNS resolution.
  The pre-source endpoint-literal grammar supports simple `KEY=value` or
  `export KEY=value` with optional leading spaces/tabs; raw RHS whitespace is
  rejected before sourcing. Other Bash command forms are outside this
  pre-source endpoint-literal grammar; their resulting endpoint values are
  validated without trimming or reinterpretation
- **AND** the relay immediately closes over-cap clients before any upstream
  dial, uses a bounded listener backlog, and bounds upstream connect, idle, and
  total session lifetime with cancellation-safe cleanup; each side gets at most
  one second for graceful close before forced transport abort
- **AND** both supported launchers, `scripts/compose.sh` and `butlers deploy`,
  treat stop/down failure as terminal before create, invoke only a fixed
  root-owned firewall wrapper's versioned prepare verb, inject its
  per-created-generation nonce into the created executor, then require the
  wrapper to bind that nonce
  to the current boot, exact executor/relay containers, networks, and internal
  endpoints after installing the default-deny policy and before either service
  starts; a stale wrapper or same-boot manual down/recreate must fail before
  secret use, and neither protected service has an automatic restart policy
  that could bypass a transient fence after a Docker daemon or host restart
- **AND** a direct stop/start of the unchanged, already-fenced container
  generation is not treated as a new authorization; any down/recreate or
  topology change must repeat the canonical prepare/create/fence sequence
- **AND** the database is configured with a DNS hostname that remains the
  executor's TLS/SNI identity for `sslmode=verify-full` while it resolves only
  as the relay's internal-network alias; a separately resolved IPv4 address is
  used only by the relay and firewall, never as an executor direct route
- **AND** `sslmode=verify-ca` and `sslmode=verify-full` receive one dedicated
  noncredential CA-root file through a read-only executor-only mount; libpq and
  asyncpg use that same root, missing or invalid roots fail closed before a
  connection, and ordinary `sslmode=require` does not require that file
- **AND** it receives its credential only through the private file-secret mount,
  not the shared `POSTGRES_*`/`DATABASE_URL` environment used by dashboard-api
- **AND** dashboard-api reads durable results but does not schedule or execute
  the scratch lifecycle

#### Scenario: Direct Compose preserves the executor network boundary
- **WHEN** an operator renders the protected base-plus-restore-drill Compose
  files directly
- **THEN** the ordinary base file alone omits both protected services, their
  dedicated networks, the private secret, and the CA-root config
- **AND** the merged executor still joins only the Docker `internal` relay
  network, has no direct firewall IPv4 mapping or external bridge attachment,
  and reaches its TLS identity only as the relay alias; rendering itself is not
  a launch authorization or a substitute for the prepared executor-bridge
  default-deny policy
- **AND** only `scripts/compose.sh` and `butlers deploy` prepare both relay
  egress and executor-bridge default-deny policies before they start either
  protected service
- **AND** the documented merged-file inspection helper accepts only read-only
  `config`, `ps`, and `logs` operations and rejects lifecycle commands such as
  `up`

#### Scenario: Scratch lifecycle never targets live application data
- **WHEN** a scheduled or documented restore drill runs with an available backup
- **THEN** it performs stale-scratch cleanup, creates a fresh scratch database,
  restores, verifies, and performs post-run cleanup
- **AND** every database command targets the scratch database except the
  connection needed to create or drop that scratch database
- **AND** a failure in cleanup is recorded as a failed drill rather than a pass

#### Scenario: Fixed scratch database remains single-executor
- **WHEN** the deployment has one restore-drill executor
- **THEN** the fixed scratch database lifecycle may run as the documented
  single-executor operation

#### Scenario: Multi-executor deployment requires a guard
- **WHEN** a deployment is changed to permit more than one restore-drill executor
- **THEN** it SHALL add and verify a cross-process exclusive guard before enabling
  concurrent execution

#### Scenario: Rollback preserves recovery evidence and avoids live mutation
- **WHEN** the restore-drill implementation or its attention-source migration is
  rolled back
- **THEN** rollback does not restore a dump into the live database, delete backup
  artifacts, or manually erase the executor-owner result authority that recorded
  a failure (or its fixed public audit projection)
- **AND** a source-constraint downgrade removes only records that the older
  constraint cannot represent before narrowing the constraint
- **AND** role remediation, if needed, is performed only through the managed
  bootstrap procedure

### Source References

- Non-Negotiable Rule 1 (`about/heart-and-soul/vision.md`): protecting the
  owner-controlled data plane requires demonstrable recovery.
- `about/heart-and-soul/security.md` § Schema Isolation: the purpose-bound
  executor is an explicit privileged-runtime boundary; dashboard and normal
  runtime roles remain least-privilege.
- RFC 0006 § Database Connection Scoping: role privileges remain least-privilege
  outside the bootstrap boundary.
- RFC 0008 § Invariants: deployment operations retain explicit, bounded
  infrastructure boundaries.
