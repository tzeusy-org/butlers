## Decision

Keep the applied core_255 source immutable. Its targeted grant and retained evidence are separate behaviors, and grant widening is not an ownership repair. The new requirement describes this bounded lifecycle; it does not alter caller/source validation, ENABLE versus FORCE RLS, or candidate cascade semantics.

## Verification

The exact historical source is stored as checksum-verified inert test data. Runtime-generated snapshots keep the complete canonical Alembic/bootstrap environment and isolate destructive variants in empty disposable databases. Current controls plant real rows, use actual SET ROLE identities and production bootstrap replay, and record definer ownership independently. Hosted PR PostgreSQL results supply execution evidence; local collection and strict spec validation supply only their named structural evidence.

## Budget

One new grant/visibility test function is balanced by grouping core_168 and core_241 downgrade parameters. Each older boundary retains its fresh database, original helper body and every assertion. core_255 remains independently collected. Integration collected delta is zero; no baseline increase.

## PostgreSQL evidence correction

The first hosted falsification rejected the shaping prediction that ordinary rollback must fail on bootstrap-owned objects: the fixture login owns its database and implicitly owns public through pg_database_owner. Generic DROP permits schema-owner authority, while ALTER/REPLACE still needs object ownership. Tests record both operations separately. This changes no production grants, ownership, role membership, data-retention policy or existing requirement body. The original shaping seal remains immutable.
