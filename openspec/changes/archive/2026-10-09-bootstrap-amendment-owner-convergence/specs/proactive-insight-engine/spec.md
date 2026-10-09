## ADDED Requirements

### Requirement: Privileged Bootstrap Converges the Amendment Table Owner

The privileged managed bootstrap SHALL narrowly converge an existing ordinary `public.insight_amendments` table from the actual current bootstrap owner to the existing configured trusted ordinary migration identity before subsequent ordinary core replay. An absent table and an already-converged owner SHALL be no-ops. Unexpected relation kind, third ownership or unsafe/unknown target identity SHALL fail closed; no generic owner reassignment, role creation, new grant, destructive recreation, applied-revision rewrite or new caller interface SHALL be introduced. Read-only initial classification SHALL precede bootstrap side effects, and the transition SHALL revalidate qualified object/owner/target identity under a finite nonwaiting exclusive lock. Its atomic ownership block SHALL preserve complete amendment rows, logical definitions, FK, runtime effective privileges and grant options, RLS flags/policy bodies and independently installed functions. Expected PostgreSQL owner-OID ACL and dependent-index/TOAST/owned-sequence ownership effects SHALL be separately measured. The trusted migration owner's ordinary RLS bypass SHALL be explicit and SHALL NOT be conflated with runtime-role authority. Existing historical diagnostics, six causal variants, 45 role controls, two bootstrap replays, 43-row baseline, all amendment retention states and bounded cumulative CHECK/lossy-fold/q34 invariants SHALL remain enforced. Actual ordinary canonical replay SHALL reach the dynamic core head and durable per-schema version/data witnesses; metadata, a stamped head, mocks or expected-failure green SHALL NOT establish repair. Function/caller/FK/FORCE-RLS hardening remains a separate contract.

ID: REQ-proactive-insight-engine-033
Source: RFC0006; scripts/init-db.sql Important ownership note; bu-d55reo adopted complete canonical P0-P9; scripts/init-db.sql qualified owner convergence
Scope: v1-mandatory

#### Scenario: First absent table and already ordinary-owned replay remain no-ops
- **WHEN** the managed privileged bootstrap encounters no amendment table, or an actual ordinary-first table already owned by the configured trusted migration login, including a repeated convergence
- **THEN** the ownership block issues no ownership-changing DDL and separate readback confirms unchanged relation/data/catalog authority for each present-table companion
- **AND** the ordinary-first and repeated canonical migration paths retain their genuine dynamic-head and runtime-role controls

#### Scenario: Populated bootstrap-owned table permits real ordinary continuation
- **WHEN** the exact current privileged core_255 installation retains 43 amendment rows after its two baseline bootstrap replays, and the actual unmodified privileged bootstrap converges the qualified table to its configured trusted normal migration identity
- **THEN** the actual ordinary canonical entrypoint completes through the dynamic core head without stamping past255, and separate per-schema version and complete-row readbacks witness durable success
- **AND** a second-schema continuation and a repeated bootstrap preserve the same completed authority and data

#### Scenario: Unexpected owner, object or configured identity refuses before side effects
- **WHEN** initial read-only classification finds a planted third-owned relation, wrong relation kind, missing configured identity or a runtime/recovery identity instead of the trusted migration identity
- **THEN** bootstrap refuses before its first extension/role/schema/ACL side effect and separate planted catalog/data/version witnesses remain unchanged
- **AND** the corresponding genuine bootstrap-owned ordinary table and valid configured target are accepted without granting membership or appropriating another owner

#### Scenario: Transfer lock, race and transaction failures remain bounded and atomic
- **WHEN** another connection holds the qualified table's exclusive lock, or the actual OID/owner/target changes before transition, or a disposable failure aborts the ownership block
- **THEN** nonwaiting exclusion or identity revalidation refuses, the ownership block releases its resources on transaction exit, and independent readback proves no partial owner/data/ACL/version transition
- **AND** release of the held lock permits the same genuine operation to complete
- **AND** the receipt separately records any earlier committed legacy-bootstrap effects and never asserts rollback of the whole existing psql script

#### Scenario: Runtime matrix survives expected owner effects
- **WHEN** table ownership changes under the privileged bootstrap and the configured normal migration owner performs its real post-convergence DDL/row observation
- **THEN** its trusted-owner RLS bypass is explicit, while actual runtime SET ROLE acquisitions retain the existing Switchboard/peer/foreign-schema/write-refusal behavior and grant options
- **AND** raw table/column ACL observations show only the expected old-owner-to-new-owner grantor/grantee remapping or consolidation, dependent ownership follows the table, and logical index/FK/default definitions, RLS flags/policies and complete original rows remain unchanged
- **AND** no new DML grant, FORCE-RLS or function-authority convergence is inferred

#### Scenario: Exact historical failure remains a causal negative
- **WHEN** only the uniquely identified convergence block is neutralized in a full source-bound disposable bootstrap copy and the exact populated supported-current state is replayed through the ordinary entrypoint
- **THEN** actual core_255 CREATE INDEX again raises42501 before ALTER while qualified object/metadata positives and separate unchanged catalog/data/version witnesses remain
- **AND** all original 45 role controls, six separately labeled historical variants and two original bootstrap replays remain enforced, the fixture path is restored on normal and exceptional exit, and actual unmodified bootstrap supplies the success companion before any destructive diagnostic

#### Scenario: Independent lifecycle proof retains historical contracts and attribution
- **WHEN** the bounded repair is validated, reviewed and delivered through normal native/source/hosted/protected operations
- **THEN** all existing amendment states, candidate retention/cascade, documented core168/241/255 lossy folds, cumulative CHECK and q34 provenance assertions remain enforced with independent durable readback
- **AND** function ownership/caller/EXECUTE and FK/FORCE-RLS hardening are reported separately, source/mock/collection or historical diagnostic green is never called SQL repair, and full original delivery outcomes remain required for closure
