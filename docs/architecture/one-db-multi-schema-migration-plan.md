# One-DB Multi-Schema Migration Plan

Status: Proposed (authoritative for epic `butlers-1003`)
Last updated: 2026-02-20
Issue: `butlers-1003.1`

## 1. Goal and scope

Define the target-state topology and cutover sequence for moving from the current
"one PostgreSQL database per butler" model to a single PostgreSQL database with:

- one shared schema: `shared`
- one schema per butler identity: `general`, `relationship`, `health`, `switchboard`, `messenger`

This document is implementation-facing and sets hard constraints for child issues
(`butlers-1003.2+`).

## 2. Target topology (authoritative)

### 2.1 Physical database

- Canonical DB name: `butlers`
- The previous per-butler database names (`butler_general`, `butler_health`, etc.) become legacy sources only during migration.

### 2.2 Schemas

- `shared`: globally shared platform data (starting with `butler_secrets`)
- `general`: general butler data
- `relationship`: relationship butler data
- `health`: health butler data
- `switchboard`: switchboard butler data
- `messenger`: messenger butler data

Reserved future schema names must match butler identity names exactly.

### 2.3 Object placement rule

- Butler-scoped tables (core/module/butler-specific) must exist only in that butler's schema.
- Shared tables must exist only in `shared`.
- `public` remains present (PostgreSQL default) but must not contain butler-owned runtime tables after cutover.

## 3. Role ownership and ACL model

### 3.1 Roles

- `butlers_migrator` (LOGIN in CI/ops): owns schemas and runs Alembic.
- `butlers_api` (LOGIN): dashboard/API service role.
- Runtime roles (LOGIN), one per butler:
  - `butler_general_app`
  - `butler_relationship_app`
  - `butler_health_app`
  - `butler_switchboard_app`
  - `butler_messenger_app`

### 3.2 Ownership

- Database `butlers` owner: platform DBA/admin account (environment-specific).
- Schemas `shared`, `general`, `relationship`, `health`, `switchboard`, `messenger` owner: `butlers_migrator`.
- Runtime/app roles are non-owners; they receive grants only.

### 3.3 Grants (required baseline)

- Every runtime role:
  - `USAGE` on its own schema and `shared`
  - DML (`SELECT, INSERT, UPDATE, DELETE`) on all tables in its own schema and `shared`
  - default privileges for new tables in its own schema and `shared`
- Every runtime role:
  - no `USAGE` on other butler schemas
  - no table privileges on other butler schemas
- `butlers_api`:
  - `USAGE` on all schemas
  - read access on all butler schemas
  - write access only where API must mutate data (currently `shared` secrets flows and existing dashboard write paths)

## 4. Connection and pool strategy

### 4.1 Butler daemons

Each daemon keeps one pool, but all pools target DB `butlers` and set:

- role-specific credentials (preferred), or equivalent role `SET ROLE` policy
- `search_path=<butler_schema>,shared,public`

`[butler.db]` becomes schema-aware:

- `name = "butlers"` (same for every butler)
- `schema = "<butler_name>"` (for example, `general`)

### 4.2 Dashboard/API

Retain the logical "pool per butler key" API to minimize callsite churn, but each pool connects to DB `butlers` with schema-specific search path:

- pool key `general` -> search_path `general,shared,public`
- pool key `relationship` -> search_path `relationship,shared,public`
- etc.

`DatabaseManager` keeps a dedicated shared-credentials pool targeting `shared` semantics.

### 4.3 Environment/config changes (required)

- Keep existing host/auth inputs: `DATABASE_URL` or `POSTGRES_*`.
- Standardize all butlers to `[butler.db].name = "butlers"`.
- Add/require `[butler.db].schema` for all butlers.
- Deprecate `BUTLER_SHARED_DB_NAME` and `BUTLER_LEGACY_SHARED_DB_NAME` after cutover; replace with fixed `shared` schema in the single DB.

## 5. Alembic and chain ordering constraints

### 5.1 New migration ordering contract

For one-db deployments, migration execution order is:

1. Shared bootstrap chain (creates schemas, roles, grants baseline, `shared` tables)
2. For each butler schema:
   1. `core` chain in that schema context
   2. butler-specific chain (if present)
   3. module chains required by that butler

### 5.2 Technical constraints

- Alembic version tracking must be schema-scoped (no single global `alembic_version` for all schemas).
- Every migration that relies on unqualified table names must run with deterministic search path for the intended schema.
- New migrations must avoid moving existing revision files across chains; use additive revisions only.
- Existing chain discovery rules in `src/butlers/migrations.py` stay linear; no multi-head introduction.

## 6. Phased execution with checkpoints and rollback

### Phase 0: Preparation

Entry criteria:
- Current multi-DB production state is healthy.
- Full backup/snapshot plan is tested.

Actions:
- Inventory all source DBs and table row counts.
- Generate ACL test matrix for runtime roles.

Exit criteria:
- Baseline inventory artifact committed to rollout notes.

Rollback:
- Not applicable (no writes yet).

### Phase 1: Bootstrap target DB/schemas/roles

Entry criteria:
- Phase 0 complete.

Actions:
- Create DB `butlers`.
- Create schemas (`shared`, per-butler).
- Create roles and grants defined in Section 3.
- Apply bootstrap/shared migrations.

Exit criteria:
- Schema/role existence and grant checks pass.
- `shared.butler_secrets` exists.

Rollback:
- Drop newly created schemas/roles in `butlers` (source DBs untouched).

### Phase 2: Data copy + parity validation (offline copy)

Entry criteria:
- Phase 1 complete.

Actions:
- Copy each source DB into its target schema.
- Copy shared credentials/state into `shared` as defined by data mapping.
- Run parity checks from Section 7.

Exit criteria:
- All parity checks pass at 100%.

Rollback:
- Truncate/replace target schemas and re-run copy.
- Keep source DBs as source of truth.

### Phase 3: Shadow runtime verification (no production writes on target)

Entry criteria:
- Phase 2 complete.

Actions:
- Start daemons/API against one-db target in staging/shadow environment.
- Execute smoke tests and ACL denial tests.

Exit criteria:
- Functional smoke suite passes.
- ACL tests confirm cross-butler denial.

Rollback:
- Keep production on legacy topology; fix target issues and repeat Phase 3.

### Phase 4: Production cutover

Entry criteria:
- Phase 3 complete.
- Freeze window approved.

Actions:
- Quiesce legacy writers.
- Run final incremental sync.
- Switch runtime config to one-db schema-aware mode.

Exit criteria:
- Production health checks green.
- No parity drift after cutover snapshot.

Rollback:
- Re-point runtime to legacy DBs using pre-cutover config snapshot.
- Restore writes on legacy DBs.
- Discard failed target writes since freeze point and re-plan.

### Phase 5: Legacy decommission

Entry criteria:
- Cutover stable for compatibility window (Section 8).

Actions:
- Remove legacy multi-DB config branches.
- Archive legacy DB snapshots.

Exit criteria:
- No runtime path references legacy DB names.

Rollback:
- Not planned; requires incident procedure and snapshot restore.

## 7. Data parity and no-data-loss checks (pass/fail)

Every table moved from source DB -> target schema must satisfy all checks:

1. Row count parity: exact equality.
2. Primary-key set parity: exact equality (missing=0, extra=0).
3. Aggregate checksum parity (deterministic hash over PK + updated_at or equivalent mutable marker): exact equality.
4. Null-safety checks for required columns: zero violations.
5. Referential integrity checks: zero orphaned foreign keys.
6. App-level smoke reads/writes for each butler: all pass.

Cutover is blocked on any failed check.

## 8. Backward-compatibility window

- Transitional support window: one release cycle after cutover.
- During this window:
  - legacy DB-name config may still parse but emits deprecation warnings
  - no new features may depend on legacy multi-DB behavior
- End-of-window requirement: remove legacy code paths and env vars.

## 9. Non-goals

- No change to butler identities, manifests, or routing contracts.
- No redesign of module data models beyond schema relocation.
- No cross-butler shared table expansion beyond required shared primitives in this epic.
- No adoption of RLS as part of this migration (schema-level grants only).

## 10. Open questions

1. Should `heartbeat` be migrated as a first-class butler schema in this epic, or deferred until it is roster-managed?
2. Should `butlers_api` be read-only outside `shared`, with writes proxied through butler MCP tools, or keep direct write paths where they already exist?
3. Do we enforce hard `public` schema lockdown (`REVOKE CREATE/USAGE`) in Phase 1, or after all migration scripts are schema-qualified?

These questions must be resolved before implementing `butlers-1003.5` runtime wiring.
