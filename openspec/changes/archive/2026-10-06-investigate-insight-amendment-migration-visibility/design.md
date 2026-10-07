## Decision

Keep the applied core_255 source immutable. Its targeted grant and retained evidence are separate behaviors, and grant widening is not an ownership repair. The new requirement describes this bounded lifecycle; it does not alter caller/source validation, ENABLE versus FORCE RLS, or candidate cascade semantics.

## Verification

The exact historical source is stored as checksum-verified inert test data. Runtime-generated snapshots keep the complete canonical Alembic/bootstrap environment and isolate destructive variants in empty disposable databases. Current controls plant real rows, use actual SET ROLE identities and production bootstrap replay, and record definer ownership independently. Hosted PR PostgreSQL results supply execution evidence; local collection and strict spec validation supply only their named structural evidence.

## Budget

One new grant/visibility test function is balanced by grouping core_168 and core_241 downgrade parameters. Each older boundary retains its fresh database, original helper body and every assertion. core_255 remains independently collected. Integration collected delta is zero; no baseline increase.

## PostgreSQL evidence correction

The first hosted falsification rejected the shaping prediction that ordinary rollback must fail on bootstrap-owned objects: the fixture login owns its database and implicitly owns public through pg_database_owner. Generic DROP permits schema-owner authority, while ALTER/REPLACE still needs object ownership. Tests record both operations separately. This changes no production grants, ownership, role membership, data-retention policy or existing requirement body. The original shaping seal remains immutable.

## Ordinary bootstrap-owned traversal

The actual normal `run_migrations(chain="core", schema="health")` entrypoint now runs before any ordinary DROP/recreate in each current hypothetical DROP diagnostic. It also runs separately on the exact current bootstrap-created state after two full init-db replays and all role/data controls. Only health's independent version table is stamped at core_254; shared predecessors are already applied, and core_255 must execute. Receipts name the reached upgrade frame, statement and SQLSTATE, with separate before/after ownership, object, catalog, complete row and version readback. A failed Alembic transaction must leave health at core_254 and public at core_255. Raw ALTER and later normal-owned traversal remain independent controls.

The current bootstrap-owned ordinary replay's existing 42501 is recorded as a failure, even though the metadata and role matrix pass. The hypothetical empty-DROP variants are diagnostics, not supported production repairs. No deployed-state claim follows from these disposable observations.

The concrete forward proposal is a separate narrowly scoped privileged bootstrap convergence: resolve the configured migration identity, guard the qualified amendment table and converge its owner to that normal trusted migration identity before ordinary core replay. Preserve rows, runtime ACL entries, RLS settings and policies, and validate repeated convergence plus the same role negatives on disposable PostgreSQL. Owner ACL entries necessarily follow the owner change and must be recorded separately. This follows init-db's existing normal-owner ALTER contract. This PR does not implement or certify that repair, change applied core_255, reserve a revision, widen runtime grants or converge function owners. Existing .33 retains definer/caller/FK/FORCE-RLS scope. Fresh ownership/allocation census and separate review are required before any later production change.
