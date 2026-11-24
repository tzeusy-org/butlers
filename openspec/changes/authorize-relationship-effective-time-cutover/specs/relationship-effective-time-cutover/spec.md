## ADDED Requirements

### Requirement: Plain hard cutover for gate G
The Relationship legacy-index cutover, symbolic gate G, SHALL be a plain hard cutover: G SHALL be
authorized by shipping in the same release as the complete compatible mutator inventory and by its
own in-database lock-and-refuse check, run after every database client of the target database has
been stopped. No signer, signing key, signed or unsigned receipt, receipt verifier, wrapper-produced
test receipt, freshness witness or orchestrator lifecycle fence SHALL be required, read or accepted
as authority for G. A caller argument, environment variable, configuration value, label or file
SHALL NOT grant or bypass G.
This proof satisfies the adopted `relationship-fact-effective-time` precondition that every
Relationship instance contains the complete compatible mutator inventory and every old image is
proven absent, in this way. Every writer of `relationship.entity_facts` ships in the Relationship
release image, so the release that carries G carries the inventory, and REQ-relationship-effective-time-cutover-002
keeps that inventory complete and tested at the commit that carries G. The old-image-absent
proof is G's own check: G drops the legacy index only inside a transaction that holds an exclusive
lock on `relationship.entity_facts` and has found no other client connected to the target database,
so no old writer is present while the index changes, and the rollout starts only the release that
carries G afterward. Old images are proven absent from the database at G, not inventoried by an
orchestrator.

ID: REQ-relationship-effective-time-cutover-001
Source: bu-4awqyt owner answer B (2026-10-20, owner-questionnaire-2026-10-20); relationship-fact-effective-time relationship-facts uniqueness transition; bu-mw7muo
Scope: v1-mandatory

#### Scenario: G needs no signer or receipt

- **WHEN** the release carrying G migrates a database with no other client connected
- **THEN** G MUST apply without any signer, key, receipt, test receipt or fence file
- **AND** G MUST NOT read a receipt argument or path even if one is supplied

#### Scenario: Caller assertions cannot authorize G

- **WHEN** a caller supplies a flag, x-argument, environment variable, configuration value or
  disposable label claiming G is safe while another client is connected
- **THEN** G MUST still refuse `active_writer` before any DDL

#### Scenario: Old images are proven absent by the database

- **WHEN** G's lock-and-refuse check passes
- **THEN** no client other than G's own session is connected to the target database while the
  legacy index is dropped
- **AND** that check, plus starting only the release that carries G afterward, MUST be the whole
  old-image-absent proof

### Requirement: Compatible mutator inventory ships with G
The mutator inventory SHALL be the static guard `tests/contracts/test_entity_facts_mutator_inventory.py`,
which scans `roster/relationship` and `src/butlers` and requires its inventory to equal the found
`relationship.entity_facts` mutators exactly, each classified as compatible or fenced. G SHALL merge
only in a commit where that guard is clean and every real-PostgreSQL transition and mutator scenario
named by `relationship-fact-effective-time` task 3.5, plus the G tests required by
REQ-relationship-effective-time-cutover-003, pass in the repository's required checks. The
compatible mutators and G SHALL ship in one release; no release SHALL carry G without them.
Central-writer proof alone MUST NOT satisfy this requirement.

ID: REQ-relationship-effective-time-cutover-002
Source: relationship-fact-effective-time design section 8 and task 3.5; bu-4awqyt owner answer B (keep the compatible-mutator inventory)
Scope: v1-mandatory

#### Scenario: Inventory drift blocks G

- **WHEN** the static guard finds an unclassified or uninventoried mutator at the commit carrying G
- **THEN** the guard MUST fail and G MUST NOT merge until the mutator is made compatible or fenced
  and inventoried

#### Scenario: Central-writer tests do not stand in for the inventory

- **WHEN** only central-writer tests pass and a named mutator scenario fails or is absent
- **THEN** the commit carrying G MUST NOT be treated as satisfying the inventory requirement

### Requirement: Lock-and-refuse proves no old writer during G
G SHALL run as the migration login role: the database user of the migration connection, which
runs `butlers db migrate` and owns the butler schemas, never a `butler_*_rw` runtime role assumed by
`SET ROLE`. That role SHALL be a superuser or a member of the predefined role `pg_read_all_stats`,
so that `pg_stat_activity` shows it the database, user and backend type of every other role's
sessions. Without that privilege, PostgreSQL masks other roles' session details, and a naive count
could report zero clients beside live writers.
The G upgrade SHALL, in one transaction and before any DDL: acquire
`LOCK TABLE relationship.entity_facts IN ACCESS EXCLUSIVE MODE NOWAIT`; then verify that the
session's current role is a superuser or has the privileges of `pg_read_all_stats`; then read every
`pg_stat_activity` row other than its own `pg_backend_pid()`; then verify that
`uq_ef_spo_active` exists with its original definition and that the occurrence index exists and is
valid. It SHALL hold the lock through the DDL and the commit. Lock contention SHALL refuse
`lock_unavailable`. A missing `pg_read_all_stats` privilege SHALL refuse `db_activity_unverifiable`.
Any row whose database, user or backend type is NULL or otherwise unreadable SHALL refuse
`db_activity_unverifiable`, whatever database it might belong to. Any readable client backend on
`current_database()`, whatever its role or purpose, SHALL refuse `active_writer`. An unexpected
index state SHALL refuse `cutover_index_state_invalid`.
Every refusal SHALL leave indexes, data and the applied Relationship revision unchanged. Refusal
error output SHALL carry the stable code and counts only, never fact content or SQL text.
Before G runs on a database with existing clients, the rollout SHALL stop every database client of
the target database, which stops every butler and accepts whole-assistant downtime for the
migration, and SHALL afterward start only the release that carries G. Starting an image older than
G after G commits is unsupported; this residual operator risk SHALL be stated in the rollout notes.
Granting `pg_read_all_stats` to the migration login role, where it lacks it, is part of the owner's
rollout, not of any migration.
The implementation SHALL include real-PostgreSQL tests proving that: G refuses `active_writer`, with
the legacy index and revision unchanged, while a second client connected as a different login role
from the migrating role is connected to the target database; G refuses `db_activity_unverifiable`
when the migrating role lacks `pg_read_all_stats` while that second client is connected; G refuses
`lock_unavailable` while another session holds a lock on `relationship.entity_facts`; and G applies
when no other client is connected.

ID: REQ-relationship-effective-time-cutover-003
Source: bu-4awqyt owner answer B (lock-and-refuse plus stop of every database client, with a test that G refuses while another client is connected); relationship-fact-effective-time migration plan steps 3-4
Scope: v1-mandatory

#### Scenario: Another connected client refuses G

- **WHEN** any other client backend is connected to the target database at G's check
- **THEN** the upgrade MUST refuse `active_writer` before DDL
- **AND** `uq_ef_spo_active`, all rows and the applied Relationship revision MUST be unchanged

#### Scenario: Lock contention refuses instead of waiting

- **WHEN** another session holds any lock on `relationship.entity_facts`
- **THEN** the `NOWAIT` lock MUST fail and the upgrade MUST refuse `lock_unavailable` with no change

#### Scenario: A late connection cannot write during the DDL

- **WHEN** a client connects after G's check but before G commits
- **THEN** it MUST NOT read or write `relationship.entity_facts` until G commits, because G holds
  the access-exclusive lock

#### Scenario: Unreadable session activity fails closed

- **WHEN** the migrating role is neither a superuser nor has the privileges of `pg_read_all_stats`,
  or any other `pg_stat_activity` row has a NULL or unreadable database, user or backend type
- **THEN** the upgrade MUST refuse `db_activity_unverifiable` with no change
- **AND** it MUST NOT treat a masked row as belonging to another database

#### Scenario: The refusal test is required

- **WHEN** the change carrying G is reviewed
- **THEN** it MUST include a real-PostgreSQL test that holds a second client connection to the
  target database, logged in as a different role from the migrating role, and asserts G refuses
  `active_writer` with the legacy index and revision unchanged
- **AND** a companion test MUST assert that a migrating role without `pg_read_all_stats` refuses
  `db_activity_unverifiable` under the same second connection

### Requirement: G is an ordinary Relationship revision
G SHALL be a then-free revision in the ordinary Relationship migration chain after the actual
then-current Relationship head, and SHALL drop only `relationship.uq_ef_spo_active`, preserving the
temporal columns, checks and the valid occurrence index. Historical `rel_036` is meeting-debrief
storage and MUST NOT be renamed, overwritten, gated or mistaken for the cutover; no revision number
is reserved by this requirement. Every online migration path, including daemon startup,
`butlers db migrate`, deployment migration runs, test helpers and fresh or disposable databases,
SHALL traverse G through the same check in REQ-relationship-effective-time-cutover-003, with no
gated ceiling, explicit cutover argument or fresh-database exemption. An automatic path that
reaches G while other clients are connected SHALL fail with G's refusal and leave the database at
G's predecessor. Offline SQL generation SHALL refuse G, because the check cannot run offline.

ID: REQ-relationship-effective-time-cutover-004
Source: bu-4awqyt owner answer B (fresh and CI databases migrate through G like any other revision); relationship-fact-effective-time relationship-facts uniqueness transition
Scope: v1-mandatory

#### Scenario: A fresh database migrates through G

- **WHEN** a fresh database is migrated to head with no other client connected
- **THEN** G MUST apply like any other revision, without a receipt or exemption

#### Scenario: An automatic path with live clients stops at the predecessor

- **WHEN** daemon startup or a deployment migration run reaches G while another client is connected
- **THEN** G MUST refuse `active_writer` and the applied Relationship revision MUST remain G's
  predecessor

#### Scenario: Offline mode refuses G

- **WHEN** Alembic runs in offline mode through G
- **THEN** G MUST refuse rather than emit DDL

### Requirement: Rollback and first temporal write boundary
The cutover SHALL preserve the adopted rollback boundary. The first temporal write SHALL be the first
committed state in which any `relationship.entity_facts` row, in any validity, is temporal-bearing
(any effective bound, precision or period id non-null), or in which two active rows share one SPO.
Before G this state is unreachable, because the transition writer refuses temporal intent while the
legacy index exists. Rollback before G SHALL be ordinary transition-release rollback plus the
existing `rel_035` downgrade refusals. The G `downgrade()` SHALL, after stopping every database
client and under the same lock-and-refuse check, verify zero temporal-bearing rows and zero
duplicate active SPOs, then recreate `uq_ef_spo_active` with its original definition inside that
transaction; only after that commit may the earlier release start. After the first temporal write,
downgrade and old-release rollback MUST be refused `temporal_rollback_prohibited`, and no path SHALL
delete, choose, supersede, flatten or discard rows to recreate the legacy index.

ID: REQ-relationship-effective-time-cutover-005
Source: relationship-fact-effective-time rollback boundary; bu-4awqyt owner answer B
Scope: v1-mandatory

#### Scenario: Rollback before the first temporal write

- **WHEN** G is applied, no temporal-bearing row exists, no SPO has two active rows, and no other
  client is connected
- **THEN** the G downgrade MAY recreate `uq_ef_spo_active`
- **AND** only then MAY the earlier release start

#### Scenario: Rollback after the first temporal write is refused

- **WHEN** any temporal-bearing row or duplicate active SPO exists
- **THEN** the downgrade MUST refuse `temporal_rollback_prohibited` with no change
- **AND** recovery MUST roll forward or follow a separately reviewed data-preserving plan

#### Scenario: No flattening escape exists

- **WHEN** any path would make the legacy index buildable by deleting, superseding, collapsing or
  clearing temporal rows
- **THEN** that path MUST NOT exist in the migration or the rollout notes

### Requirement: Ownership and authorization boundary
This change SHALL remain specification and documentation only. It SHALL NOT run a migration,
deploy, stop or start a client, write the database or activate temporal writes. `bu-h3b7t` SHALL own
the implementation: G and its real-PostgreSQL tests in the Relationship migration chain and its
nearest migration test file, any inventory updates in
`tests/contracts/test_entity_facts_mutator_inventory.py`, and the rollout notes in
`docs/operations/relationship-effective-time-cutover.md`. Rolling out G to any real database SHALL
remain an owner act. The fresh-database receipt exemption (`bu-2z6jyb`) is superseded, because no
receipt exists to exempt from. The Kubernetes extension `extend-relationship-cutover-kubernetes`
SHALL be archived unadopted, with no delta applied to `openspec/specs`. Temporal writes SHALL
remain refused `temporal_cutover_pending` until G is applied.

ID: REQ-relationship-effective-time-cutover-006
Source: bu-4awqyt owner answer B (no migration or deploy runs here; rolling out G stays an owner act; supersede bu-2z6jyb; archive the Kubernetes extension unadopted); bu-mw7muo
Scope: v1-mandatory

#### Scenario: Source delivery does not roll out G

- **WHEN** this change or the later G implementation merges
- **THEN** no migration, deployment, client stop or start, or temporal activation MUST occur as part
  of that merge
- **AND** applying G to a real database MUST wait for the owner's rollout

#### Scenario: Superseded receipt work is not revived

- **WHEN** later work cites the fresh-database exemption, the signer, the receipt or the Kubernetes
  fence
- **THEN** it MUST treat them as superseded by this hard cutover and MUST NOT require them for G

**Source References (retained supporting context)**

- Non-Negotiable Rule 1 (user-federated sovereignty over one instance and its data)
- Non-Negotiable Rule 4 (deterministic infrastructure)
- RFC 0006 (database schema and isolation)
- `relationship-fact-effective-time` active change (two-stage uniqueness transition, mutator
  inventory, rollback boundary)
- Owner decision `bu-4awqyt` answer B (2026-10-20)
