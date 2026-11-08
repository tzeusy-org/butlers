## ADDED Requirements

### Requirement: Private History Enters The Backup Only Through Fixed Export Identities

The full application-data backup SHALL include the complete DND history (every
mutation receipt including expired and superseded rows, the generation guard, and the
`signal_type = 'dnd'` rows of `user_context`) and, once those relations exist, the
ten perception relations of the perception registry. Their rows SHALL be captured in
the same exported snapshot as the ordinary dump, through fixed `SECURITY DEFINER`
functions owned by the relations' fenced owner and executable only by dedicated
`NOLOGIN` backup roles. The dump identity SHALL gain no grant, role membership or
EXECUTE for this path, the owner roles' no-membership fences SHALL remain, and
runtime grants and forced owner-only row security SHALL be unchanged.

The interface SHALL ship dark. Until the owner activates an export credential, a
backup run SHALL publish without these populations and SHALL state the omission on
its error stream; once an export identity is configured, any export or proof failure
SHALL refuse publication. Provisioning a login, delivering a credential, deployment and
restore execution are owner activation steps, not effects of this requirement.

Restore SHALL reproduce the exact captured rows into a fresh target, SHALL refuse a
target that already holds the history, and SHALL NOT reseed counters or replay
mutations. The backup artifact SHALL be understood to hold this private history, including
rows already expired or superseded in the live database, for up to the configured
backup retention (default 14 days) beyond their live expiry.

Any relation registered as a perception relation SHALL be covered by the export
manifest; a registered relation that exists without an exporter SHALL fail the
manifest guard test rather than be silently omitted.

ID: REQ-deployment-hardening-014
Source: owner decision perception-backup-boundary = A (2026-11-08); bu-cew60z; REQ-deployment-hardening-010
Scope: v1-mandatory

#### Scenario: The history is exported without widening the dump identity
- **WHEN** a backup runs with an activated export identity against a migrated database
- **THEN** the artifact stages every DND receipt, the guard and only the DND context rows, and the dump identity still has no privilege on, membership in or EXECUTE for any of them

#### Scenario: The export is not yet activated
- **WHEN** the interface is installed but no export identity is configured
- **THEN** the backup publishes, states on its error stream that the DND history is not in the artifact, and does not fail or silently omit it

#### Scenario: A perception relation exists without an exporter
- **WHEN** a relation registered as perception is created while its manifest status is not exported
- **THEN** the manifest guard test fails and names the relation

#### Scenario: Restore targets a populated database
- **WHEN** the staged history is restored into a target that already holds DND history
- **THEN** restore refuses and leaves the target unchanged
