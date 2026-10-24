## ADDED Requirements

### Requirement: Owner Operations Overlay Storage Isolation
The owner-operations overlay store SHALL enforce append-only write and per-agent read authority in
PostgreSQL independently of HTTP admission. A dedicated direct-login prompt writer with `NOINHERIT`
and `NOBYPASSRLS` SHALL use a dedicated pool. A separate `NOLOGIN NOINHERIT NOBYPASSRLS` role SHALL
own both prompt-history and composition-mode-history tables and SHALL NOT be granted to an
application role. The generic dashboard pool, runtime roles, connectors, and `PUBLIC` SHALL have no
INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, or TRIGGER authority over either table.
RLS SHALL allow the dedicated writer to select and insert overlay and mode-history rows only within
separately authorized owner-management operations, not to select a goal prompt. A fixed database-owned
mapping from each canonical runtime role's `current_user` to one exact roster agent SHALL allow that
runtime to select only its own rows; an unmapped runtime, connector, or generic API login SHALL see
zero protected rows. This fixed role mapping SHALL be only a database upper bound, not a goal grant,
a runner credential or a cross-domain read path. Domain daemons SHALL NOT read operations into
reasoning-session prompts; leads use lead policy and owner profile and children their briefs under
RFC0038 D15. No application path SHALL depend on the generic API pool using `SET ROLE`.
Associated sequence authority SHALL be limited to the dedicated writer, and normal bootstrap/grant
reconciliation SHALL preserve this restricted end state. The generic dashboard login SHALL be
`NOSUPERUSER NOBYPASSRLS` and SHALL neither own nor inherit either table-owner role. Migration and
backup/restore authority SHALL use a separately governed operational principal that is never
available to the dashboard process.

ID: REQ-database-security-015
Source: specify-roster-identity-owner-operations-overlay design D5-D6; heart-and-soul/security.md Least Privilege
Scope: v1-mandatory

#### Scenario: Dedicated direct-login writer appends an overlay
- **WHEN** the prompt API uses a connection authenticated directly as the dedicated prompt writer
- **THEN** PostgreSQL permits SELECT and INSERT required for a versioned overlay append
- **AND** the writer cannot UPDATE, DELETE, or TRUNCATE historical rows

#### Scenario: Dedicated writer appends a composition mode
- **WHEN** a separately authorized owner-management operation appends historical mode evidence using a direct-login dedicated writer
- **THEN** PostgreSQL permits only the SELECT and INSERT needed for that immutable history append
- **AND** no application role can update, delete or truncate mode history, and no append selects a goal prompt

#### Scenario: Runtime role reads only its own agent rows
- **WHEN** a canonical `butler_{name}_rw` runtime connection selects protected operations history
- **THEN** RLS exposes at most rows for the exact agent fixed by `current_user`, with every other agent invisible
- **AND** this database limit grants no goal capability or runner credential and does not inject the rows into a domain or goal system prompt

#### Scenario: Unmapped and connector roles see no prompt rows
- **WHEN** an unmapped runtime role or `connector_writer` selects from the prompt store
- **THEN** the result contains zero protected rows
- **AND** neither role can perform application DML or use the prompt sequence

#### Scenario: Generic dashboard pool has no prompt authority
- **WHEN** the generic dashboard pool connects under its actual no-`SET ROLE` login
- **THEN** it cannot read protected prompt text or perform INSERT, UPDATE, DELETE, or TRUNCATE
- **AND** prompt routes cannot succeed unless they use the dedicated direct-login pool

#### Scenario: Generic dashboard login cannot bypass RLS
- **WHEN** login attributes and application configuration are inspected
- **THEN** the generic dashboard login is `NOSUPERUSER NOBYPASSRLS` and owns neither history table
- **AND** the dashboard process has no migration or backup principal credential

#### Scenario: Non-login table owner cannot become an application bypass
- **WHEN** role membership and login attributes are inspected after migration
- **THEN** the table owner has `NOLOGIN NOINHERIT NOBYPASSRLS`
- **AND** no application login is a member of or can set role to the table owner

#### Scenario: Historic broad grants are removed
- **WHEN** the forward privilege cutover completes
- **THEN** catalog inspection proves the table and sequence grants introduced by `core_098` are absent from `PUBLIC`, all runtime roles, connectors, and the generic API login
- **AND** only the dedicated writer and separately governed operational migration/backup paths retain their specified authority

#### Scenario: Bootstrap rerun preserves least privilege
- **WHEN** normal database bootstrap or grant reconciliation runs after privilege cutover
- **THEN** the restricted table, sequence, ownership, and RLS state remains unchanged
- **AND** no historical broad DML grant is restored

#### Scenario: Unmapped agent input cannot widen runtime reads
- **WHEN** a runtime query supplies another agent's name as data
- **THEN** RLS derives visibility only from `current_user` and the fixed mapping, with no additional row visibility from the supplied name
- **AND** role visibility itself does not widen a goal's capability grant or expose history through a new model-facing consumer

### Requirement: Prompt History Migration and Rollback Integrity
Migration of owner-operations storage SHALL preserve every existing prompt-history row and its
version, text, timestamp and actor while labeling it `legacy_full_replacement`, never reinterpreting,
copying, activating or deleting it as owner operations. Historical append-only per-agent mode records
SHALL retain the same owner, writer, RLS, grant and audit protections and truthful seeded-versus-owner
attribution. `precutover_legacy_hold`, `roster_overlay` and `legacy_full_replacement` SHALL NOT become
selectable goal-runtime modes or re-enable domain-daemon prompt composition. New agents SHALL NOT
receive a legacy hold, and leaving historical hold SHALL NOT permit re-entry.
Separately authorized schema-aware code, dedicated pool/credentials and real-PG privilege proof
SHALL precede live storage changes; failure SHALL preserve the prior verified stage and all rows
and block the next stage. An authenticated owner SHALL explicitly review any reuse of legacy text;
migration SHALL NOT infer identity versus operations or activate it automatically. RFC0038 requires
one hard runtime cutover, not per-agent old/new reasoning paths. Operational rollback/restore during
its separately approved observation window SHALL retain exact prior-stage evidence, explicit
acknowledgement, CAS/atomic audit where writes are authorized, and least privilege; it SHALL NOT
restore broad runtime/connector/generic-API/table/sequence grants or select a second live goal mode.
Retiring obsolete machinery SHALL require a later reviewed authorized change without deleting
history. This spec-only amendment SHALL perform no migration, activation or retirement.

ID: REQ-database-security-010
Source: specify-roster-identity-owner-operations-overlay design D6 and Migration Plan
Scope: v1-mandatory

#### Scenario: Existing rows retain legacy provenance
- **WHEN** a separately authorized additive migration processes existing prompt-history rows
- **THEN** every row retains its exact version, text, timestamp and actor and is labeled `legacy_full_replacement`, never active or represented as an owner-operations overlay
- **AND** historical seeded mode records retain unreviewed provenance without fabricated owner selection or rewritten history
- **AND** no live goal compatibility selector is seeded

#### Scenario: Failed migration preserves the prior verified stage
- **WHEN** schema backfill, role creation, RLS setup, credential provisioning, or verification fails
- **THEN** the next cutover stage does not begin
- **AND** the prior verified behavior and every historical row remain available
- **AND** no partial success is reported

#### Scenario: Owner review precedes per-agent cutover
- **WHEN** an agent has only legacy full-replacement history or a historical seeded hold
- **THEN** migration code does not infer or copy identity/operations text and any later reuse waits for explicit authenticated owner review
- **AND** that review does not authorize per-agent goal modes, automatic activation or domain reasoning sessions

#### Scenario: New agent cannot enter a legacy hold
- **WHEN** an agent is created after the migration
- **THEN** no migration or API assigns `precutover_legacy_hold` or legacy goal composition
- **AND** its versioned guidance is governed by the same lead-policy/profile and child-brief boundary, without a compatibility default

#### Scenario: Prepared agent cuts over without history rewrite
- **WHEN** separately authorized implementation passes the single RFC0038 hard-cutover verification
- **THEN** leads use lead policy and owner profile and children their briefs; domain guidance is versioned and no per-agent old/new reasoning selector remains
- **AND** all legacy rows and per-agent provenance remain byte-identical history, without silent overlay activation or alteration of another agent's records

#### Scenario: Rollback restores selection without restoring broad grants
- **WHEN** a separately authorized operator performs an approved bounded operational rollback/restore
- **THEN** exact prior-stage evidence and explicit acknowledgement govern the restore, with CAS and metadata-only atomic audit for authorized state transitions
- **AND** it does not select a legacy goal mode, update/delete historical rows or restore broad grants
- **AND** RLS, dedicated-writer isolation and runtime/connector/generic-API DML denial remain in force

#### Scenario: Legacy retirement requires a later reviewed change
- **WHEN** the separately approved observation window ends successfully
- **THEN** removal of obsolete selection machinery requires a later reviewed and authorized change
- **AND** this migration does not delete legacy history or silently retire operational recovery obligations
- **AND** historical preservation does not imply permission to run a legacy goal selector
