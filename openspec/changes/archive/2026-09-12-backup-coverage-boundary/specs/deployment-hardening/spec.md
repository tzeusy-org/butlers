## MODIFIED Requirements

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
SHALL be limited to the trusted-bootstrap control plane — objects that the
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

#### Scenario: Documented restore drill exists and is verifiable
- **WHEN** an operator follows the documented backup-and-restore procedure
- **THEN** a backup of the PostgreSQL data plane can be produced and restored to a
  working instance, and the procedure includes a verification step proving the
  restored data is intact

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
