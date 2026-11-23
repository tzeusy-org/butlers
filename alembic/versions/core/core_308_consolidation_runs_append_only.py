"""Make ``public.consolidation_runs`` append-only for runtime roles (bu-7kbtfw).

Revision ID: core_308
Revises: core_307
Create Date: 2026-11-03 00:00:00.000000

core_119 granted runtime roles ``SELECT`` on the consolidation audit table and
called it read-only, but it never revoked the DML that ``init-db.sql``'s
``ALTER DEFAULT PRIVILEGES ... IN SCHEMA public`` hands every runtime role at
``CREATE TABLE`` time.  Every butler role and ``connector_writer`` could
therefore UPDATE or DELETE audit history.

The read-only premise was also wrong about the writer: consolidation runs
inside each memory-enabled butler daemon, and
``consolidation._record_consolidation_run`` inserts through that daemon's
pool, i.e. as ``butler_<name>_rw``.  Revoking INSERT would silently stop the
best-effort audit writes that feed ``GET /api/memory/stats``.

So the intended grant is append-only: every ``butler_*_rw`` role keeps SELECT
and INSERT (the memory module is opt-in per butler, so the grant follows the
role family, not today's roster), UPDATE and DELETE are revoked, and
``connector_writer`` is read-only.  ``init-db.sql`` repeats the fence after its
broad public grant so a bootstrap replay cannot restore the DML.

The core chain runs once per butler schema over one shared table, so only the
last schema to leave core_308 restores the bootstrap defaults (core_306
pattern).
"""

from __future__ import annotations

from alembic import op

revision = "core_308"
down_revision = "core_307"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_308:consolidation-runs', 0))"
_BUTLER_ROLES = "rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\'"


def _set_runtime_grants(*, append_only: bool) -> None:
    if append_only:
        butler = "REVOKE UPDATE, DELETE ON TABLE public.consolidation_runs FROM %I"
        connector = "REVOKE INSERT, UPDATE, DELETE ON TABLE public.consolidation_runs FROM %I"
    else:
        butler = "GRANT UPDATE, DELETE ON TABLE public.consolidation_runs TO %I"
        connector = "GRANT INSERT, UPDATE, DELETE ON TABLE public.consolidation_runs TO %I"
    op.execute(f"""
        DO $$
        DECLARE role_name text;
        BEGIN
            IF to_regclass('public.consolidation_runs') IS NULL THEN
                RETURN;
            END IF;
            FOR role_name IN SELECT rolname FROM pg_roles WHERE {_BUTLER_ROLES} LOOP
                EXECUTE format(
                    'GRANT SELECT, INSERT ON TABLE public.consolidation_runs TO %I', role_name
                );
                EXECUTE format('{butler}', role_name);
            END LOOP;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'connector_writer') THEN
                EXECUTE 'GRANT SELECT ON TABLE public.consolidation_runs TO connector_writer';
                EXECUTE format('{connector}', 'connector_writer');
            END IF;
        END $$
    """)


def _core_308_installation_count() -> int:
    """Count schema-local core chains that still require the append-only fence."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND left(namespace.nspname, 3) <> 'pg_'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 308
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    _set_runtime_grants(append_only=True)


def downgrade() -> None:
    op.execute(_LOCK)
    # One shared table serves every schema still at core_308; only the last one reopens it.
    if _core_308_installation_count() > 1:
        return
    _set_runtime_grants(append_only=False)
