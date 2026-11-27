## Proposed modified requirement

### Requirement: Backup And Restore Verification Path

An always-on personal-data deployment SHALL have a documented, executable
backup-and-restore path for the PostgreSQL data plane, and that path SHALL be
verifiable (a restore drill that proves a backup can be restored to a working
state). This is shipped: `deploy/backup/pg_dump.sh` produces timestamped dumps,
`scripts/pg_restore.sh` restores to a scratch database, and
`scripts/pg_verify_restore.sh` runs the verification drill (schema, table, and
row-count checks), all documented in `docs/operations/backup-restore.md`. Restore
verification protects the owner's irreplaceable personal data against corruption
or accidental loss.

A produced backup SHALL be a stated coverage claim, not merely a zero exit
status. The backup SHALL cover the application data plane. Objects it omits
SHALL be limited to trusted-bootstrap control-plane objects that the
cluster-superuser bootstrap reconstructs and that a dump could not correctly
restore, because the restoring login is not a member of their fenced owner role.
Every omission SHALL be documented with its cost in
`docs/operations/backup-restore.md`.

The omission set SHALL be verified in both directions against a real
bootstrapped database: an object the dump identity cannot read but the backup
does not omit SHALL fail verification (it would abort the run and publish no
file), and an object the dump identity can read but the backup omits SHALL fail
verification (it would silently narrow the backup). The dump SHALL NOT be made
to succeed by widening the dump identity's privileges or by enabling row-level
security during the dump, either of which trades a loud failure for a silent
one.

A backup run that publishes no artifact SHALL say so on its error stream, and an
artifact that is undersized or does not decompress SHALL NOT be published.

The restore drill SHALL restore a single published artifact only into named
scratch inside disposable PostgreSQL in a Kubernetes Job. It SHALL mount the
backup PVC read-only, isolate scratch data for each attempt, verify required
scopes, clean up and submit bounded evidence to the protected writer. The
operations documentation SHALL describe the Job lifecycle, proof boundaries,
writer authority and rollback without any live-application restore.

ID: REQ-deployment-hardening-007
Source: Non-Negotiable Rule 1; RFC 0006 § Database Connection Scoping; RFC 0008 § Invariants
Scope: v1-mandatory

#### Scenario: Documented restore drill exists and is verifiable

- **WHEN** an operator follows the approved restore-verification procedure
- **THEN** it restores a published PostgreSQL backup to a working isolated
  scratch database and verifies the exact artifact and required scopes
- **AND** the procedure distinguishes the scratch from the live application
  cluster and records scope limits

#### Scenario: Isolated executor prerequisite is available without a live workaround

- **WHEN** a scheduled or approved manual drill needs to create scratch
- **THEN** it first establishes its Pod-local sidecar, bounded ephemeral store,
  authenticated generation and read-only artifact mount, or fails before restore
- **AND** it never widens live privileges or requires a live scratch database

#### Scenario: Ordinary dev excludes the privileged executor boundary

- **WHEN** the ordinary dev topology is deployed
- **THEN** it excludes the live restore executor and its legacy secrets
- **AND** it does not implicitly enable the Job or writer merely because an old
  secret or backup PVC exists

#### Scenario: Executor deployment boundary stays narrow

- **WHEN** the approved CronJob is rendered and admitted
- **THEN** verifier and PostgreSQL sidecar run within the restricted Pod identity,
  without host mounts, runtime sockets, host networking or privilege escalation
- **AND** the only persistent input mount is the read-only backup PVC
- **AND** the sidecar exposes only the Pod-local shared Unix socket and the
  verifier's only remote interaction is authenticated evidence submission
- **AND** generation admission, mount and route enforcement are verified before
  artifact access, including on replacement Pod creation
- **AND** runtime, storage consumption, cleanup and submission retries are bounded

#### Scenario: Direct Compose preserves the executor network boundary

- **WHEN** an operator inspects or launches Compose before the authorized cutover
  or after an authorized rollback
- **THEN** its existing protected deployment and generation fence still apply
- **WHEN** the CronJob has replaced the Compose path
- **THEN** Compose does not start the retired executor or supply its live
  credential, and the prior writer cannot race the new one

#### Scenario: Scratch lifecycle never targets live application data

- **WHEN** a scheduled or documented restore drill runs with an available backup
- **THEN** it creates, restores, verifies and drops a fresh named scratch database
  in the Pod-local sidecar
- **AND** scratch data uses per-Pod ephemeral storage and never a live route
- **AND** a failed cleanup precludes a pass and uncertain retirement prevents a
  successor generation from starting

#### Scenario: Fixed scratch database remains single-executor

- **WHEN** a catalog has a scheduled drill
- **THEN** an active or unretired attempt excludes any second scheduled, manual
  or retried attempt for that catalog
- **AND** a fresh generation cannot reuse the old scratch or scoped verdicts

#### Scenario: Multi-executor deployment requires a guard

- **WHEN** another release, manual Job or executor can request a drill
- **THEN** an independently verified cross-process admission guard prevents
  overlapping authorization for the same catalog before any restore begins

#### Scenario: Rollback preserves recovery evidence and avoids live mutation

- **WHEN** the restore-drill implementation or attention-source migration is
  rolled back
- **THEN** rollback stops admission and verifies quiescence before revoking
  the CronJob writer capability or restoring the previous execution path
- **AND** rollback does not restore a dump into the live database, delete backup
  artifacts or manually erase either protected result history
- **AND** an attention-source constraint downgrade removes only projection
  records the older constraint cannot represent before narrowing it
- **AND** any separately authorized role remediation uses managed bootstrap

#### Scenario: A fenced object is added without a backup decision
- **WHEN** a new object is fenced away from the dump identity and the backup's
  omission set is not updated
- **THEN** verification fails and names the object, rather than the nightly
  backup silently ceasing to produce a file

#### Scenario: The omission set is widened past what is fenced
- **WHEN** the omission set covers an object the dump identity can read
- **THEN** verification fails and names the object, rather than the backup
  silently ceasing to contain it

#### Scenario: A backup run produces no file
- **WHEN** a backup run fails before publishing an artifact
- **THEN** the run exits non-zero and reports the failure on its error stream,
  and the backup directory retains only previously published artifacts

## Source References

- Non-Negotiable Rule 1, Vision, on verifiable recovery of owner-controlled data.
- Schema Isolation, heart-and-soul/security.md, on least-privilege runtime roles.
- RFC 0006, Database Connection Scoping, and RFC 0008, Invariants.
