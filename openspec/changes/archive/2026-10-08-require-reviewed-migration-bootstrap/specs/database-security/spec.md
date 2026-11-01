## ADDED Requirements

### Requirement: Reviewed Bootstrap Before Supported Alembic Execution

Every supported database, including ordinary dev, SHALL complete the reviewed `scripts/init-db.sql` using its distinct privileged cluster-superuser bootstrap identity before its first online Alembic migration, and SHALL rerun that managed procedure when its managed role/schema/interface surface changes. Online programmatic and direct Alembic admission SHALL read the actual target database's current catalog before extension, target-schema, version-table or revision mutations; missing, unreadable or untrusted required bootstrap provenance SHALL fail closed with a bounded prerequisite error pointing to `scripts/init-db.sql`. The check SHALL validate required extensions, the current bootstrap-managed runtime-role/schema and migration-membership/default-ACL profile, and the exact trusted protected admin signatures/ownership/definer/search-path profile, without executing installers, issuing grants, changing roles or trusting a writable completion marker. Installer-ready and legitimately finalized protected states SHALL both be accepted: completion may consume one-shot installer grants, and a repeated or second-schema migration SHALL NOT demand those consumed grants when the boundary is correctly finalized. Applied protected migrations SHALL retain their independent state-specific guards. The managed bootstrap prerequisite SHALL NOT enable the executor, supply its secret, activate a drill or widen normal migration/runtime privileges; ordinary dev SHALL retain its base-only service selection. Connectivity/authentication errors SHALL remain distinguishable from a successfully inspected missing prerequisite, and diagnostics SHALL omit connection credentials, private row contents and raw SQL values. Supported privileged downgrade operations SHALL retain their existing managed authorization and data-preservation constraints; a bootstrap check SHALL NOT authorize a normal role to perform them. No invocation SHALL run the privileged script blindly before every migration, self-bootstrap, waive provenance or mark a partial migration as complete. The required default-ACL admission profile SHALL cover stable bootstrap authority and own-schema/base privileges; ordinary migration-owned Relationship SELECT defaults on Switchboard SHALL remain outside that profile because applied core_001 revokes them during legitimate bounded replay and core_077 repairs them. Their absence SHALL NOT waive any stable required privilege or attest working runtime read access; actual catalog and effective-role readback SHALL independently distinguish the bounded absent state from the repaired state without trusting a revision, marker or object name as provenance.

ID: REQ-database-security-011
Source: bu-kqnum.8.10 owner decision and clarification (2026-08-15); about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md; scripts/init-db.sql; REQ-database-security-006
Scope: v1-mandatory

#### Scenario: Existing unbootstrapped core_195 is refused before side effects
- **WHEN** a genuine disposable database has the complete historical core chain applied through core_195 and lacks trusted managed bootstrap provenance, and its ordinary migration login requests an online upgrade
- **THEN** admission fails with the bounded bootstrap-prerequisite error naming `scripts/init-db.sql`, while separate readback confirms unchanged core_195 tracking, planted application data and the extension/schema/version/object inventory captured before the request

#### Scenario: Managed bootstrap permits the same existing database upgrade
- **WHEN** the distinct disposable bootstrap superuser successfully runs the checked-in fail-fast `scripts/init-db.sql` for that database and its actual normal migration login, with no executor secret or enablement, and the normal login retries the upgrade
- **THEN** core_196 and the requested supported later revisions complete, a separate normal-login readback witnesses the expected revision and planted data, and genuine runtime-role controls retain the protected interface restrictions

#### Scenario: First fresh online migration requires bootstrap
- **WHEN** a fresh supported database is inspected through either the programmatic runner or direct online Alembic entry point before its first migration
- **THEN** absent prerequisites refuse before extension, schema or version-table DDL, whereas the same request after the real managed bootstrap succeeds without requiring migration-created protected tables to exist in advance

#### Scenario: Finalized repeated and multi-schema migrations remain valid
- **WHEN** the first schema has legitimately finalized the protected interfaces and consumed their one-shot installer grants, and the normal login repeats its upgrade or upgrades another target schema with multiple known chain heads
- **THEN** read-only admission accepts the legitimate finalized state, all-chain revision resolution and per-schema tracking remain correct, planted data survives, and no installer privilege is restored or privileged bootstrap repeated automatically
- **AND** a genuine checked-init, bounded core_076 and second-schema continuation remains admitted while the ordinary Relationship-to-Switchboard read default is absent, required own-schema privilege loss still refuses without catalog changes, and immutable core_077/head repair restores both the read default and actual runtime SELECT without losing the planted row

#### Scenario: Partial bootstrap and misleading objects do not satisfy admission
- **WHEN** required managed catalog entries are absent or have wrong ownership, signatures, definer mode, pinned search path, membership or required privileges, including a caller-owned lookalike, a temporary/catalog shadow, or a caller-writable completion marker
- **THEN** admission refuses before migration side effects using qualified catalog identities, with a genuinely bootstrapped companion accepted; unchanged private authority objects and data are witnessed independently

#### Scenario: Managed surface change requires privileged rerun
- **WHEN** the reviewed bootstrap and its checked-in prerequisite profile gain a managed role/schema/interface surface that is absent from an existing supported database
- **THEN** the next online admission reports the missing prerequisite and permits retry only after the authorized managed bootstrap rerun; changing comments, a caller label or an unrelated migrated revision cannot satisfy that profile

#### Scenario: Dev service selection and role safety remain independent
- **WHEN** an ordinary dev database is bootstrapped and migrated without selecting the protected restore-drill overlay or providing an executor secret
- **THEN** its normal migration and runtime roles remain nonsuperuser without self-bootstrap or recovery-owner membership, the executor remains unenabled, and base-only services can proceed without weakening the isolated executor or dashboard read-only boundary

#### Scenario: Downgrade and operational failures retain their boundaries
- **WHEN** an online operation encounters connection failure, insufficient catalog visibility, a normal-role request to cross a protected downgrade boundary, or an authorized managed privileged downgrade followed by rebootstrap and upgrade
- **THEN** errors remain truthful and sanitized, unreadable provenance never admits migration, normal roles cannot drop the protected boundary, and only the existing authorized disposable privileged sequence may complete with its actual revision/data state independently read back

## MODIFIED Requirements

### Requirement: Graceful Fallback Policy

SET ROLE enforcement SHALL degrade gracefully in environments where runtime roles are absent. The system SHALL retain the existing development fallback for direct runtime connection setup when PostgreSQL roles cannot be verified, including the warning and shared-user connection behavior. This fallback SHALL NOT waive REQ-database-security-011: a supported daemon or launcher that requests online migrations SHALL satisfy the reviewed bootstrap prerequisite first, and missing provenance SHALL stop that migration/startup path even under dev. No fallback SHALL authorize self-bootstrap or recovery privileges. SET ROLE enforcement SHALL retain its existing graceful development fallback for ordinary non-DND workloads. When runtime roles are absent, the normal non-DND context path may continue under the shared database user with the existing warning and application-level authorization behavior. The DND mutation and DND-based durable admission are a strict exception. If a caller cannot prove its active runtime role, the DND RLS/ACL boundary, the singleton guard, or database-time revalidation, it SHALL fail closed before changing DND or writing a durable admission. It SHALL not treat a shared-user development connection, a caller-supplied writer, or migration-owner privilege as a substitute for verified runtime authority.

ID: REQ-database-security-012
Source: about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md (Database Connection Scoping); bu-kqnum.8.10 owner decision and clarification (2026-08-15)
Scope: v1-mandatory

#### Scenario: Missing roles do not widen DND authority
- **WHEN** a development environment lacks the required runtime roles or an
  active `SET ROLE` cannot be verified
- **THEN** ordinary non-DND context behavior follows the existing development
  fallback
- **AND** the canonical DND mutation rejects before any DND row, guard, or
  audit change

#### Scenario: Unprovable DND admission fails closed
- **WHEN** a consumer cannot establish its guarded DND snapshot/admission
  boundary because role, RLS/ACL, guard, or database-time evidence is missing
- **THEN** it writes no durable admission and authorizes no external effect

#### Scenario: Missing roles in development
- **WHEN** the `core_001_foundation` migration ran but could not create roles (e.g., connecting user lacks CREATEROLE)
- **AND** the request is direct development runtime connection setup and requests no online migration
- **THEN** the roles do not exist in `pg_roles`
- **AND** `Database.connect()` detects this and skips the `setup` callback
- **AND** a warning is logged: "Role {role} not found; SET ROLE enforcement disabled. Butler {name} runs with shared-user privileges."
- **AND** all queries execute with the shared database user's privileges (identical to pre-enforcement behavior)
- **AND** no error is raised -- the butler starts and operates normally

- **AND** this direct runtime continuation does not waive a supported online migration/startup prerequisite

#### Scenario: Enforcement in production
- **WHEN** the PostgreSQL instance has roles created by the migration (production default)
- **THEN** SET ROLE enforcement is active for all butler and connector connections
- **AND** the connecting user (`butlers`) must be a member of each runtime role (granted by `core_065`)
- **AND** any query that violates the role's privileges fails with a PostgreSQL permission error
