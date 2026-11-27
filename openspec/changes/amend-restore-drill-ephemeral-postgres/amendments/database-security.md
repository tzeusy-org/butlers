## Proposed modified requirement

### Requirement: Restore-Drill Isolated Executor Boundary

The restore drill SHALL create and restore databases only inside per-attempt
PostgreSQL in its own disposable Pod. The drill SHALL have no live PostgreSQL
credential or database route. Following cutover, every live-cluster login
outside the managed bootstrap superuser SHALL remain `NOCREATEDB`, and the
managed retirement SHALL remove the `restore_drill_executor` login and active
privilege path only after the old executor is quiescent. Old results retain
their lineage. The dedicated protected writer SHALL accept only bounded,
authenticated results and SHALL expose no arbitrary SQL to the workload.

ID: REQ-database-security-006
Source: RFC 0006 § Database Connection Scoping; heart-and-soul/security.md § Schema Isolation
Scope: v1-mandatory

#### Scenario: Bootstrap provisions only the isolated executor role

- **WHEN** managed bootstrap runs after the approved CronJob cutover
- **THEN** it does not provision the old live restore executor login, restores
  no retired grant on retry, and leaves every non-bootstrap live login
  `NOCREATEDB`
- **AND** the sidecar's scratch-only credentials cannot authenticate on the
  live cluster

#### Scenario: Dashboard and runtime roles cannot create a scratch database

- **WHEN** a dashboard, connector, migration or ordinary butler role attempts
  to create a database on live PostgreSQL
- **THEN** PostgreSQL denies the operation and the failure does not shed role
  restrictions or grant a broader role

#### Scenario: Executor credential is isolated at the process boundary

- **WHEN** the ordinary application and approved drill deployments render
- **THEN** the drill lacks live PostgreSQL credentials, shared
  `POSTGRES_*` and `DATABASE_URL`, cluster-management tokens and host access
- **AND** only the verifier reads the read-only backup mount and the only
  database it can access is the local PostgreSQL sidecar on a shared socket
- **AND** evidence submission uses an approved, generation-bound identity for
  only one fixed operation at the protected writer
- **AND** the writer's live persistence privileges are unavailable to the drill,
  shared runtime roles and public projections

#### Scenario: Admin bootstrap provenance cannot be substituted

- **WHEN** an untrusted owner has created compatible schema or writer objects
- **THEN** the approved writer bootstrap refuses the unrelated provenance before
  any privilege grant, ownership transfer or result submission
- **AND** a managed install, retry or rerun validates writer object ownership,
  exact interfaces and grants before accepting traffic

#### Scenario: Shared authority staging cannot be blessed

- **WHEN** a shared credential stages superficially compatible result data or a
  replacement writer interface
- **THEN** deployment refuses the staged authority before role grants or trust
  handoff, even if shape and caller-controlled markers match
- **AND** old protected evidence remains readable with its original provenance

#### Scenario: Privilege repair requires the managed bootstrap path

- **WHEN** an operator sees a retired-executor privilege or ownership failure
- **THEN** the drill remains disabled while the approved management path resolves
  the discrepancy
- **AND** documented recovery never widens a live role, pre-creates live scratch
  or offers an application superuser connection

## Source References

- [Vision](../../../../about/heart-and-soul/vision.md), non-negotiable rules 1 and 3.
- RFC 0006, Database Connection Scoping, and RFC 0008, Container Environment Isolation.
