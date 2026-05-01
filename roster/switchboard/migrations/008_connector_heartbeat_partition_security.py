"""Run heartbeat partition maintenance with migration-owner privileges.

The runtime role (e.g. ``butler_switchboard_rw``) does not own the
``connector_heartbeat_log`` parent table, so it cannot create or drop
partitions even though it must call the maintenance routines. Marking the
maintenance functions ``SECURITY DEFINER`` lets them run with the
migration-owner privileges that created the table while keeping the call
sites unchanged.

A ``SECURITY DEFINER`` function with a permissive search_path is a known
object-injection vector: any schema later prepended to the path can shadow
unqualified references. We pin the search_path to the function's home
schema plus ``pg_temp`` (always implicit and last by PostgreSQL contract)
so the function's behavior is independent of the caller's session and
``public`` is excluded.

Revision ID: sw_008
Revises: sw_007
Create Date: 2026-05-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_008"
down_revision = "sw_007"
branch_labels = None
depends_on = None


_FUNCTION_SIGNATURES = (
    "switchboard_connector_heartbeat_log_ensure_partition(TIMESTAMPTZ)",
    "switchboard_connector_heartbeat_log_drop_expired_partitions(INTERVAL, TIMESTAMPTZ)",
)


def upgrade() -> None:
    for signature in _FUNCTION_SIGNATURES:
        op.execute(
            f"""
            DO $$
            DECLARE
                target_schema text := current_schema();
            BEGIN
                EXECUTE format(
                    'ALTER FUNCTION %I.{signature} SECURITY DEFINER',
                    target_schema
                );
                EXECUTE format(
                    'ALTER FUNCTION %I.{signature} SET search_path TO %I, pg_temp',
                    target_schema,
                    target_schema
                );
            END
            $$;
            """
        )


def downgrade() -> None:
    for signature in _FUNCTION_SIGNATURES:
        op.execute(
            f"""
            DO $$
            DECLARE
                target_schema text := current_schema();
            BEGIN
                EXECUTE format(
                    'ALTER FUNCTION %I.{signature} SECURITY INVOKER',
                    target_schema
                );
                EXECUTE format(
                    'ALTER FUNCTION %I.{signature} RESET search_path',
                    target_schema
                );
            END
            $$;
            """
        )
