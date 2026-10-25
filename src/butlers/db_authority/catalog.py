"""Fresh connecting-login admission on the actual connection being handed out."""

from __future__ import annotations

from typing import Any

from .roles import AuthorityError, RuntimeProfile

# One statement observes one catalog snapshot. Membership traversal deliberately
# includes NOINHERIT and SET-capable edges: RESET ROLE must not regain authority.
_ADMISSION_SQL = """
WITH RECURSIVE reachable(oid) AS (
    SELECT oid FROM pg_catalog.pg_roles WHERE rolname = session_user
    UNION
    SELECT m.roleid FROM pg_catalog.pg_auth_members m
      JOIN reachable r ON m.member = r.oid
), roles AS (
    SELECT p.* FROM pg_catalog.pg_roles p JOIN reachable r ON r.oid = p.oid
)
SELECT
    current_database() = $3 AND current_user = $2
      AND session_user <> ALL($1::text[]) AS identity_ok,
    EXISTS (SELECT FROM roles WHERE rolname = session_user)
      AND NOT EXISTS (SELECT FROM roles
          WHERE rolname <> session_user AND rolname <> ALL($1::text[])) AS role_set_ok,
    NOT EXISTS (SELECT FROM roles WHERE rolsuper OR rolcreaterole OR rolcreatedb
        OR rolreplication OR rolbypassrls)
      AND NOT EXISTS (SELECT FROM pg_catalog.pg_auth_members m
          JOIN reachable r ON r.oid = m.member WHERE m.admin_option) AS attributes_ok,
    NOT EXISTS (SELECT FROM pg_catalog.pg_database d JOIN reachable r ON r.oid = d.datdba)
      AND NOT EXISTS (SELECT FROM pg_catalog.pg_namespace n
          JOIN reachable r ON r.oid = n.nspowner)
      AND NOT EXISTS (SELECT FROM pg_catalog.pg_class c JOIN reachable r ON r.oid = c.relowner)
      AND NOT EXISTS (SELECT FROM pg_catalog.pg_proc p JOIN reachable r ON r.oid = p.proowner)
      AS ownership_ok,
    NOT EXISTS (SELECT FROM roles r CROSS JOIN pg_catalog.pg_database d
        WHERE COALESCE(pg_catalog.has_database_privilege(r.oid, d.oid, 'CREATE'), true))
      AND NOT EXISTS (SELECT FROM roles r CROSS JOIN pg_catalog.pg_namespace n
        WHERE COALESCE(pg_catalog.has_schema_privilege(r.oid, n.oid, 'CREATE'), true))
      AS create_ok,
    EXISTS (SELECT FROM roles WHERE rolname = $2) AS target_member
"""
_FACTS = (
    "identity_ok",
    "role_set_ok",
    "attributes_ok",
    "ownership_ok",
    "create_ok",
    "target_member",
)


async def assert_runtime_authority(connection: Any, profile: RuntimeProfile) -> None:
    """Refuse unknown/unreadable facts; no cached success or caller-asserted privilege."""
    try:
        row = await connection.fetchrow(
            _ADMISSION_SQL, list(profile.runtime_roles), profile.role, profile.database
        )
        if row is None or any(row[key] is not True for key in _FACTS):
            raise AuthorityError("Runtime database authority is unavailable")
    except AuthorityError:
        raise
    except Exception as exc:
        raise AuthorityError("Runtime database authority is unavailable") from exc
