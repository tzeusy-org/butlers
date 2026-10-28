"""Explicit operator preparation and read-only migration admission.

Preparation executes the one production SQL source only on its independently
verified administrative session. Admission never repairs the catalog or trusts
a completion singleton. Serving admission is a separate stricter boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from butlers.db_authority.roles import PURPOSE_OWNERS, bootstrap_role_manifest

_PSQL_DIRECTIVE = "\\set ON_ERROR_STOP on\n"


def bootstrap_sql() -> str:
    """The one production preparation source, without its psql-only directive."""
    source = (Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql").read_text()
    if source.count(_PSQL_DIRECTIVE) != 1:
        raise BootstrapPrerequisiteError("source invalid")
    return source.replace(_PSQL_DIRECTIVE, "", 1)


def prepare_bootstrap_connection(
    connection, database: str, migration_role: str, *, source_database: str = "butlers"
) -> None:
    """Explicit operator preparation on an already owned privileged DBAPI session.

    The caller owns lifetime and credentials. The source compiler runs before
    any SQL, while the script independently checks its actual privileged actor
    and database before mutating anything. This is never a serving fallback.
    """
    manifest = bootstrap_role_manifest(database, migration_role, source_database=source_database)
    source = bootstrap_sql()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('butlers.source_manifest', %s, false)",
            (json.dumps(manifest),),
        )
        cursor.execute(source)


class BootstrapPrerequisiteError(RuntimeError):
    """A closed diagnostic; no credentials, row data or raw driver text."""

    def __init__(self, category: str) -> None:
        super().__init__(
            f"Database migration prerequisite {category}; complete the reviewed "
            "operator role/extension/ownership preparation before online migrations."
        )


_BOOTSTRAP_PROFILE_SQL = (
    "\n"
    "WITH required AS (SELECT value AS name FROM pg_catalog.jsonb_array_elements_text("
    "\n"
    "    CAST(:roles AS jsonb))),\n"
    "capabilities AS (SELECT r.* FROM pg_catalog.pg_roles r JOIN required ON required.n"
    "ame=r.rolname)\n"
    "SELECT\n"
    "    (SELECT count(*) FROM capabilities)=(SELECT count(*) FROM required)\n"
    "    AND NOT EXISTS(SELECT FROM capabilities WHERE rolcanlogin OR rolinherit OR rol"
    "super\n"
    "        OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)\n"
    "    AND NOT EXISTS(SELECT FROM pg_catalog.pg_roles\n"
    "        WHERE rolname=session_user AND rolname IN (SELECT name FROM required))\n"
    "    AND EXISTS(SELECT FROM pg_catalog.pg_roles WHERE rolname=session_user\n"
    "        AND (current_user=session_user OR (rolsuper AND current_user=(\n"
    "             SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_catalog.pg_database"
    "\n"
    "             WHERE datname=current_database())))\n"
    "        AND (rolsuper OR (NOT rolcreaterole AND NOT rolcreatedb\n"
    "             AND NOT rolreplication AND NOT rolbypassrls))) AS actor_ok,\n"
    "    NOT EXISTS(SELECT FROM pg_catalog.pg_auth_members m\n"
    "        JOIN pg_catalog.pg_roles role ON role.oid=m.roleid\n"
    "        JOIN pg_catalog.pg_roles member ON member.oid=m.member\n"
    "        WHERE role.rolname=ANY(:owners) OR member.rolname=ANY(:owners)) AS owners_"
    "isolated,\n"
    "    NOT EXISTS(SELECT FROM (VALUES('vector'),('pgcrypto'),('uuid-ossp'),('pg_trgm'"
    ")) e(name)\n"
    "        WHERE NOT EXISTS(SELECT FROM pg_catalog.pg_extension WHERE extname=e.name)"
    ") AS extensions_ok,\n"
    "    NOT EXISTS(SELECT FROM pg_catalog.pg_namespace n JOIN capabilities c ON c.oid="
    "n.nspowner WHERE c.rolname<>ALL(:owners))\n"
    "        AND NOT EXISTS(SELECT FROM pg_catalog.pg_database d\n"
    "            JOIN capabilities c ON c.oid=d.datdba WHERE c.rolname<>ALL(:owners)) A"
    "S capability_ownership_ok,\n"
    "    NOT EXISTS(SELECT FROM pg_catalog.jsonb_array_elements_text(CAST(:schemas AS j"
    "sonb)) s(name)\n"
    "        WHERE NOT EXISTS(SELECT FROM pg_catalog.pg_namespace n\n"
    "            WHERE n.nspname=s.name)) AS schemas_ok\n"
)


def check_bootstrap_connection(connection: Connection) -> None:
    """Inspect the actual migration session before extension/schema/version writes.

    Administrative disposable producers compile the same source roster for
    their selected database. This does not affect serving runtime identity or
    allow a caller-provided role/schema list.
    """
    try:
        database, actor = connection.exec_driver_sql(
            "SELECT current_database(), session_user"
        ).one()
        source_database = connection.info.get("butlers.source_database", "butlers")
        manifest = bootstrap_role_manifest(database, actor, source_database=source_database)
        row = connection.execute(
            text(_BOOTSTRAP_PROFILE_SQL),
            {
                "roles": json.dumps(manifest["roles"]),
                "owners": list(PURPOSE_OWNERS),
                "schemas": json.dumps(manifest["schemas"]),
            },
        ).one()
        if len(row) != 5 or any(value is not True for value in row):
            raise BootstrapPrerequisiteError("missing, unsafe or untrusted")
    except DBAPIError:
        raise BootstrapPrerequisiteError("unreadable") from None


def check_bootstrap_database(db_url: str) -> None:
    """Use a finite owned administrative probe, without DDL or catalog repair."""
    engine = create_engine(db_url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            check_bootstrap_connection(connection)
    finally:
        engine.dispose()
