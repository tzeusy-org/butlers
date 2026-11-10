"""runtime_probe_control_receipts admits the dashboard service-call audience.

Revision ID: core_327
Revises: core_326
Create Date: 2026-11-11 00:00:00.000000

RFC 0038 D7 (bu-2c1g7v). A dashboard call to a model-reachable tool reaches
Switchboard's service listener with a capability signed by the runtime-probe
control signer under its own audience, ``switchboard.service_call.v1``. Its
256-bit nonce is single-use the same way a runtime-probe nonce is: one durable
receipt, committed before the call is routed, on the same
``(audience, nonce_digest)`` primary key. The audience column already keeps
the two planes' nonces apart; core_201's CHECK admitted only the runtime-probe
audience, and this revision widens it to exactly the two.

Nothing else about the table changes: the same five columns, the retention
trigger, the TRUNCATE guard, Switchboard-only grants and FORCEd row security.

The core chain runs once per butler schema over this shared table, so both
directions take one advisory lock and do nothing while the table is absent.
Only the last schema to leave core_327 restores the single-audience CHECK, and
it restores it ``NOT VALID``: a service-call receipt still inside its
65-second retention window cannot be deleted (the retention trigger refuses,
and FORCEd row security hides the rows from the migration owner anyway), and
it is harmless once the route that consumed it is gone.
"""

from __future__ import annotations

from alembic import op

revision = "core_327"
down_revision = "core_326"
branch_labels = None
depends_on = None

_RECEIPTS = "public.runtime_probe_control_receipts"
_CHECK = "ck_runtime_probe_control_receipts_audience"
_CONTROL_AUDIENCE = "switchboard.runtime_probe_control.v1"
_SERVICE_CALL_AUDIENCE = "switchboard.service_call.v1"
_LOCK = "SELECT pg_advisory_xact_lock(hashtext('core_327_runtime_probe_control_receipts'))"


def _table_exists() -> bool:
    return bool(
        op.get_bind().exec_driver_sql(f"SELECT to_regclass('{_RECEIPTS}') IS NOT NULL").scalar()
    )


def _installation_count() -> int:
    """Count schema-local core chains still at or past core_327."""
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
                    THEN substring(version_num FROM 6)::integer >= 327
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    if not _table_exists():
        return
    op.execute(f"ALTER TABLE {_RECEIPTS} DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute(
        f"ALTER TABLE {_RECEIPTS} ADD CONSTRAINT {_CHECK}"
        f" CHECK (audience IN ('{_CONTROL_AUDIENCE}', '{_SERVICE_CALL_AUDIENCE}'))"
    )


def downgrade() -> None:
    op.execute(_LOCK)
    if not _table_exists() or _installation_count() > 1:
        return
    op.execute(f"ALTER TABLE {_RECEIPTS} DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute(
        f"ALTER TABLE {_RECEIPTS} ADD CONSTRAINT {_CHECK}"
        f" CHECK (audience = '{_CONTROL_AUDIENCE}') NOT VALID"
    )
