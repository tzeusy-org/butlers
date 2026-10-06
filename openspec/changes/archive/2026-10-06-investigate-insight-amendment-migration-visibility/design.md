## Decision

Keep the applied core_255 source immutable. Its targeted grant and retained evidence are separate behaviors, and grant widening is not an ownership repair. The new requirement describes this bounded lifecycle; it does not alter caller/source validation, ENABLE versus FORCE RLS, or candidate cascade semantics.

## Verification

The exact historical source is stored as checksum-verified inert test data. Runtime-generated snapshots keep the complete canonical Alembic/bootstrap environment and isolate destructive variants in empty disposable databases. Current controls plant real rows, use actual SET ROLE identities and production bootstrap replay, and record definer ownership independently. Hosted PR PostgreSQL results supply execution evidence; local collection and strict spec validation supply only their named structural evidence.

## Budget

One new grant/visibility test function is balanced by grouping core_168 and core_241 downgrade parameters. Each older boundary retains its fresh database, original helper body and every assertion. core_255 remains independently collected. Integration collected delta is zero; no baseline increase.
