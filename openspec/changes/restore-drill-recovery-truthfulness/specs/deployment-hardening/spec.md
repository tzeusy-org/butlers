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
- **THEN** it joins only a dedicated restore-drill bridge whose outbound policy
  default-denies every destination except the configured PostgreSQL endpoint
  and port, has a read-only backup mount and no listener, Docker socket,
  `backend`, `frontend`, or `egress` access
- **AND** both supported launchers, `scripts/compose.sh` and `butlers deploy`,
  stop/create the executor, install that default-deny policy, and only then
  start it; failure to install the policy prevents the executor from starting
- **AND** when the database is configured by DNS name, that name remains the
  executor's TLS identity for `sslmode=verify-full` while a separately resolved
  IPv4 address is used only by the firewall and local container host mapping,
  so the isolated bridge has no DNS egress
- **AND** it receives its credential only through the private file-secret mount,
  not the shared `POSTGRES_*`/`DATABASE_URL` environment used by dashboard-api
- **AND** dashboard-api reads durable results but does not schedule or execute
  the scratch lifecycle

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
  artifacts, or manually erase the audit result that recorded a failure
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
