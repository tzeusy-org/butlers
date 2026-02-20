"""apply per-schema ACL for butler runtime roles

Revision ID: core_011
Revises: core_010
Create Date: 2026-02-20 01:20:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_011"
down_revision = "core_010"
branch_labels = None
depends_on = None

_BUTLER_SCHEMAS = ("general", "health", "messenger", "relationship", "switchboard")
_SHARED_SCHEMA = "shared"
_ALL_SCHEMAS = (_SHARED_SCHEMA, *_BUTLER_SCHEMAS)
_RUNTIME_ROLES = {schema: f"butler_{schema}_rw" for schema in _BUTLER_SCHEMAS}

_OWN_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, TRIGGER, REFERENCES"
_OWN_SEQUENCE_PRIVILEGES = "USAGE, SELECT, UPDATE"
_SHARED_TABLE_PRIVILEGES = "SELECT"
_SHARED_SEQUENCE_PRIVILEGES = "USAGE, SELECT"


def _quote_ident(identifier: str) -> str:
    """Return a safely quoted SQL identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    """Return a safely quoted SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def _current_database_name() -> str:
    """Return the current database name for this migration connection."""
    bind = op.get_bind()
    current_db = bind.exec_driver_sql("SELECT current_database()").scalar_one()
    assert isinstance(current_db, str)
    return current_db


def _execute_best_effort(statement: str, *, role_name: str | None = None) -> None:
    """Execute SQL, ignoring missing-role and privilege errors.

    When ``role_name`` is set, execute only when that role exists.
    """
    condition = "TRUE"
    if role_name is not None:
        condition = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)})"

    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                EXECUTE {_quote_literal(statement)};
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN
                NULL;
            WHEN undefined_object THEN
                NULL;
        END
        $$;
        """
    )


def _create_runtime_role_best_effort(role_name: str) -> None:
    """Create runtime role as LOGIN if it does not already exist."""
    quoted_role = _quote_ident(role_name)
    role_lit = _quote_literal(role_name)

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {role_lit}) THEN
                EXECUTE 'CREATE ROLE {quoted_role} LOGIN';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN
                NULL;
        END
        $$;
        """
    )


def _apply_public_baseline_revokes(current_db: str) -> None:
    """Revoke permissive PUBLIC grants at database/schema/object levels."""
    quoted_db = _quote_ident(current_db)

    _execute_best_effort(f"REVOKE ALL ON DATABASE {quoted_db} FROM PUBLIC")
    _execute_best_effort("REVOKE ALL ON SCHEMA public FROM PUBLIC")

    for schema in _ALL_SCHEMAS:
        quoted_schema = _quote_ident(schema)
        _execute_best_effort(f"REVOKE ALL ON SCHEMA {quoted_schema} FROM PUBLIC")
        _execute_best_effort(f"REVOKE ALL ON ALL TABLES IN SCHEMA {quoted_schema} FROM PUBLIC")
        _execute_best_effort(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {quoted_schema} FROM PUBLIC")
        _execute_best_effort(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {quoted_schema} FROM PUBLIC")
        _execute_best_effort(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} REVOKE ALL ON TABLES FROM PUBLIC"
        )
        _execute_best_effort(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
            "REVOKE ALL ON SEQUENCES FROM PUBLIC"
        )
        _execute_best_effort(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
            "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
        )


def _grant_connect_and_search_path(current_db: str, role_name: str, own_schema: str) -> None:
    """Grant database connect and pin role search_path to own+shared schemas."""
    quoted_db = _quote_ident(current_db)
    quoted_role = _quote_ident(role_name)
    quoted_own = _quote_ident(own_schema)
    quoted_shared = _quote_ident(_SHARED_SCHEMA)

    _execute_best_effort(
        f"GRANT CONNECT ON DATABASE {quoted_db} TO {quoted_role}", role_name=role_name
    )
    _execute_best_effort(
        (
            f"ALTER ROLE {quoted_role} IN DATABASE {quoted_db} "
            f"SET search_path = {quoted_own}, {quoted_shared}, public"
        ),
        role_name=role_name,
    )


def _grant_own_schema_privileges(schema: str, role_name: str) -> None:
    """Grant runtime role read/write privileges in its own schema."""
    quoted_schema = _quote_ident(schema)
    quoted_role = _quote_ident(role_name)

    _execute_best_effort(
        f"GRANT USAGE, CREATE ON SCHEMA {quoted_schema} TO {quoted_role}", role_name=role_name
    )
    _execute_best_effort(
        f"GRANT {_OWN_TABLE_PRIVILEGES} ON ALL TABLES IN SCHEMA {quoted_schema} TO {quoted_role}",
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"GRANT {_OWN_SEQUENCE_PRIVILEGES} "
            f"ON ALL SEQUENCES IN SCHEMA {quoted_schema} TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {quoted_schema} TO {quoted_role}",
        role_name=role_name,
    )


def _grant_shared_schema_read_privileges(role_name: str) -> None:
    """Grant runtime role read-only access to shared schema objects."""
    quoted_schema = _quote_ident(_SHARED_SCHEMA)
    quoted_role = _quote_ident(role_name)

    _execute_best_effort(
        f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}",
        role_name=role_name,
    )
    _execute_best_effort(
        f"REVOKE CREATE ON SCHEMA {quoted_schema} FROM {quoted_role}", role_name=role_name
    )
    _execute_best_effort(
        (
            f"GRANT {_SHARED_TABLE_PRIVILEGES} "
            f"ON ALL TABLES IN SCHEMA {quoted_schema} TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"GRANT {_SHARED_SEQUENCE_PRIVILEGES} "
            f"ON ALL SEQUENCES IN SCHEMA {quoted_schema} TO {quoted_role}"
        ),
        role_name=role_name,
    )


def _revoke_cross_schema_privileges(own_schema: str, role_name: str) -> None:
    """Ensure runtime role has no privileges on non-owned butler schemas."""
    quoted_role = _quote_ident(role_name)

    for other_schema in _BUTLER_SCHEMAS:
        if other_schema == own_schema:
            continue

        quoted_schema = _quote_ident(other_schema)
        _execute_best_effort(
            f"REVOKE ALL ON SCHEMA {quoted_schema} FROM {quoted_role}", role_name=role_name
        )
        _execute_best_effort(
            f"REVOKE ALL ON ALL TABLES IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )


def _apply_default_privileges(own_schema: str, role_name: str) -> None:
    """Set default privileges for future objects across own/shared/other schemas."""
    quoted_role = _quote_ident(role_name)
    quoted_own_schema = _quote_ident(own_schema)
    quoted_shared_schema = _quote_ident(_SHARED_SCHEMA)

    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_own_schema} "
            f"GRANT {_OWN_TABLE_PRIVILEGES} ON TABLES TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_own_schema} "
            f"GRANT {_OWN_SEQUENCE_PRIVILEGES} ON SEQUENCES TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_own_schema} "
            f"GRANT EXECUTE ON FUNCTIONS TO {quoted_role}"
        ),
        role_name=role_name,
    )

    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_shared_schema} "
            f"GRANT {_SHARED_TABLE_PRIVILEGES} ON TABLES TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_shared_schema} "
            f"GRANT {_SHARED_SEQUENCE_PRIVILEGES} ON SEQUENCES TO {quoted_role}"
        ),
        role_name=role_name,
    )
    _execute_best_effort(
        (
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_shared_schema} "
            f"REVOKE EXECUTE ON FUNCTIONS FROM {quoted_role}"
        ),
        role_name=role_name,
    )

    for other_schema in _BUTLER_SCHEMAS:
        if other_schema == own_schema:
            continue
        quoted_other_schema = _quote_ident(other_schema)
        _execute_best_effort(
            (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_other_schema} "
                f"REVOKE ALL ON TABLES FROM {quoted_role}"
            ),
            role_name=role_name,
        )
        _execute_best_effort(
            (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_other_schema} "
                f"REVOKE ALL ON SEQUENCES FROM {quoted_role}"
            ),
            role_name=role_name,
        )
        _execute_best_effort(
            (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_other_schema} "
                f"REVOKE EXECUTE ON FUNCTIONS FROM {quoted_role}"
            ),
            role_name=role_name,
        )


def _revoke_role_access(current_db: str, role_name: str) -> None:
    """Best-effort ACL cleanup for downgrade."""
    quoted_db = _quote_ident(current_db)
    quoted_role = _quote_ident(role_name)

    _execute_best_effort(
        f"REVOKE CONNECT ON DATABASE {quoted_db} FROM {quoted_role}", role_name=role_name
    )
    _execute_best_effort(
        f"ALTER ROLE {quoted_role} IN DATABASE {quoted_db} RESET search_path", role_name=role_name
    )
    _execute_best_effort(f"REVOKE ALL ON SCHEMA public FROM {quoted_role}", role_name=role_name)

    for schema in _ALL_SCHEMAS:
        quoted_schema = _quote_ident(schema)
        _execute_best_effort(
            f"REVOKE ALL ON SCHEMA {quoted_schema} FROM {quoted_role}", role_name=role_name
        )
        _execute_best_effort(
            f"REVOKE ALL ON ALL TABLES IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {quoted_schema} FROM {quoted_role}",
            role_name=role_name,
        )
        _execute_best_effort(
            (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
                f"REVOKE ALL ON TABLES FROM {quoted_role}"
            ),
            role_name=role_name,
        )
        _execute_best_effort(
            (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
                f"REVOKE ALL ON SEQUENCES FROM {quoted_role}"
            ),
            role_name=role_name,
        )
        _execute_best_effort(
            (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
                f"REVOKE EXECUTE ON FUNCTIONS FROM {quoted_role}"
            ),
            role_name=role_name,
        )


def upgrade() -> None:
    current_db = _current_database_name()
    _apply_public_baseline_revokes(current_db)

    for role_name in _RUNTIME_ROLES.values():
        _create_runtime_role_best_effort(role_name)

    for own_schema, role_name in _RUNTIME_ROLES.items():
        _grant_connect_and_search_path(current_db, role_name, own_schema)
        _grant_own_schema_privileges(own_schema, role_name)
        _grant_shared_schema_read_privileges(role_name)
        _revoke_cross_schema_privileges(own_schema, role_name)
        _apply_default_privileges(own_schema, role_name)


def downgrade() -> None:
    current_db = _current_database_name()
    for role_name in _RUNTIME_ROLES.values():
        _revoke_role_access(current_db, role_name)
