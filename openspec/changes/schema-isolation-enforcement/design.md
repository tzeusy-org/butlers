## Context

Butlers uses a single PostgreSQL database with per-butler schemas. The `core_001_foundation` migration creates runtime roles (`butler_{name}_rw` for each of the 10 butler schemas) and a `connector_writer` role for the `connectors` schema. These roles have:

- Full RW (SELECT, INSERT, UPDATE, DELETE, TRIGGER, REFERENCES) on the butler's own schema
- SELECT-only on all public schema tables
- Explicit REVOKE on all other butler schemas
- Default privileges that propagate these grants to future tables

At runtime, `Database.from_env()` creates pools using the shared `butlers` database user (or whatever `POSTGRES_USER` is set to). The `search_path` server setting provides naming isolation, but not permission isolation. The roles sit unused in `pg_roles`.

## Goals / Non-Goals

**Goals:**
- Enforce schema isolation at the PostgreSQL permission level using `SET ROLE` on every connection acquire
- Add targeted public schema write grants so butlers can write to the specific public tables they need
- Handle the connector_writer role for connector processes
- Maintain backward compatibility in development environments where roles may not exist
- Make the enforcement observable (logging, metrics)

**Non-Goals:**
- Row-level security (RLS) within public tables -- future hardening step
- Per-module fine-grained grants (e.g., only the memory module can write to `memory_catalog`) -- overly complex for v1; all butler roles get the same public write matrix
- Changing the Dashboard API's database access model -- it intentionally uses privileged cross-schema connections
- Creating separate PostgreSQL users for each butler -- `SET ROLE` achieves the same isolation without connection credential management
- Encrypting connections between butlers and PostgreSQL -- orthogonal concern

## Decisions

### 1. SET ROLE via asyncpg `setup` callback (not separate users)

**Decision:** Use asyncpg's `setup` callback on `create_pool()` to execute `SET ROLE "butler_{name}_rw"` on every connection acquired from the pool. asyncpg automatically executes `RESET ALL` when connections are returned to the pool, which resets the role.

**Rationale:** `SET ROLE` requires only that the connecting user is a member of (or can assume) the target role. This avoids creating and managing separate database credentials for each butler. The `setup` callback runs on every `pool.acquire()`, ensuring no connection is ever used without the role set. `RESET ALL` on return is asyncpg's built-in behavior and handles cleanup without explicit teardown.

**Alternative considered:** Creating separate PostgreSQL users per butler. Rejected because it requires credential management, connection string per butler, and complicates development environments.

### 2. Uniform public write grants for all butler roles

**Decision:** All 10 butler runtime roles receive identical write grants on the 21 public tables that need writes. No per-butler customization.

**Rationale:** The current architecture has modules loaded onto different butlers, but any module could theoretically be added to any butler via `butler.toml`. Granting per-module permissions would create a coupling between the migration layer and butler configuration that doesn't exist today. The write authorization matrix below covers all current write paths. If a public table should truly be restricted to one butler, that's better expressed via RLS in a future hardening step.

### 3. Graceful fallback when role is absent

**Decision:** If the target role does not exist in `pg_roles` when the pool is created, log a warning and skip `SET ROLE`. The butler operates with the same privileges as today (shared user).

**Rationale:** Development environments may not have the migration user's ability to CREATE ROLE. The `core_001_foundation` migration already uses `_create_runtime_role_best_effort()` which silently skips role creation when the user lacks CREATEROLE privilege. Enforcing `SET ROLE` in that environment would break development. The fallback preserves current behavior while production environments (which have the roles) get enforcement.

### 4. connector_writer gets matching public write grants

**Decision:** The `connector_writer` role gets the same public table write grants as butler roles, plus its existing connectors schema access.

**Rationale:** Connectors write to `public.ingestion_events` and `public.entities`/`public.entity_info` (for identity resolution during ingestion). They also read from `public.contacts`, `public.contact_info`, and `public.google_accounts`. The uniform grant matrix covers these paths. Connector processes will use `SET ROLE connector_writer` in their pool setup callbacks.

### 5. Dashboard API pools are NOT affected

**Decision:** The `DatabaseManager` in `src/butlers/api/db.py` continues to use the privileged `butlers` database user without `SET ROLE`.

**Rationale:** The dashboard intentionally needs cross-schema access for fan-out queries, aggregate views, and the credential shared pool. It runs as a trusted server process, not as an LLM-driven agent.

## Public Schema Write Authorization Matrix

This matrix was derived by auditing all `INSERT`, `UPDATE`, and `DELETE` operations on `public.*` tables across the codebase. Every entry is backed by actual code paths in modules, connectors, or core infrastructure.

| Public Table | Granted Operations | Used By |
|---|---|---|
| `public.entities` | INSERT, UPDATE, DELETE | identity module, bootstrap, memory, contacts |
| `public.contacts` | INSERT, UPDATE | identity module, contacts module |
| `public.contact_info` | INSERT, UPDATE, DELETE | identity module, contacts, relationship |
| `public.entity_info` | INSERT, DELETE | google/steam credentials, entity management |
| `public.google_accounts` | INSERT, UPDATE | google account registry, calendar, drive |
| `public.steam_accounts` | INSERT, UPDATE, DELETE | steam account registry |
| `public.user_context` | INSERT, UPDATE | context bus (RFC 0009) |
| `public.model_round_robin_counters` | INSERT | model routing round-robin |
| `public.token_usage_ledger` | INSERT | model routing token tracking |
| `public.ingestion_events` | INSERT, UPDATE, DELETE | ingestion pipeline, switchboard, owntracks retention |
| `public.healing_attempts` | INSERT, UPDATE | QA/healing module |
| `public.qa_dismissals` | INSERT, DELETE | QA module |
| `public.qa_findings` | INSERT, UPDATE | QA module |
| `public.qa_repo_config` | UPDATE | QA module |
| `public.qa_patrols` | INSERT, UPDATE | QA module |
| `public.memory_catalog` | INSERT | memory module |
| `public.facts` | INSERT, UPDATE | finance anomaly detection (ON CONFLICT DO UPDATE) |
| `public.insight_candidates` | INSERT, UPDATE, DELETE | insight broker (switchboard tools) |
| `public.insight_cooldowns` | INSERT, DELETE | insight broker cooldown tracking |
| `public.insight_engagement` | INSERT, UPDATE, DELETE | insight engagement tracking |
| `public.insight_settings` | INSERT, UPDATE | insight delivery settings |

**Tables NOT in the write matrix** (read-only for butler roles):
- `public.model_catalog` -- managed by migrations/dashboard, read at runtime
- `public.token_limits` -- managed by dashboard, read at runtime

## Implementation Details

### 1. Migration: `core_065_public_schema_write_grants.py`

```python
"""public schema targeted write grants for SET ROLE enforcement

Revision ID: core_065
Revises: core_064
Create Date: 2026-04-09 00:00:00.000000

Grants targeted INSERT/UPDATE/DELETE on specific public tables to all
butler runtime roles and the connector_writer role, enabling SET ROLE
enforcement without breaking public table writes.
"""

from __future__ import annotations
from alembic import op

revision = "core_065"
down_revision = "core_064"
branch_labels = None
depends_on = None

_ROLE_SCHEMAS = (
    "education", "finance", "general", "health", "home",
    "lifestyle", "messenger", "relationship", "switchboard", "travel",
)
_RUNTIME_ROLES = [f"butler_{schema}_rw" for schema in _ROLE_SCHEMAS]
_CONNECTOR_ROLE = "connector_writer"
_ALL_ROLES = [*_RUNTIME_ROLES, _CONNECTOR_ROLE]

# (table_name, granted_operations)
_PUBLIC_WRITE_GRANTS = [
    ("entities",                   "INSERT, UPDATE, DELETE"),
    ("contacts",                   "INSERT, UPDATE"),
    ("contact_info",               "INSERT, UPDATE, DELETE"),
    ("entity_info",                "INSERT, DELETE"),
    ("google_accounts",            "INSERT, UPDATE"),
    ("steam_accounts",             "INSERT, UPDATE, DELETE"),
    ("user_context",               "INSERT, UPDATE"),
    ("model_round_robin_counters", "INSERT"),
    ("token_usage_ledger",         "INSERT"),
    ("ingestion_events",           "INSERT, UPDATE, DELETE"),
    ("healing_attempts",           "INSERT, UPDATE"),
    ("qa_dismissals",              "INSERT, DELETE"),
    ("qa_findings",                "INSERT, UPDATE"),
    ("qa_repo_config",             "UPDATE"),
    ("qa_patrols",                 "INSERT, UPDATE"),
    ("memory_catalog",             "INSERT"),
    ("facts",                      "INSERT, UPDATE"),
    ("insight_candidates",         "INSERT, UPDATE, DELETE"),
    ("insight_cooldowns",          "INSERT, DELETE"),
    ("insight_engagement",         "INSERT, UPDATE, DELETE"),
    ("insight_settings",           "INSERT, UPDATE"),
]


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str, *, role_name: str | None = None) -> None:
    condition = "TRUE"
    if role_name is not None:
        condition = (
            f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)})"
        )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                EXECUTE {_quote_literal(statement)};
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
        END
        $$;
        """
    )


def _grant_role_membership() -> None:
    """Grant SET ROLE capability: connecting user must be member of each role."""
    for role_name in _ALL_ROLES:
        quoted_role = _quote_ident(role_name)
        _execute_best_effort(
            f"GRANT {quoted_role} TO CURRENT_USER",
            role_name=role_name,
        )


def upgrade() -> None:
    # Step 1: Grant public table write permissions
    for role_name in _ALL_ROLES:
        for table_name, operations in _PUBLIC_WRITE_GRANTS:
            quoted_role = _quote_ident(role_name)
            quoted_table = f"public.{_quote_ident(table_name)}"
            _execute_best_effort(
                f"GRANT {operations} ON {quoted_table} TO {quoted_role}",
                role_name=role_name,
            )
    # Step 2: Grant role membership so SET ROLE works
    _grant_role_membership()


def downgrade() -> None:
    # Step 1: Revoke role membership
    for role_name in _ALL_ROLES:
        quoted_role = _quote_ident(role_name)
        _execute_best_effort(
            f"REVOKE {quoted_role} FROM CURRENT_USER",
            role_name=role_name,
        )
    # Step 2: Revoke public table write permissions
    for role_name in _ALL_ROLES:
        for table_name, operations in _PUBLIC_WRITE_GRANTS:
            quoted_role = _quote_ident(role_name)
            quoted_table = f"public.{_quote_ident(table_name)}"
            _execute_best_effort(
                f"REVOKE {operations} ON {quoted_table} FROM {quoted_role}",
                role_name=role_name,
            )
```

**Key design choices in the migration:**
- Uses `_execute_best_effort` (same pattern as `core_001`) to tolerate missing roles and missing tables
- Adds `WHEN undefined_table` exception handler because some public tables are created by module migrations that may not have run yet
- Does NOT use `ALTER DEFAULT PRIVILEGES` for writes -- grants are table-specific, not blanket
- Does NOT grant `DELETE` on tables that only need INSERT/UPDATE (principle of least privilege)

### 2. Database class changes: `src/butlers/db.py`

```python
class Database:
    def __init__(
        self,
        db_name: str,
        schema: str | None = None,
        role: str | None = None,        # NEW
        host: str = "localhost",
        port: int = 5432,
        user: str = "postgres",
        password: str = "postgres",
        ssl: str | None = None,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
    ) -> None:
        self.db_name = db_name
        self.schema = _normalize_schema_name(schema)
        self.role = role                  # NEW
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.ssl = ssl
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        self.pool: asyncpg.Pool | None = None
        self._role_verified: bool = False  # NEW: tracks if role exists

    # NEW METHOD
    async def _verify_role_exists(self, conn: asyncpg.Connection) -> bool:
        """Check if the configured role exists in pg_roles."""
        if self.role is None:
            return False
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)",
            self.role,
        )
        return bool(exists)

    # NEW METHOD
    async def _setup_connection(self, conn: asyncpg.Connection) -> None:
        """asyncpg setup callback: SET ROLE on every connection acquire."""
        if not self._role_verified:
            return
        quoted_role = '"' + self.role.replace('"', '""') + '"'
        await conn.execute(f"SET ROLE {quoted_role}")

    async def connect(self) -> asyncpg.Pool:
        """Create and return a connection pool to the butler's database."""
        pool_kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.db_name,
            "min_size": self.min_pool_size,
            "max_size": self.max_pool_size,
        }
        server_settings = self._server_settings()
        if server_settings is not None:
            pool_kwargs["server_settings"] = server_settings
        if self.ssl is not None:
            pool_kwargs["ssl"] = self.ssl

        # Verify role existence before pool creation
        if self.role is not None:
            try:
                check_conn = await asyncpg.connect(
                    host=self.host, port=self.port, user=self.user,
                    password=self.password, database=self.db_name,
                    ssl=self.ssl,
                )
                try:
                    self._role_verified = await self._verify_role_exists(check_conn)
                finally:
                    await check_conn.close()
            except Exception:
                logger.warning(
                    "Could not verify role %r existence; "
                    "SET ROLE enforcement disabled for %s",
                    self.role, self.db_name,
                )
                self._role_verified = False

            if self._role_verified:
                pool_kwargs["setup"] = self._setup_connection
                logger.info(
                    "SET ROLE enforcement enabled: %s (schema=%s)",
                    self.role, self.schema,
                )
            else:
                logger.warning(
                    "Role %r not found; SET ROLE enforcement disabled. "
                    "Butler %s runs with shared-user privileges.",
                    self.role, self.db_name,
                )

        try:
            self.pool = await asyncpg.create_pool(**pool_kwargs)
        except Exception as exc:
            if not should_retry_with_ssl_disable(exc, self.ssl):
                raise
            retry_kwargs = dict(pool_kwargs)
            retry_kwargs["ssl"] = "disable"
            logger.info(
                "Retrying PostgreSQL pool creation with ssl=disable "
                "after SSL upgrade loss"
            )
            self.pool = await asyncpg.create_pool(**retry_kwargs)

        logger.info("Connection pool created for: %s", self.db_name)
        return self.pool
```

**Key implementation details:**

- `role` parameter is optional; `None` means no `SET ROLE` (backward compatible)
- Role verification happens once during `connect()`, not on every acquire -- avoids per-acquire latency
- `_role_verified` flag gates `_setup_connection()` behavior -- if role is absent, the callback is never registered
- The verification connection is opened and closed before pool creation; it does not consume a pool slot
- asyncpg's `setup` callback signature is `async def setup(conn: asyncpg.Connection) -> None` and runs on every `pool.acquire()`
- asyncpg automatically runs `RESET ALL` when connections return to the pool, which resets the role to the connecting user

### 3. Lifecycle wiring: `src/butlers/lifecycle.py`

In `run_startup()`, after the existing `Database.from_env()` / `set_schema()` calls:

```python
# Current code:
daemon.db = Database.from_env(daemon.config.db_name)
daemon.db.set_schema(daemon.config.db_schema)

# New code:
daemon.db = Database.from_env(daemon.config.db_name)
daemon.db.set_schema(daemon.config.db_schema)
if daemon.config.db_schema:
    daemon.db.role = f"butler_{daemon.config.db_schema}_rw"
```

The role is derived from the schema name using the same convention as `core_001_foundation`:
```python
_RUNTIME_ROLES = {schema: f"butler_{schema}_rw" for schema in _ROLE_SCHEMAS}
```

This wiring happens BEFORE `await daemon.db.connect()`, so the role is set when the pool is created.

### 4. Connector role enforcement

Connectors create their own `asyncpg.create_pool()` calls with hardcoded parameters. Each connector needs:

1. A `setup` callback function that runs `SET ROLE "connector_writer"`
2. A role verification check at startup

The pattern for each connector:

```python
# Utility function (could live in src/butlers/connectors/db_utils.py)
async def connector_setup_role(conn: asyncpg.Connection) -> None:
    """SET ROLE connector_writer on every connection acquire."""
    await conn.execute('SET ROLE "connector_writer"')

async def verify_connector_role(pool: asyncpg.Pool) -> bool:
    """Check if connector_writer role exists."""
    async with pool.acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'connector_writer')"
        ))

# In each connector's pool creation:
pool = await asyncpg.create_pool(
    ...,
    setup=connector_setup_role,  # NEW
)
```

**Affected connectors** (those that create their own asyncpg pools):
- `src/butlers/connectors/telegram_bot.py` (line ~1154)
- `src/butlers/connectors/telegram_user_client.py` (line ~1919)
- `src/butlers/connectors/whatsapp_user_client.py` (line ~1482)
- `src/butlers/connectors/owntracks.py` (line ~1733)
- `src/butlers/connectors/google_drive.py` (line ~2264)
- `src/butlers/connectors/home_assistant.py` (line ~1321)
- `src/butlers/connectors/gmail.py` (has pool creation)
- `src/butlers/connectors/google_calendar.py` (credential + cursor pools)
- `src/butlers/connectors/spotify.py` (has pool creation)
- `src/butlers/connectors/discord_user.py` (has pool creation)
- `src/butlers/connectors/steam.py` (has pool creation)
- `src/butlers/connectors/cursor_store.py` (shared cursor pool factory)

### 5. Migrations: Role membership grant

The connecting user (`butlers` by default) must be able to assume each runtime role via `SET ROLE`. This requires the connecting user to be a member of each role. The `core_001_foundation` migration creates the roles with `CREATE ROLE ... LOGIN` but does not grant membership to the connecting user. A new section in `core_065` handles this:

```python
def _grant_role_membership() -> None:
    """Grant SET ROLE capability: connecting user must be member of each role."""
    for role_name in _ALL_ROLES:
        quoted_role = _quote_ident(role_name)
        _execute_best_effort(
            f"GRANT {quoted_role} TO CURRENT_USER",
            role_name=role_name,
        )
```

This uses `CURRENT_USER` so it works regardless of what the migration user is named.

### 6. `from_env()` class method update

```python
@classmethod
def from_env(cls, db_name: str) -> Database:
    params = db_params_from_env()
    return cls(
        db_name=db_name,
        host=str(params["host"]),
        port=int(params["port"]),
        user=str(params["user"]),
        password=str(params["password"]),
        ssl=params["ssl"] if isinstance(params["ssl"], str) else None,
    )
```

`from_env()` does NOT set the role -- that's done by the caller (lifecycle.py) because the role depends on the butler's schema, which is set via `set_schema()` after construction. This preserves the existing two-step pattern.

## Risks / Trade-offs

- **[Role verification latency]** An extra connection is opened during `connect()` to verify role existence. This adds ~10-50ms to startup. Acceptable because `connect()` runs once per daemon lifetime. Alternative: catch the `SET ROLE` error on first acquire. Rejected because error-path control flow is harder to reason about and would leave the first connection in an ambiguous state.

- **[Dev environment friction]** Developers without CREATEROLE on their PostgreSQL user will see warning logs but no behavioral change. This is intentional -- enforcement is opt-in based on role availability. The `scripts/init-db.sql` provisioning script should be updated to grant role membership.

- **[Future public tables]** New public tables added by future migrations need corresponding GRANT entries. Without them, `SET ROLE` connections will fail on INSERT/UPDATE/DELETE. Mitigation: the migration pattern and authorization matrix are documented. A contract test (see below) validates that all public table writes succeed under SET ROLE.

- **[Connector pool diversity]** Each connector creates pools differently (some with SSL retry, some without). Adding `setup` callbacks must be done per-connector. A utility module reduces duplication but cannot eliminate per-connector changes. Future: connectors should use a shared pool factory.

## Test Plan

### Contract tests (no DB required)

1. **`test_database_accepts_role_parameter`**: `Database.__init__` signature includes `role` parameter; `Database("test", role="butler_test_rw").role == "butler_test_rw"`.

2. **`test_database_role_none_by_default`**: `Database("test").role is None` -- backward compatible default.

3. **`test_lifecycle_derives_role_from_schema`**: Verify that `lifecycle.run_startup()` sets `daemon.db.role = f"butler_{schema}_rw"` when `db_schema` is set. (Unit test with mocked daemon.)

### Integration tests (require PostgreSQL with roles)

4. **`test_set_role_enforces_own_schema_write`**: Create pool with `SET ROLE butler_general_rw`, verify INSERT into `general.state` succeeds.

5. **`test_set_role_blocks_cross_schema_write`**: Under `SET ROLE butler_general_rw`, verify INSERT into `health.state` raises `ProgrammingError("permission denied")`.

6. **`test_set_role_allows_public_table_writes`**: Under `SET ROLE butler_general_rw`, verify INSERT into each of the 21 public write-grant tables succeeds.

7. **`test_set_role_blocks_public_table_not_in_matrix`**: Under `SET ROLE butler_general_rw`, verify INSERT into `public.model_catalog` raises `ProgrammingError("permission denied")`.

8. **`test_connector_writer_role_enforcement`**: Under `SET ROLE connector_writer`, verify INSERT into `connectors.*` tables and `public.ingestion_events` succeed, but INSERT into `general.state` fails.

9. **`test_role_fallback_when_absent`**: Create a Database with a non-existent role, call `connect()`, verify pool is created (with warning log), and queries still work (as the connecting user).

10. **`test_role_reset_on_connection_return`**: Acquire connection from SET ROLE pool, return it, acquire again, verify the setup callback re-sets the role (confirming asyncpg RESET ALL + setup cycle works).
