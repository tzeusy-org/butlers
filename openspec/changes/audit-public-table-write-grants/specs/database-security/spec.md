## ADDED Requirements

### Requirement: Public Table Write Narrowing Must Persist Across Bootstrap

Any change that narrows runtime-role or `connector_writer` write privileges on a `public` table SHALL be implemented with a mechanism that survives a rerun of `scripts/init-db.sql` and the `ALTER DEFAULT PRIVILEGES` it registers. A `REVOKE` issued only from an Alembic migration SHALL NOT be accepted as the narrowing.

#### Scenario: A migration-only REVOKE is rejected

- **WHEN** a proposed narrowing consists of a `REVOKE` in an Alembic migration with no change to the bootstrap and no row-level security
- **THEN** the proposal is incomplete, because the next `init-db.sql` run re-grants `SELECT, INSERT, UPDATE, DELETE` on all public tables
- **AND** the narrowing is not merged until it is expressed as a post-grant revoke in `init-db.sql`, an ownership-transfer finalizer, or a row-level-security policy

#### Scenario: Narrowing is proven against a real bootstrap

- **WHEN** a narrowing for a public table is proposed for merge
- **THEN** its tests run the bootstrap twice against a real PostgreSQL database and assert the narrowed privilege or policy holds after the second run
- **AND** they assert that each legitimate writer for that table still succeeds and that a runtime role outside the writer set is refused

### Requirement: Narrowing Preserves The Dashboard And Daemon Writers

A narrowing of a public table SHALL preserve every legitimate writer recorded for that table in the writer inventory, including writers that run on the dashboard API's shared login without `SET ROLE`.

#### Scenario: QA patrol narrowing preserves the synthetic-finding writer

- **WHEN** write access to `public.qa_patrols` is narrowed
- **THEN** the QA daemon role keeps the `INSERT` and `UPDATE` paths used for patrol start, completion, overlap skip and stale-row recovery
- **AND** `POST /api/qa/dev/synthetic-findings`, which inserts an `operator_synthetic` patrol row through the dashboard API, still succeeds when its feature flag is enabled
- **AND** any other runtime role is refused `INSERT`, `UPDATE` and `DELETE` on `public.qa_patrols`

#### Scenario: Row-level security does not lock out the owner login

- **WHEN** a narrowing uses row-level security on a table that the dashboard API also writes
- **THEN** it uses `ENABLE ROW LEVEL SECURITY` without `FORCE`, or adds an explicit policy for the shared login
- **AND** a development stack without runtime roles continues to write the table through the owner login

### Requirement: Narrowing Decisions Record Evidence And Unknowns

A change that proposes narrowing public table writes SHALL cite file and line evidence for each writer it relies on, name the principal that runs it, and list tables whose writers could not be established as requiring further analysis.

#### Scenario: Insufficient evidence blocks narrowing

- **WHEN** the writer set of a public table cannot be established from the migration chain, bootstrap and a scan of `src/` and `roster/`
- **THEN** the change lists the table as having insufficient evidence
- **AND** no narrowing of that table is merged until a runtime trace or analysis establishes its writers
