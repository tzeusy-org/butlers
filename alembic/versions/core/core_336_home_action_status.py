"""resolve_home_action_status: a parked Home action's closed status (bu-jv1moa.114).

Revision ID: core_336
Revises: core_335
Create Date: 2026-11-14 00:00:00.000000

REQ-home-deterministic-jobs-011.  Cards that mention a parked Home fix queue a timed insight
whose premise is ``("home", "fix_status_unchanged")``.  The Switchboard cannot read
``home.pending_actions`` or ``home.ha_command_log``, so it calls this SECURITY DEFINER function
instead.  It answers one word from a closed set and never an argument, a target, a message or a
reason.  The mapping is the one in ``butlers.jobs.home_prepared_status.classify``; a parity test
runs both over the same rows.

``search_path`` is pinned and every relation is schema-qualified.  ``plpgsql`` resolves the home
tables at call time, so a database without the home chain gets an error the probe turns into
unknown.  EXECUTE is revoked from PUBLIC as a standalone statement (an object this migration
owns) and granted to the Switchboard role only.  The core chain runs once per butler schema over
this shared function, so every statement is idempotent under an advisory lock and only the last
schema at core_336 drops it.
"""

from __future__ import annotations

from alembic import op

revision = "core_336"
down_revision = "core_335"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_336:home-action-status', 0))"
_FUNCTION = "public.resolve_home_action_status(uuid)"
_SWITCHBOARD_ROLE = "butler_switchboard_rw"
#: Mirrors ``butlers.jobs.home_prepared_status.CANCEL_ACTOR``.
_CANCEL_ACTOR = "system:home-departure-fix"

CREATE_FUNCTION = f"""
CREATE OR REPLACE FUNCTION public.resolve_home_action_status(p_action_id uuid)
RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_receipt text;
    v_status text;
    v_decided_by text;
    v_expires_at timestamptz;
BEGIN
    SELECT l.status INTO v_receipt
      FROM home.ha_command_log l
     WHERE l.approval_id = p_action_id
     ORDER BY l.issued_at DESC, l.id DESC
     LIMIT 1;
    IF FOUND THEN
        RETURN CASE v_receipt
            WHEN 'succeeded' THEN 'succeeded'
            WHEN 'failed' THEN 'failed'
            WHEN 'unverified' THEN 'unverified'
            WHEN 'attempting' THEN 'unverified'
            ELSE 'unknown'
        END;
    END IF;
    SELECT a.status, a.decided_by, a.expires_at
      INTO v_status, v_decided_by, v_expires_at
      FROM home.pending_actions a
     WHERE a.id = p_action_id;
    IF NOT FOUND THEN
        RETURN 'unknown';
    END IF;
    RETURN CASE
        WHEN v_status = 'pending' AND v_expires_at IS NOT NULL
             AND v_expires_at <= pg_catalog.now() THEN 'expired'
        WHEN v_status = 'pending' THEN 'awaiting_approval'
        WHEN v_status = 'approved' THEN 'approved_not_run'
        WHEN v_status = 'rejected' AND v_decided_by = '{_CANCEL_ACTOR}' THEN 'cancelled'
        WHEN v_status = 'rejected' THEN 'rejected'
        WHEN v_status = 'cancelled' THEN 'cancelled'
        WHEN v_status = 'expired' THEN 'expired'
        WHEN v_status = 'abandoned' THEN 'abandoned'
        ELSE 'unknown'
    END;
END;
$fn$
"""


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the shared function."""
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
                    THEN substring(version_num FROM 6)::integer >= 336
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(CREATE_FUNCTION)
    op.execute(f"REVOKE ALL ON FUNCTION {_FUNCTION} FROM PUBLIC")
    op.execute(
        f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_SWITCHBOARD_ROLE}') THEN
                EXECUTE 'GRANT EXECUTE ON FUNCTION {_FUNCTION} TO {_SWITCHBOARD_ROLE}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
        END
        $do$
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}")
