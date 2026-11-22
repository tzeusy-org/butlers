"""The one superuser step that moves object ownership to the migrator login (bu-dss7he.6 S3).

Today one shared login migrates, serves the dashboard and runs the daemons, so runtime
code owns the tables whose triggers, policies and definers guard it. The split gives
ownership to a dedicated migrator login: ``transfer_ownership`` runs
``REASSIGN OWNED BY <shared> TO <migrator>`` and grants the migrator every role
membership the shared login holds (same INHERIT/SET/ADMIN options), so the migrator can
do everything a migration could do before. The shared login keeps its memberships, so
the daemons' ``SET ROLE butler_<schema>_rw`` keeps working; it simply owns nothing.

Only objects the shared login owns move. Objects owned by purpose roles
(``dnd_generation_owner`` for the DND FORCE-RLS tables, ``runtime_attention_outbox_owner``,
the dashboard capability owners) keep their owners, so their fencing is unchanged.

What it does not do, by design, and what does it instead:

* Privileges granted *to* the shared login on objects it does not own (schema ``CREATE``,
  database ``CONNECT``) and its default privileges (which ``REASSIGN OWNED`` leaves with
  the old role; they only govern objects that login creates) are not copied: replaying
  ``scripts/init-db.sql`` with ``butlers.connecting_user`` set to the migrator grants them
  to the migrator. The plan lists them as ``acl_not_copied``.
* ``REASSIGN OWNED`` also moves shared objects (databases, tablespaces) the login owns in
  the whole cluster, so a login that owns any database other than the current one, or a
  tablespace, is refused rather than silently handing those over.

Statements and plans name roles and objects only, never row data. Nothing calls this at
runtime; the owner runs ``butlers db transfer-ownership`` once, as a superuser
(docs/data_and_storage/migrator-login.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from butlers.db_authority.roles import runtime_roles

# "butlers owner" as ASCII; distinct from the grant-apply lock.
OWNERSHIP_TRANSFER_LOCK_KEY = 0x62_75_74_6F_77_6E_65_72

_NON_LOGIN_PREFIXES = ("butler_", "connector_")


@dataclass(frozen=True)
class LoginFacts:
    """The role attributes the refusal rules look at."""

    name: str
    exists: bool
    can_login: bool = False
    superuser: bool = False
    createrole: bool = False
    createdb: bool = False
    replication: bool = False
    bypassrls: bool = False


@dataclass(frozen=True)
class Membership:
    """One role the source login is a member of, with its options."""

    role: str
    inherit: bool
    set: bool
    admin: bool

    def options(self) -> str:
        flag = {True: "TRUE", False: "FALSE"}
        return f"INHERIT {flag[self.inherit]}, SET {flag[self.set]}, ADMIN {flag[self.admin]}"


@dataclass(frozen=True)
class TransferPlan:
    """What a transfer moves and copies, and why it would be refused."""

    source: str
    target: str
    owned: tuple[str, ...]
    memberships: tuple[Membership, ...]
    acl_not_copied: tuple[str, ...]
    refusals: tuple[str, ...]


def transfer_refusals(
    *,
    executor_is_superuser: bool,
    source: LoginFacts,
    target: LoginFacts,
    foreign_shared_objects: tuple[str, ...],
) -> list[str]:
    """Return every reason this source/target pairing must not be transferred."""
    problems: list[str] = []
    if not executor_is_superuser:
        problems.append("the connecting login is not a superuser")
    for login in (source, target):
        if not login.exists:
            problems.append(f"login {login.name} does not exist")
    if source.name == target.name:
        problems.append("source and target are the same login")
    if source.exists and source.superuser:
        problems.append(f"{source.name} is a superuser source; only an ordinary login moves")
    if target.exists:
        if not target.can_login:
            problems.append(f"{target.name} is not a LOGIN role")
        for attribute in ("superuser", "createrole", "createdb", "replication", "bypassrls"):
            if getattr(target, attribute):
                problems.append(f"{target.name} has {attribute.upper()}")
    if (
        target.name in runtime_roles()
        or target.name.startswith(_NON_LOGIN_PREFIXES)
        or target.name.endswith("_owner")
    ):
        problems.append(f"{target.name} is a runtime or purpose role name, not a migrator login")
    if foreign_shared_objects:
        problems.append(
            f"{source.name} owns objects outside this database that REASSIGN OWNED would move: "
            + ", ".join(foreign_shared_objects)
        )
    return problems


def membership_statement(membership: Membership, target: str, *, quote: Callable) -> str:
    """``GRANT <role> TO <target> WITH <options>`` copying the source's options."""
    return f"GRANT {quote(membership.role)} TO {quote(target)} WITH {membership.options()}"


_LOGIN_SQL = text(
    """
    SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls
    FROM pg_catalog.pg_roles WHERE rolname = :name
    """
)
_OWNED_SQL = text(
    """
    SELECT pg_catalog.pg_describe_object(d.classid, d.objid, d.objsubid)
    FROM pg_catalog.pg_shdepend AS d
    WHERE d.refclassid = 'pg_catalog.pg_authid'::regclass
      AND d.refobjid = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = :name)
      AND CASE WHEN :owned
          -- REASSIGN OWNED leaves default privileges (pg_default_acl) with the old role.
          THEN d.deptype = 'o' AND d.classid <> 'pg_catalog.pg_default_acl'::regclass
          ELSE d.deptype = 'a'
               OR (d.deptype = 'o' AND d.classid = 'pg_catalog.pg_default_acl'::regclass)
          END
      AND d.dbid = (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database())
    ORDER BY 1
    """
)
_FOREIGN_SQL = text(
    """
    SELECT 'database ' || datname FROM pg_catalog.pg_database
    WHERE datdba = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = :name)
      AND datname <> current_database()
    UNION ALL
    SELECT 'tablespace ' || spcname FROM pg_catalog.pg_tablespace
    WHERE spcowner = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = :name)
    ORDER BY 1
    """
)
_CURRENT_DATABASE_SQL = text(
    """
    SELECT 'database ' || datname FROM pg_catalog.pg_database
    WHERE datname = current_database()
      AND datdba = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = :name)
    """
)
_MEMBERSHIPS_SQL = text(
    """
    SELECT r.rolname, bool_or(m.inherit_option), bool_or(m.set_option), bool_or(m.admin_option)
    FROM pg_catalog.pg_auth_members AS m
    JOIN pg_catalog.pg_roles AS r ON r.oid = m.roleid
    WHERE m.member = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = :name)
    GROUP BY r.rolname
    ORDER BY r.rolname
    """
)


def _login(conn: Connection, name: str) -> LoginFacts:
    row = conn.execute(_LOGIN_SQL, {"name": name}).first()
    if row is None:
        return LoginFacts(name, exists=False)
    return LoginFacts(name, True, *(bool(value) for value in row))


def _names(conn: Connection, sql, **params: object) -> tuple[str, ...]:
    return tuple(conn.execute(sql, params).scalars())


def _owned(conn: Connection, name: str) -> tuple[str, ...]:
    """Objects in this database owned by ``name``, plus this database itself if it owns it."""
    current = _names(conn, _CURRENT_DATABASE_SQL, name=name)
    return (*current, *_names(conn, _OWNED_SQL, name=name, owned=True))


def _memberships(conn: Connection, name: str) -> tuple[Membership, ...]:
    return tuple(
        Membership(role, bool(inherit), bool(set_), bool(admin))
        for role, inherit, set_, admin in conn.execute(_MEMBERSHIPS_SQL, {"name": name})
    )


def read_transfer_plan(conn: Connection, source: str, target: str) -> TransferPlan:
    """Read-only: what ``transfer_ownership`` would move and copy, and any refusals."""
    executor_is_superuser = bool(
        conn.execute(
            text("SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user")
        ).scalar()
    )
    refusals = transfer_refusals(
        executor_is_superuser=executor_is_superuser,
        source=_login(conn, source),
        target=_login(conn, target),
        foreign_shared_objects=_names(conn, _FOREIGN_SQL, name=source),
    )
    return TransferPlan(
        source=source,
        target=target,
        owned=_owned(conn, source),
        memberships=_memberships(conn, source),
        acl_not_copied=_names(conn, _OWNED_SQL, name=source, owned=False),
        refusals=tuple(refusals),
    )


def transfer_ownership(conn: Connection, source: str, target: str) -> TransferPlan:
    """Move ownership and copy memberships in the caller's transaction; raise unless done.

    The caller commits only when this returns, so a refusal or a failed verification
    leaves the database unchanged. Re-running after success is a no-op: the source owns
    nothing and every membership grant already exists.
    """
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": OWNERSHIP_TRANSFER_LOCK_KEY})
    plan = read_transfer_plan(conn, source, target)
    if plan.refusals:
        raise RuntimeError("ownership transfer refused:\n" + "\n".join(plan.refusals))
    quote = conn.dialect.identifier_preparer.quote
    conn.execute(text(f"REASSIGN OWNED BY {quote(source)} TO {quote(target)}"))
    for membership in plan.memberships:
        conn.execute(text(membership_statement(membership, target, quote=quote)))
    still_owned = _owned(conn, source)
    if still_owned:
        raise RuntimeError(
            f"ownership transfer did not converge; {source} still owns: "
            + ", ".join(still_owned[:50])
        )
    granted = {m.role: m for m in _memberships(conn, target)}
    missing = [
        m.role
        for m in plan.memberships
        if (g := granted.get(m.role)) is None
        or (m.inherit and not g.inherit)
        or (m.set and not g.set)
        or (m.admin and not g.admin)
    ]
    if missing:
        raise RuntimeError("ownership transfer did not copy memberships: " + ", ".join(missing))
    return plan
