## MODIFIED Requirements

### Requirement: Switchboard Case And Link Write Authority

Only effective current_user butler_switchboard_rw SHALL INSERT or UPDATE fleet_cases or fleet_case_links during runtime operation. Enabled and forced row-level security SHALL enforce that authority despite broad public GRANTs and bootstrap reruns. A fixed offline restore-only function MAY INSERT complete historical rows into those two named relations under their exact table-owner authority and existing operator membership precondition; it SHALL NOT permit runtime execution, UPDATE, DELETE, arbitrary relation selection, parent synthesis or relaxed constraints. All roles SHALL retain SELECT as a shared case-file read surface; access to a peer private schema SHALL NOT be conferred by this contract.

ID: REQ-fleet-case-file-006
Source: RFC 0032 §Write authority and §Alternatives rejected; heart-and-soul/vision.md Rule 3
Scope: v1-mandatory

#### Scenario: Wrong-role insert or update

- **WHEN** a non-Switchboard runtime role attempts a case or link mutation
- **THEN** INSERT is refused and UPDATE cannot change the row
- **AND** the case and link remain readable

#### Scenario: Bootstrap preserves fence

- **WHEN** scripts/init-db.sql re-widens public DML grants after migration
- **THEN** only current_user butler_switchboard_rw can still insert or update cases and links

#### Scenario: Shared read boundary

- **WHEN** a butler reads a case file
- **THEN** it reads the public case/evidence/link surface without receiving peer private-schema authority


#### Scenario: Complete Fleet history restores with validated foreign keys

- **WHEN** the checked-in backup and restore scripts preserve a complete case/evidence/link family under the source table-owner membership precondition
- **THEN** complete rows, UUIDs, timestamps, nulls, nested JSON, owners and forced runtime fences are retained
- **AND** both expected case foreign keys remain validated and enabled before certification

#### Scenario: Policy drift refuses publication

- **WHEN** either forced case/link relation has a changed or restrictive SELECT-applying policy at the exported snapshot
- **THEN** backup fails loudly without publishing an artifact instead of silently filtering history

#### Scenario: Orphan evidence refuses certification

- **WHEN** a new staged artifact or a legacy ordinary artifact contains evidence without its parent case, or its expected foreign key is missing, unvalidated or disabled
- **THEN** the checked-in restore script refuses certification without deleting evidence or synthesizing parents
- **AND** a failing staged history transaction commits none of its case/evidence/link rows

#### Scenario: Restore importer is unavailable to runtime roles

- **WHEN** an ordinary non-owner runtime or connector role invokes the fixed offline importer, including after bootstrap replay
- **THEN** execution is refused and existing history is unchanged
- **AND** runtime Switchboard case/link writes and shared reads keep their existing effective-role boundaries

