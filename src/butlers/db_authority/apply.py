"""Converge runtime-role privileges on ``public`` relations to the grant manifest.

``plan_public_grants`` turns a catalog read (``butlers.db_authority.catalog``) into the
REVOKE/GRANT statements that make every manifest relation match its row; relations the
manifest does not name are never touched. ``apply_public_grants`` runs the plan in one
transaction under an advisory lock, then re-reads the catalog and rolls back unless it
now equals the manifest, so a revoke that does not take (a ``PUBLIC`` grant, a role
membership) fails loudly instead of leaving a half-applied state.

Statements and the report name relations, roles and privileges only, never row data.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from butlers.db_authority.catalog import PublicCatalog, read_public_catalog
from butlers.db_authority.grants import PUBLIC_RELATIONS, TABLE_PRIVILEGES, expected_privileges
from butlers.db_authority.roles import runtime_roles

# Arbitrary fixed key: "butlers grants" as ASCII, so concurrent appliers serialize.
GRANT_APPLY_LOCK_KEY = 0x62_75_74_6C_65_72_73_67


@dataclass(frozen=True)
class GrantChange:
    """One privilege set to revoke from or grant to one role on one relation."""

    action: str  # "REVOKE" or "GRANT"
    relation: str
    role: str
    privileges: tuple[str, ...]
    skipped: bool = False  # the connecting login cannot administer this relation's ACL

    def describe(self) -> str:
        line = f"{self.action} {','.join(self.privileges)} ON {self.relation} {self.role}"
        return f"SKIPPED (login does not own it) {line}" if self.skipped else line


def _bounds(
    row: str, roles: tuple[str, ...]
) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """Per role: the most it may hold (migration intent) and the least it must hold.

    The floor is intent intersected with what an init-db replay leaves, so apply never
    grants a privilege the deployed bootstrap deliberately removed (``cost_claims``
    DELETE for the connector, say): the manifest only ever narrows a live database.
    """
    ceiling = expected_privileges(row, roles)
    replayed = expected_privileges(row, roles, after_init_db_replay=True)
    return ceiling, {role: ceiling[role] & replayed[role] for role in roles}


_UNMANAGED_SQL = text(
    """
    SELECT c.relname
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND NOT pg_catalog.pg_has_role(current_user, c.relowner, 'USAGE')
      AND NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user)
    """
)


def unmanaged_relations(conn: Connection) -> frozenset[str]:
    """``public`` relations whose ACL the connecting login cannot change (not owner/member)."""
    return frozenset(conn.execute(_UNMANAGED_SQL).scalars())


def plan_public_grants(
    catalog: PublicCatalog,
    roles: tuple[str, ...] | None = None,
    *,
    unmanaged: frozenset[str] = frozenset(),
) -> list[GrantChange]:
    """Return the changes that bring ``catalog`` inside the manifest bounds, revokes first.

    Changes on ``unmanaged`` relations are listed with ``skipped=True`` and never executed:
    a REVOKE by a non-owner silently does nothing, so claiming it would be a lie.
    """
    roles = roles or runtime_roles()
    revokes: list[GrantChange] = []
    grants: list[GrantChange] = []
    for relation in sorted({rel for rel, _ in catalog.privileges}):
        row = catalog.parents.get(relation, relation)
        if row not in PUBLIC_RELATIONS:
            continue
        ceiling, floor = _bounds(row, roles)
        for role in roles:
            if role not in catalog.present_roles:
                continue
            actual = catalog.privileges.get((relation, role), frozenset())
            extra = tuple(p for p in TABLE_PRIVILEGES if p in actual - ceiling[role])
            missing = tuple(p for p in TABLE_PRIVILEGES if p in floor[role] - actual)
            skipped = relation in unmanaged
            if extra:
                revokes.append(GrantChange("REVOKE", relation, role, extra, skipped))
            if missing:
                grants.append(GrantChange("GRANT", relation, role, missing, skipped))
    return [*revokes, *grants]


def _statement(conn: Connection, change: GrantChange) -> str:
    quote = conn.dialect.identifier_preparer.quote
    target = "public." + quote(change.relation)
    role = quote(change.role)
    privileges = ", ".join(change.privileges)
    if change.action == "REVOKE":
        return " ".join(["REVOKE", privileges, "ON TABLE", target, "FROM", role])
    return " ".join(["GRANT", privileges, "ON TABLE", target, "TO", role])


_COLUMN_ACL_SQL = text(
    """
    SELECT c.relname, a.attname, r.rolname, p.privilege_type
    FROM pg_catalog.pg_attribute AS a
    JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) AS p
    JOIN pg_catalog.pg_roles AS r ON r.oid = p.grantee
    WHERE n.nspname = 'public' AND a.attacl IS NOT NULL
      AND a.attnum > 0 AND NOT a.attisdropped AND r.rolname IN :roles
    """
).bindparams(bindparam("roles", expanding=True))


def column_acls(conn: Connection) -> frozenset[tuple[str, str, str, str]]:
    """Column-level grants (relation, column, role, privilege); the manifest does not model them."""
    rows = conn.execute(_COLUMN_ACL_SQL, {"roles": list(runtime_roles())})
    return frozenset((r[0], r[1], r[2], r[3]) for r in rows)


def _restorecolumn_acls(conn: Connection, before: frozenset[tuple[str, str, str, str]]) -> None:
    """Re-grant column privileges a table-level REVOKE dropped (it revokes every column too)."""
    quote = conn.dialect.identifier_preparer.quote
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for relation, column, role, privilege in sorted(before - column_acls(conn)):
        grouped.setdefault((relation, role, privilege), []).append(quote(column))
    for (relation, role, privilege), columns in grouped.items():
        conn.execute(
            text(
                " ".join(
                    [
                        "GRANT",
                        privilege,
                        "(" + ", ".join(columns) + ")",
                        "ON TABLE",
                        "public." + quote(relation),
                        "TO",
                        quote(role),
                    ]
                )
            )
        )


def apply_public_grants(conn: Connection, *, allow_grants: bool = False) -> list[GrantChange]:
    """Apply the plan in the caller's transaction; raise unless the result is within bounds.

    The caller commits only when this returns, so a failed verification rolls everything back.
    GRANT changes widen a live database, so they are refused unless ``allow_grants``; column
    grants that a table-level REVOKE dropped are restored and checked unchanged.
    """
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": GRANT_APPLY_LOCK_KEY})
    unmanaged = unmanaged_relations(conn)
    changes = plan_public_grants(read_public_catalog(conn), unmanaged=unmanaged)
    widening = [c for c in changes if c.action == "GRANT" and not c.skipped]
    if widening and not allow_grants:
        raise RuntimeError(
            "grant apply refused: the plan would widen grants (pass --allow-grants):\n"
            + "\n".join(c.describe() for c in widening[:50])
        )
    columns_before = column_acls(conn)
    for change in changes:
        if not change.skipped:
            conn.execute(text(_statement(conn, change)))
    _restorecolumn_acls(conn, columns_before)
    if column_acls(conn) != columns_before:
        raise RuntimeError("grant apply changed column-level grants; rolled back")
    leftover = [
        c
        for c in plan_public_grants(read_public_catalog(conn), unmanaged=unmanaged)
        if not c.skipped
    ]
    if leftover:
        raise RuntimeError(
            "grant apply did not converge; rolled back:\n"
            + "\n".join(c.describe() for c in leftover[:50])
        )
    return changes
