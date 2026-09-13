## ADDED Requirements

### Requirement: Owner Operations Overlay Storage Isolation
The owner-operations overlay store SHALL enforce append-only write and per-agent read authority in
PostgreSQL independently of HTTP admission. A dedicated direct-login prompt writer with `NOINHERIT`
and `NOBYPASSRLS` SHALL use a dedicated pool. A separate `NOLOGIN NOINHERIT NOBYPASSRLS` role SHALL
own both prompt-history and composition-mode-history tables and SHALL NOT be granted to an
application role. The generic dashboard pool, runtime roles, connectors, and `PUBLIC` SHALL have no
INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, or TRIGGER authority over either table.
RLS SHALL allow the dedicated writer to select and insert overlay and mode-history rows. A fixed database-owned
mapping from each canonical runtime role's `current_user` to one exact roster agent SHALL allow that
runtime to select only its own rows; an unmapped runtime, connector, or generic API login SHALL see
zero protected rows. No application path SHALL depend on the generic API pool using `SET ROLE`.
Associated sequence authority SHALL be limited to the dedicated writer, and normal bootstrap/grant
reconciliation SHALL preserve this restricted end state. The generic dashboard login SHALL be
`NOSUPERUSER NOBYPASSRLS` and SHALL neither own nor inherit either table-owner role. Migration and
backup/restore authority SHALL use a separately governed operational principal that is never
available to the dashboard process.

ID: REQ-database-security-009
Source: specify-roster-identity-owner-operations-overlay design D5-D6; heart-and-soul/security.md Least Privilege
Scope: v1-mandatory

#### Scenario: Dedicated direct-login writer appends an overlay
- **WHEN** the prompt API uses a connection authenticated directly as the dedicated prompt writer
- **THEN** PostgreSQL permits SELECT and INSERT required for a versioned overlay append
- **AND** the writer cannot UPDATE, DELETE, or TRUNCATE historical rows

#### Scenario: Dedicated writer appends a composition mode
- **WHEN** the owner-only mode route uses a connection authenticated directly as the dedicated prompt writer
- **THEN** PostgreSQL permits SELECT and INSERT required for one mode-history append
- **AND** no application role can update, delete, or truncate mode history

#### Scenario: Runtime role reads only its own agent rows
- **WHEN** a canonical `butler_{name}_rw` runtime connection selects active overlay history
- **THEN** RLS exposes only rows whose agent matches the fixed mapping for `current_user`
- **AND** rows for every other agent remain invisible

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
- **THEN** RLS still derives visibility from `current_user` and the fixed mapping
- **AND** the supplied name grants no additional row visibility

### Requirement: Prompt History Migration and Rollback Integrity
Migration to roster-plus-overlay composition SHALL preserve every existing prompt-history row and
its version, text, timestamp, and actor while labeling it `legacy_full_replacement`. The migration
SHALL create append-only per-agent composition-mode history protected by the same owner, writer, RLS,
and grant boundary as prompt history. No migration or bootstrap process SHALL reinterpret, copy,
activate, or delete legacy text as owner-operations
content. Compatibility code and the dedicated prompt pool SHALL be available before schema,
behavior, or privilege cutover. Each existing agent SHALL receive a
`precutover_legacy_hold` mode-history row and remain on that last verified behavior until an
authenticated owner explicitly reviews its legacy head and creates or activates an overlay.
Rollback during the approved observation window SHALL restore the selected legacy behavior without
rewriting history or restoring broad runtime, connector, generic API, table, or sequence authority.

ID: REQ-database-security-010
Source: specify-roster-identity-owner-operations-overlay design D6 and Migration Plan
Scope: v1-mandatory

#### Scenario: Existing rows retain legacy provenance
- **WHEN** the additive migration runs against one or more existing prompt-history rows
- **THEN** every row retains its exact version, text, timestamp, and actor
- **AND** every row is labeled `legacy_full_replacement`
- **AND** no row is active or represented as an owner-operations overlay
- **AND** the initial mode-history row selects `precutover_legacy_hold` without claiming owner selection or rewriting prompt history

#### Scenario: Failed migration preserves the prior verified stage
- **WHEN** schema backfill, role creation, RLS setup, credential provisioning, or verification fails
- **THEN** the next cutover stage does not begin
- **AND** the prior verified behavior and every historical row remain available
- **AND** no partial success is reported

#### Scenario: Owner review precedes per-agent cutover
- **WHEN** an existing agent has only legacy full-replacement history and `precutover_legacy_hold`
- **THEN** migration code does not infer which text is identity or operations content
- **AND** roster-plus-overlay activation waits for an authenticated owner to create or activate an overlay explicitly

#### Scenario: New agent cannot enter a legacy hold
- **WHEN** an agent is created after the migration
- **THEN** its initial mode is `roster_overlay`
- **AND** no migration or API path assigns `precutover_legacy_hold`

#### Scenario: Prepared agent cuts over without history rewrite
- **WHEN** the owner activates a reviewed overlay for one agent and that agent passes cutover verification
- **THEN** the owner-gated mode route appends `roster_overlay` and new sessions use roster-plus-overlay composition for that agent
- **AND** legacy rows remain byte-identical history
- **AND** other agents remain on their previously selected modes

#### Scenario: Rollback restores selection without restoring broad grants
- **WHEN** an operator rolls one agent back during the approved observation window
- **THEN** the owner-gated mode route restores that agent's legacy full-replacement selection with compare-and-swap and explicit acknowledgement
- **AND** no historical row is updated or deleted
- **AND** RLS, the dedicated writer, and runtime/connector/generic-API DML denial remain in force

#### Scenario: Legacy retirement requires a later reviewed change
- **WHEN** the observation window ends successfully
- **THEN** legacy-selection removal occurs only through a separately reviewed and authorized change
- **AND** this migration does not delete legacy history or silently retire rollback capability
