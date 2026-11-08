"""Read effective runtime-role privileges on ``public`` and diff them against the manifest.

Read-only and content-blind: it inspects ``pg_class``/``pg_roles`` and
``has_table_privilege`` (which counts direct grants, ``PUBLIC`` grants and role
membership), never table rows. A runtime role missing from the catalog is reported,
not skipped: the manifest describes every role the roster derives.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from butlers.db_authority.grants import PUBLIC_RELATIONS, TABLE_PRIVILEGES, expected_privileges
from butlers.db_authority.roles import runtime_roles

_PRIVILEGE_COLUMNS = ",\n".join(
    f"has_table_privilege(r.oid, c.oid, '{p}') AS {p.lower()}" for p in TABLE_PRIVILEGES
)

_MATRIX_SQL = text(
    f"""
    SELECT c.relname,
           CASE WHEN c.relispartition THEN parent.relname END AS parent,
           r.rolname,
           {_PRIVILEGE_COLUMNS}
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    LEFT JOIN pg_catalog.pg_inherits AS i ON i.inhrelid = c.oid AND c.relispartition
    LEFT JOIN pg_catalog.pg_class AS parent ON parent.oid = i.inhparent
    CROSS JOIN pg_catalog.pg_roles AS r
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND r.rolname IN :roles
      AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.pg_depend AS d
          WHERE d.classid = 'pg_catalog.pg_class'::regclass
            AND d.objid = c.oid
            AND d.deptype = 'e'
      )
    ORDER BY c.relname, r.rolname
    """
).bindparams(bindparam("roles", expanding=True))

_ROLES_SQL = text("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname IN :roles").bindparams(
    bindparam("roles", expanding=True)
)


@dataclass(frozen=True)
class PublicCatalog:
    """Effective privileges per ``(relation, role)``; partitions map to their parent."""

    privileges: Mapping[tuple[str, str], frozenset[str]]
    parents: Mapping[str, str]
    present_roles: frozenset[str]


def read_public_catalog(conn: Connection, roles: tuple[str, ...] | None = None) -> PublicCatalog:
    """Read every non-extension ``public`` relation's privileges for the runtime roles."""
    roles = roles or runtime_roles()
    present = frozenset(conn.execute(_ROLES_SQL, {"roles": list(roles)}).scalars())
    privileges: dict[tuple[str, str], frozenset[str]] = {}
    parents: dict[str, str] = {}
    for row in conn.execute(_MATRIX_SQL, {"roles": list(roles)}).mappings():
        held = frozenset(p for p in TABLE_PRIVILEGES if row[p.lower()])
        privileges[(row["relname"], row["rolname"])] = held
        if row["parent"] is not None:
            parents[row["relname"]] = row["parent"]
    return PublicCatalog(privileges, parents, present)


def diff_public_catalog(
    catalog: PublicCatalog,
    roles: tuple[str, ...] | None = None,
    *,
    absent_ok: Iterable[str] = (),
) -> list[str]:
    """Return one line per difference between the catalog and the manifest; empty is a match.

    ``absent_ok`` names manifest relations the database may legitimately lack (a test
    database without the WhatsApp bridge, say); every other absent row is reported.
    """
    roles = roles or runtime_roles()
    problems = [f"role missing from catalog: {r}" for r in roles if r not in catalog.present_roles]
    relations = {relation for relation, _ in catalog.privileges}
    for relation in sorted(relations):
        row = catalog.parents.get(relation, relation)
        if row not in PUBLIC_RELATIONS:
            problems.append(f"relation not in manifest: {relation}")
            continue
        expected = expected_privileges(row, roles)
        for role in roles:
            if role not in catalog.present_roles:
                continue
            actual = catalog.privileges.get((relation, role), frozenset())
            if actual != expected[role]:
                problems.append(
                    f"{relation} {role}: manifest {_fmt(expected[role])} catalog {_fmt(actual)}"
                )
    allowed = set(absent_ok)
    present_rows = {catalog.parents.get(r, r) for r in relations}
    problems.extend(
        f"manifest relation absent from catalog: {name}"
        for name in PUBLIC_RELATIONS
        if name not in present_rows and name not in allowed
    )
    return problems


def _fmt(privileges: frozenset[str]) -> str:
    return ",".join(p for p in TABLE_PRIVILEGES if p in privileges) or "-"
