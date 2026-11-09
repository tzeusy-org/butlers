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

from sqlalchemy import text
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

    def describe(self) -> str:
        return f"{self.action} {','.join(self.privileges)} ON {self.relation} {self.role}"


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


def plan_public_grants(
    catalog: PublicCatalog, roles: tuple[str, ...] | None = None
) -> list[GrantChange]:
    """Return the changes that bring ``catalog`` inside the manifest bounds, revokes first."""
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
            if extra:
                revokes.append(GrantChange("REVOKE", relation, role, extra))
            if missing:
                grants.append(GrantChange("GRANT", relation, role, missing))
    return [*revokes, *grants]


def _statement(conn: Connection, change: GrantChange) -> str:
    quote = conn.dialect.identifier_preparer.quote
    target = f"public.{quote(change.relation)}"
    role = quote(change.role)
    privileges = ", ".join(change.privileges)
    if change.action == "REVOKE":
        return f"REVOKE {privileges} ON TABLE {target} FROM {role}"
    return f"GRANT {privileges} ON TABLE {target} TO {role}"


def apply_public_grants(conn: Connection) -> list[GrantChange]:
    """Apply the plan in the caller's transaction; raise unless the result is within bounds.

    The caller commits only when this returns, so a failed verification rolls everything back.
    """
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": GRANT_APPLY_LOCK_KEY})
    changes = plan_public_grants(read_public_catalog(conn))
    for change in changes:
        conn.execute(text(_statement(conn, change)))
    leftover = plan_public_grants(read_public_catalog(conn))
    if leftover:
        raise RuntimeError(
            "grant apply did not converge; rolled back:\n"
            + "\n".join(c.describe() for c in leftover[:50])
        )
    return changes
