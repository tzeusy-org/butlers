## 1. Core migration: public schema write grants and role membership

- [ ] 1.1 Create `alembic/versions/core/core_065_public_schema_write_grants.py` with `down_revision = "core_064"`
- [ ] 1.2 Implement `upgrade()` that grants targeted INSERT/UPDATE/DELETE on all 21 public tables to all 10 butler runtime roles and `connector_writer`
- [ ] 1.3 Implement `_grant_role_membership()` that grants each runtime role and `connector_writer` to `CURRENT_USER` (enables `SET ROLE`)
- [ ] 1.4 Implement `downgrade()` that revokes the grants and role membership
- [ ] 1.5 All grants use `_execute_best_effort` with `role_name` guard and `undefined_table` exception handling

**Dependencies:** None. This is the foundation for all runtime enforcement.

## 2. Database class: SET ROLE support

- [ ] 2.1 Add `role: str | None = None` parameter to `Database.__init__()` and store as `self.role`
- [ ] 2.2 Add `self._role_verified: bool = False` instance variable
- [ ] 2.3 Implement `_verify_role_exists()` async method that checks `pg_roles` for the configured role
- [ ] 2.4 Implement `_setup_connection()` async method that runs `SET ROLE "role_name"` (asyncpg setup callback signature)
- [ ] 2.5 Modify `connect()` to: (a) open a verification connection to check role existence, (b) set `_role_verified`, (c) pass `setup=self._setup_connection` to `asyncpg.create_pool()` when role is verified, (d) log warning when role is absent
- [ ] 2.6 Preserve SSL retry logic in `connect()` -- the `setup` kwarg must be included in both the initial and retry pool creation kwargs
- [ ] 2.7 `from_env()` remains unchanged -- role is set by the caller, not by env

**Dependencies:** Task 1 (migration must exist so the grants are in place when SET ROLE is used).

## 3. Lifecycle wiring

- [ ] 3.1 In `lifecycle.py` `run_startup()`, after `daemon.db.set_schema(daemon.config.db_schema)`, add: `if daemon.config.db_schema: daemon.db.role = f"butler_{daemon.config.db_schema}_rw"`
- [ ] 3.2 Verify role assignment happens BEFORE `await daemon.db.connect()` (pool creation)
- [ ] 3.3 Confirm `daemon.db is None` guard still works correctly -- role is only set on the non-injected path

**Dependencies:** Task 2 (Database class must accept `role` parameter).

## 4. Connector role enforcement utility

- [ ] 4.1 Create `src/butlers/connectors/db_role.py` with `connector_setup_role()` async function and `verify_connector_role()` helper
- [ ] 4.2 `connector_setup_role()` runs `SET ROLE "connector_writer"` -- used as asyncpg `setup` callback
- [ ] 4.3 `verify_connector_role()` checks `pg_roles` for `connector_writer` existence and returns bool

**Dependencies:** Task 1 (migration creates grants for connector_writer).

## 5. Wire connector pools

- [ ] 5.1 Update `telegram_bot.py` pool creation to pass `setup=connector_setup_role` (with role verification and fallback)
- [ ] 5.2 Update `telegram_user_client.py` pool creation
- [ ] 5.3 Update `whatsapp_user_client.py` pool creation
- [ ] 5.4 Update `owntracks.py` pool creation
- [ ] 5.5 Update `google_drive.py` pool creation
- [ ] 5.6 Update `home_assistant.py` pool creation
- [ ] 5.7 Update `gmail.py` pool creation
- [ ] 5.8 Update `google_calendar.py` pool creation
- [ ] 5.9 Update `discord_user.py` pool creation
- [ ] 5.10 Update `spotify.py` pool creation
- [ ] 5.11 Update `steam.py` pool creation
- [ ] 5.12 Update `cursor_store.py` shared pool creation

**Dependencies:** Task 4 (utility module must exist). Can proceed connector-by-connector.

## 6. Contract tests

- [ ] 6.1 Add `test_database_accepts_role_parameter` to `tests/contracts/test_schema_isolation.py`
- [ ] 6.2 Add `test_database_role_none_by_default` to confirm backward compatibility
- [ ] 6.3 Add `test_database_setup_connection_method_exists` to verify the asyncpg setup callback method

**Dependencies:** Task 2.

## 7. Integration tests

- [ ] 7.1 Add `test_set_role_enforces_own_schema_write` to `tests/config/test_schema_acl_isolation.py`
- [ ] 7.2 Add `test_set_role_blocks_cross_schema_write`
- [ ] 7.3 Add `test_set_role_allows_public_table_writes` -- iterate all 21 tables in the write matrix, INSERT a test row, verify success
- [ ] 7.4 Add `test_set_role_blocks_public_table_not_in_matrix` -- verify INSERT into `public.model_catalog` fails
- [ ] 7.5 Add `test_connector_writer_role_enforcement` -- verify connectors schema write + public ingestion_events write + cross-schema block
- [ ] 7.6 Add `test_role_fallback_when_absent` -- create Database with nonexistent role, verify pool creates successfully with warning
- [ ] 7.7 Add `test_role_reset_on_connection_return` -- acquire, return, acquire, verify role is re-set by setup callback

**Dependencies:** Tasks 1, 2, 3.

## 8. RFC and spec updates

- [ ] 8.1 Amend RFC 0006 section "Staffer Schema Permissions and Cross-Butler Access": change "In v1 this model is advisory" to document SET ROLE enforcement
- [ ] 8.2 Amend RFC 0006 section "Database Connection Scoping": add SET ROLE to the connection setup description
- [ ] 8.3 Add a "Public Schema Write Authorization Matrix" section to RFC 0006 with the 21-table grant matrix
- [ ] 8.4 Sync delta spec `specs/butler-base-spec/spec.md` into `openspec/specs/butler-base-spec/spec.md`
- [ ] 8.5 Create new spec `openspec/specs/database-security/spec.md`

**Dependencies:** Tasks 1-7 (specs should reflect the implemented state).

## 9. Dev environment support

- [ ] 9.1 Update `scripts/init-db.sql` (or equivalent provisioning script) to grant runtime role membership to the `butlers` user: `GRANT butler_{name}_rw TO butlers` for each role
- [ ] 9.2 Update dev setup documentation to note that role membership is required for SET ROLE enforcement
- [ ] 9.3 Verify `docker-compose.yml` PostgreSQL init scripts create roles with proper membership

**Dependencies:** Task 1.

## 10. Quality gates

- [ ] 10.1 `ruff check src/ tests/ roster/ conftest.py --output-format concise` passes
- [ ] 10.2 `ruff format --check src/ tests/ roster/ conftest.py -q` passes
- [ ] 10.3 Unit tests pass: `pytest tests/contracts/ tests/core/test_db_ssl.py -q --tb=short`
- [ ] 10.4 Integration tests pass: `pytest tests/config/test_schema_acl_isolation.py tests/integration/test_schema_isolation.py -q --tb=short`
- [ ] 10.5 Full test suite passes: `pytest tests/ --ignore=tests/e2e -q --maxfail=3 --tb=short`

**Dependencies:** All prior tasks.
