"""Read-only admission for the reviewed PostgreSQL migration prerequisite.

This verifies the *current* bootstrap-managed catalog, not script execution
history. It neither provisions nor repairs anything. Applied protected revisions
still make their own point-of-use checks. No result is cached across connections.
"""

from __future__ import annotations

import importlib.util
import json
import re
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

_BUTLERS = (
    "chronicler",
    "concierge",
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "qa",
    "relationship",
    "switchboard",
    "travel",
)
_ADMINS = (
    ("restore_drill_executor_admin", "core_196_restore_drill_executor_boundary.py"),
    ("dnd_generation_admin", "core_197_canonical_dnd_generation_guard.py"),
    ("runtime_attention_admin", "core_198_runtime_attention_outbox.py"),
)


class BootstrapPrerequisiteError(RuntimeError):
    """A fixed, non-sensitive prerequisite diagnostic (never a raw DB error)."""

    def __init__(self, category: str) -> None:
        super().__init__(
            f"Reviewed database bootstrap prerequisite {category}; run scripts/init-db.sql "
            "using the distinct privileged bootstrap identity before online migrations."
        )


def _true(connection: Connection, sql: str, params: dict | None = None) -> bool:
    # Strict bool: unknown/NULL and a misleading nonboolean value never attest.
    value = connection.execute(text(sql), params or {}).scalar_one()
    if type(value) is not bool:
        raise BootstrapPrerequisiteError("unreadable")
    return value


_ADMIN_PROVENANCE = """
SELECT EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace AS n
    JOIN pg_catalog.pg_roles AS owner ON owner.oid = n.nspowner AND owner.rolsuper
    JOIN pg_catalog.pg_class AS config ON config.relnamespace = n.oid
        AND config.relname = 'bootstrap_configuration' AND config.relkind = 'r'
        AND config.relpersistence = 'p' AND config.relowner = owner.oid
    WHERE n.nspname = :admin
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(n.nspacl,
          pg_catalog.acldefault('n', n.nspowner))) AS acl WHERE acl.grantee = 0)
      AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.aclexplode(COALESCE(config.relacl,
              pg_catalog.acldefault('r', config.relowner))) AS acl
          WHERE acl.grantee <> owner.oid)
      AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute AS a
          WHERE a.attrelid = config.oid AND a.attname = 'singleton' AND a.attnotnull
            AND a.attnum > 0 AND NOT a.attisdropped
            AND a.atttypid = 'pg_catalog.bool'::pg_catalog.regtype
            AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint AS pk
                WHERE pk.conrelid = config.oid AND pk.contype = 'p'
                  AND pk.conkey = ARRAY[a.attnum]::pg_catalog.int2[])
            AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint AS check_constraint
                WHERE check_constraint.conrelid = config.oid
                  AND check_constraint.contype = 'c' AND check_constraint.convalidated
                  AND pg_catalog.pg_get_expr(check_constraint.conbin, config.oid) = 'singleton'))
      AND NOT EXISTS (
          SELECT 1 FROM (VALUES ('migration_role'), ('bootstrap_role')) AS required(name)
          WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute AS a
              WHERE a.attrelid = config.oid AND a.attname = required.name
                AND a.attnum > 0 AND NOT a.attisdropped AND a.attnotnull
                AND a.atttypid = 'pg_catalog.name'::pg_catalog.regtype))
      AND NOT EXISTS (
          SELECT 1 FROM (VALUES ('install_interface'), ('finalize_interface')) AS required(name)
          WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc AS p
              WHERE p.pronamespace = n.oid AND p.proname = required.name
                AND p.pronargs = 0 AND p.prokind = 'f'
                AND p.prorettype = 'pg_catalog.void'::pg_catalog.regtype
                AND p.proowner = owner.oid AND p.prosecdef
                AND p.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::pg_catalog.text[])))
"""

_BASE_PROFILE = """
WITH subject AS (SELECT * FROM pg_catalog.pg_roles WHERE rolname = :migration_role),
required AS (SELECT * FROM pg_catalog.jsonb_to_recordset(CAST(:roles AS pg_catalog.jsonb))
    AS r(role pg_catalog.text, schema pg_catalog.text))
SELECT EXISTS (SELECT 1 FROM subject
    WHERE NOT rolsuper AND NOT rolcreaterole AND NOT rolreplication AND NOT rolcreatedb)
AND NOT EXISTS (
    SELECT 1 FROM (VALUES ('vector'), ('pgcrypto'), ('uuid-ossp'), ('pg_trgm')) AS e(name)
    WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = e.name))
AND NOT EXISTS (
    SELECT 1 FROM required AS required
    LEFT JOIN pg_catalog.pg_roles AS runtime ON runtime.rolname = required.role
    LEFT JOIN pg_catalog.pg_namespace AS n ON n.nspname = required.schema
    WHERE runtime.oid IS NULL OR n.oid IS NULL OR runtime.rolsuper
       OR runtime.rolcreaterole OR runtime.rolreplication OR runtime.rolcreatedb
       OR NOT pg_catalog.pg_has_role(CAST(:migration_role AS pg_catalog.name), runtime.oid, 'SET')
       OR NOT pg_catalog.pg_has_role(CAST(:migration_role AS pg_catalog.name), runtime.oid, 'USAGE')
       OR NOT pg_catalog.has_schema_privilege(
           CAST(:migration_role AS pg_catalog.name), n.oid, 'USAGE')
       OR NOT pg_catalog.has_schema_privilege(
           CAST(:migration_role AS pg_catalog.name), n.oid, 'CREATE')
       OR NOT pg_catalog.has_schema_privilege(runtime.oid, n.oid, 'USAGE')
       OR NOT pg_catalog.has_schema_privilege(runtime.oid, n.oid, 'CREATE')
       OR NOT pg_catalog.has_schema_privilege(runtime.oid, 'public', 'USAGE')
       OR NOT pg_catalog.has_schema_privilege(runtime.oid, 'connectors', 'USAGE')
       OR NOT pg_catalog.has_database_privilege(
           runtime.oid, pg_catalog.current_database(), 'CONNECT'))
AND pg_catalog.has_schema_privilege(CAST(:migration_role AS pg_catalog.name), 'public', 'USAGE')
AND pg_catalog.has_schema_privilege(CAST(:migration_role AS pg_catalog.name), 'public', 'CREATE')
AND NOT EXISTS (
    SELECT 1 FROM (VALUES ('restore_drill_executor'), ('restore_drill_executor_owner'),
        ('restore_drill_executor_audit_writer')) AS required(name)
    LEFT JOIN pg_catalog.pg_roles AS reserved ON reserved.rolname = required.name
    WHERE reserved.oid IS NULL OR reserved.rolsuper OR reserved.rolcreaterole
       OR reserved.rolreplication OR reserved.rolinherit
       OR (required.name <> 'restore_drill_executor'
           AND (reserved.rolcanlogin OR reserved.rolcreatedb))
       OR pg_catalog.pg_has_role(CAST(:migration_role AS pg_catalog.name), reserved.oid, 'MEMBER'))
AND EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'dashboard_auth_api'
    AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolcreaterole
    AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls)
AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'butler_calendar_rw'
    AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication))
"""

_DEFAULT_ACLS = """
WITH required AS (
    SELECT * FROM pg_catalog.jsonb_to_recordset(CAST(:defaults AS pg_catalog.jsonb))
      AS d(role pg_catalog.text, schema pg_catalog.text, kind pg_catalog.text,
           privilege pg_catalog.text))
SELECT NOT EXISTS (
    SELECT 1 FROM required
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_default_acl AS defaults
        JOIN pg_catalog.pg_namespace AS n ON n.oid = defaults.defaclnamespace
        JOIN pg_catalog.pg_roles AS grantee ON grantee.rolname = required.role
        CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) AS acl
        WHERE defaults.defaclrole = (
            SELECT oid FROM pg_catalog.pg_roles WHERE rolname = :migration_role)
          AND n.nspname = required.schema
          AND defaults.defaclobjtype::pg_catalog.text = required.kind
          AND acl.grantee = grantee.oid AND acl.privilege_type = required.privilege))
"""

# Only absence of the actual authority surface permits the staged installer
# path. An existing malformed/partial boundary must satisfy the full finalized
# predicate instead. Bootstrap-only producer/control tables are not outbox
# installation and therefore are intentionally not sentinels here.
_INSTALLED = {
    "restore_drill_executor_admin": """
        SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace AS n
            WHERE n.nspname = 'restore_drill_executor' AND (
                EXISTS (SELECT 1 FROM pg_catalog.pg_class WHERE relnamespace = n.oid)
                OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc WHERE pronamespace = n.oid)))
    """,
    "dnd_generation_admin": """
        SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace
            WHERE nspname = 'dnd_generation_private')
        OR EXISTS (SELECT 1 FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname IN
                ('dnd_generation_guard', 'dnd_generation_mutations'))
        OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc AS p
            JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname = 'context_dnd_mutate')
    """,
    "runtime_attention_admin": """
        SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname IN
                ('runtime_attention_outbox', 'runtime_attention_delivery_lease'))
    """,
}
_STAGED = """
SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace AS n
    JOIN pg_catalog.pg_proc AS p ON p.pronamespace = n.oid
        AND p.proname = 'install_interface' AND p.pronargs = 0 AND p.prokind = 'f'
    WHERE n.nspname = :admin
      AND pg_catalog.has_schema_privilege(CAST(:migration_role AS pg_catalog.name), n.oid, 'USAGE')
      AND pg_catalog.has_function_privilege(
          CAST(:migration_role AS pg_catalog.name), p.oid, 'EXECUTE')
      AND NOT pg_catalog.has_schema_privilege(
          CAST(:migration_role AS pg_catalog.name), n.oid, 'CREATE')
      AND NOT pg_catalog.pg_has_role(
          CAST(:migration_role AS pg_catalog.name), n.nspowner, 'MEMBER')
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(p.proacl,
          pg_catalog.acldefault('f', p.proowner))) AS acl WHERE acl.grantee = 0))
"""

# Qualify the existing read-only applied-revision predicates without altering
# literals (especially pinned search paths) or comments. Never call upgrade,
# downgrade, installers or rollback preflights while checking admission.
_SQL_TOKEN = re.compile(r"--[^\n]*|'(?:''|[^'])*'|\b[A-Za-z_][A-Za-z_0-9]*\b")
_CATALOG_NAMES = frozenset(
    {
        "pg_namespace",
        "pg_roles",
        "pg_class",
        "pg_proc",
        "pg_auth_members",
        "pg_policy",
        "pg_attribute",
        "pg_constraint",
        "pg_index",
        "pg_trigger",
        "pg_depend",
        "pg_type",
        "pg_attrdef",
        "aclexplode",
        "acldefault",
        "pg_has_role",
        "has_schema_privilege",
        "has_table_privilege",
        "has_function_privilege",
        "has_column_privilege",
        "pg_get_expr",
        "pg_get_constraintdef",
        "pg_get_indexdef",
        "pg_get_triggerdef",
        "pg_get_userbyid",
        "lower",
        "count",
        "unnest",
        "array_length",
        "cardinality",
        "to_regclass",
        "to_regnamespace",
        "to_regprocedure",
        "text",
        "name",
        "oid",
        "oidvector",
        "regtype",
        "regclass",
        "regnamespace",
        "regprocedure",
        "bool",
        "int4",
        "jsonb",
    }
)


def _qualified_predicate(sql: str) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group()
        if token.lower() == "current_user":
            return "CAST(:migration_role AS pg_catalog.name)"
        if token.lower() in _CATALOG_NAMES and (
            match.start() == 0 or sql[match.start() - 1] != "."
        ):
            return "pg_catalog." + token
        return token

    return _SQL_TOKEN.sub(replace, sql)


@lru_cache(maxsize=3)
def _finalized_predicate(filename: str) -> str:
    # The runner already requires this checked-in Alembic directory. Loading
    # trusted code constants avoids maintaining a weaker duplicate of hundreds
    # of lines of catalog/RLS/ACL proof. Only source is cached, never DB state.
    source = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "core" / filename
    spec = importlib.util.spec_from_file_location("_bootstrap_" + source.stem, source)
    if spec is None or spec.loader is None:
        raise BootstrapPrerequisiteError("profile unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return _qualified_predicate(module._TRUSTED_FINALIZED_INTERFACE_SQL)


def _default_profile() -> list[dict[str, str]]:
    result = []
    for schema in _BUTLERS + ("connectors",):
        role = f"butler_{schema}_rw" if schema != "connectors" else "connector_writer"
        scopes = [
            (schema, "r", ("SELECT", "INSERT", "UPDATE", "DELETE")),
            (schema, "S", ("USAGE", "SELECT", "UPDATE")),
            (schema, "f", ("EXECUTE",)),
            ("public", "r", ("SELECT", "INSERT", "UPDATE", "DELETE")),
            ("public", "S", ("USAGE", "SELECT", "UPDATE")),
        ]
        if schema != "connectors":
            scopes.append((schema, "r", ("TRIGGER", "REFERENCES")))
            scopes.append(("connectors", "r", ("SELECT",)))
            if schema == "relationship":
                scopes.append(("switchboard", "r", ("SELECT",)))
        else:
            scopes += [
                ("switchboard", "r", ("SELECT", "INSERT", "UPDATE", "DELETE")),
                ("switchboard", "S", ("USAGE", "SELECT", "UPDATE")),
            ]
        for namespace, kind, privileges in scopes:
            result.extend(
                {"role": role, "schema": namespace, "kind": kind, "privilege": p}
                for p in privileges
            )
    return result


def check_bootstrap_connection(connection: Connection) -> None:
    """Inspect this actual connection before online migration side effects.

    A managed privileged lifecycle caller may read the trusted private singleton
    configurations using its *existing* authority to identify the normal subject.
    This does not grant that authority to the normal migration identity.
    Connection/authentication failures occur before this function and retain
    their separate exception channel. Unreadable SQL is a sanitized refusal.
    """
    try:
        identity = connection.execute(
            text("SELECT rolname, rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user")
        ).one()
        for admin, _filename in _ADMINS:
            if not _true(connection, _ADMIN_PROVENANCE, {"admin": admin}):
                raise BootstrapPrerequisiteError("missing or untrusted")
        migration_role = identity[0]
        if identity[1] is True:
            subjects = []
            for admin, _filename in _ADMINS:
                # Static qualified name; validated relation owner/shape/ACL above.
                rows = (
                    connection.execute(
                        text(
                            f'SELECT migration_role FROM "{admin}".bootstrap_configuration '
                            "WHERE singleton"
                        )
                    )
                    .scalars()
                    .all()
                )
                if len(rows) != 1:
                    raise BootstrapPrerequisiteError("missing or untrusted")
                subjects.append(rows[0])
            if len(set(subjects)) != 1:
                raise BootstrapPrerequisiteError("missing or untrusted")
            migration_role = subjects[0]
        params = {
            "migration_role": migration_role,
            "roles": json.dumps(
                [{"role": f"butler_{s}_rw", "schema": s} for s in _BUTLERS]
                + [{"role": "connector_writer", "schema": "connectors"}]
            ),
            "defaults": json.dumps(_default_profile()),
        }
        if not _true(connection, _BASE_PROFILE, params) or not _true(
            connection, _DEFAULT_ACLS, params
        ):
            raise BootstrapPrerequisiteError("missing or untrusted")
        for admin, filename in _ADMINS:
            if _true(connection, _INSTALLED[admin]):
                valid = _true(connection, _finalized_predicate(filename), params)
            else:
                valid = _true(connection, _STAGED, {**params, "admin": admin})
            if not valid:
                raise BootstrapPrerequisiteError("missing or untrusted")
    except DBAPIError:
        # Exception chaining would print SQL/driver parameters or private rows.
        raise BootstrapPrerequisiteError("unreadable") from None


def check_bootstrap_database(db_url: str) -> None:
    """Open a normal connection and inspect it; never bootstrap or commit DDL."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            check_bootstrap_connection(connection)
    finally:
        engine.dispose()
