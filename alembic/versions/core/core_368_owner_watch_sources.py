"""The owner-watch source reader (bu-4c7nvp.1.5.1).

Revision ID: core_368
Revises: core_364
Create Date: 2026-11-23 12:00:00.000000

``public.owner_watch_sources()`` is the one read the owner-watch providers make over perception:
the registered perception kinds and, for each, whether any timeline row exists (``written``, a
boolean, never a time, so no activity pattern leaks). It returns no value, subject, label,
evidence or any other timeline column. It is ``SECURITY DEFINER`` with a pinned
``search_path``, owned by ``perception_owner`` (the perception tables are FORCE-RLS, owner-only),
``REVOKE``d from PUBLIC, and ``EXECUTE`` goes to ``butler_switchboard_rw`` alone. The function
checks that identity itself too, so a later broader grant still reads nothing.

The definition lives in the fixed ``scripts/init-db.sql`` installer, as for core_322, core_328 and
core_333, because only that installer can create a ``perception_owner`` function. This revision
re-runs the installer and then proves, read-only, that the bootstrap really applied the pinned body,
owner, search path and ACL. A database whose bootstrap predates this change fails loudly here:
the migration calls the *installed* ``perception_admin.install_interface()``, whose body
only changes when the fixed bootstrap is replayed, so a database bootstrapped before this revision
needs one replay of ``scripts/init-db.sql`` (a cluster-superuser act: the owner's, or a deploy that
already replays it) before this revision applies.

It is outside the perception function allowlist, so the pinned backup catalog profile is unchanged.

The core chain runs once per butler schema over this shared function, so a run that finds it
installed exactly reads the catalog only (no replay of the whole perception installer). The
downgrade is a no-op: the function is read-only and additive, and only ``perception_owner`` could
drop it, so it stays for the other schemas and for a later upgrade; with no provider wired it is
unreachable.
"""

from __future__ import annotations

from alembic import op

revision = "core_368"
down_revision = "core_364"
branch_labels = None
depends_on = None

_SWITCHBOARD = "butler_switchboard_rw"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_368:owner-watch-sources', 0))"
_BODY_SHA = "bd76915048ad3d4652a67e0078003cde2283694ee24144441d95f4e1cf39ff6f"

# Catalog-only and lock-free: the function exists with the pinned body, owner, pinned search path,
# SECURITY DEFINER, STABLE, and an ACL of its owner plus (when the role exists) the Switchboard
# role alone, with EXECUTE and no grant option.
_INSTALLED = f"""
SELECT EXISTS (
    SELECT 1 FROM pg_proc p
    WHERE p.oid = to_regprocedure('public.owner_watch_sources()')
      AND p.prosecdef AND p.provolatile = 's'
      AND p.proowner = to_regrole('perception_owner')
      AND p.proconfig = ARRAY['search_path=pg_catalog, pg_temp']
      AND encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') = '{_BODY_SHA}'
      AND NOT EXISTS (
          SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
          LEFT JOIN pg_roles r ON r.oid = a.grantee
          WHERE a.grantee <> p.proowner
            AND (r.rolname IS DISTINCT FROM '{_SWITCHBOARD}'
                 OR a.privilege_type <> 'EXECUTE' OR a.is_grantable)
      )
      AND (
          NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_SWITCHBOARD}')
          OR EXISTS (
              SELECT 1 FROM aclexplode(p.proacl) a
              WHERE a.grantee = to_regrole('{_SWITCHBOARD}') AND a.privilege_type = 'EXECUTE'
          )
      )
)
"""


def upgrade() -> None:
    op.execute(_LOCK)
    bind = op.get_bind()
    if bind.exec_driver_sql(_INSTALLED).scalar():
        return
    # Scoped: restore the chain transaction's own timeouts afterwards (SET LOCAL lasts to its end).
    previous = bind.exec_driver_sql(
        "SELECT current_setting('lock_timeout'), current_setting('statement_timeout')"
    ).one()
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("SELECT perception_admin.install_interface()")
    bind.exec_driver_sql("SELECT set_config('lock_timeout', %s, true)", (previous[0],))
    bind.exec_driver_sql("SELECT set_config('statement_timeout', %s, true)", (previous[1],))
    if not bind.exec_driver_sql(_INSTALLED).scalar():
        raise RuntimeError(
            "owner watch source reader not applied: re-run the fixed bootstrap "
            "(scripts/init-db.sql) before migrating"
        )


def downgrade() -> None:
    # Read-only and additive; only perception_owner could drop it. See the module docstring.
    op.execute(_LOCK)
