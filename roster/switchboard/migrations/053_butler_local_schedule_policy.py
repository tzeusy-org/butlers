"""Expose each butler's own administrative policy to its effective runtime role.

Revision ID: sw_053
Revises: sw_052

Generalises sw_038 (QA only). Runtime roles ``butler_<name>_rw`` cannot read
switchboard tables, so since role isolation (#5016) non-switchboard schedulers
could not observe an administrative hold. The function derives the butler from
the caller's effective role, never from caller input, and returns only that
butler's control-plane policy. No table grant is added. The search path is
pinned to ``pg_catalog, pg_temp`` (sw_039); the body qualifies every relation.

The function is retained on downgrade: migration state cannot prove that every
reader has stopped, and removing its authority would strand active schedulers.
"""

from __future__ import annotations

from alembic import op

revision = "sw_053"
down_revision = "sw_052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The production chain lives in switchboard; migration fixtures can bind it
    # to another schema. Resolve the producer once, never from caller input.
    schema = str(op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    quoted_schema = '"' + schema.replace('"', '""') + '"'
    op.execute(f"""
        CREATE OR REPLACE FUNCTION public.butler_local_schedule_policy()
        RETURNS TABLE (butler_name text, policy_state text, policy_provenance text)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE
            v_name text;
        BEGIN
            -- current_user is the definer here, not the runtime caller.
            v_name := substring(
                current_setting('role', true) from '^butler_([a-z][a-z0-9_]*)_rw$'
            );
            IF v_name IS NULL THEN
                RAISE EXCEPTION 'butler_policy_denied' USING ERRCODE = '42501';
            END IF;
            RETURN QUERY
                SELECT p.name::text, p.policy_state::text, p.policy_provenance::text
                FROM {quoted_schema}.butler_registry_control_plane AS p
                WHERE p.name = v_name;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'butler_policy_missing' USING ERRCODE = 'P0002';
            END IF;
        END
        $fn$;
    """)
    op.execute("REVOKE ALL ON FUNCTION public.butler_local_schedule_policy() FROM PUBLIC")
    # Every existing butler runtime role; the function still returns only the
    # caller's own row. A role created later lacks EXECUTE and the scheduler
    # treats that as unavailable (fail open), as before this revision.
    op.execute("""
        DO $grant$
        DECLARE
            r record;
        BEGIN
            FOR r IN SELECT rolname FROM pg_roles WHERE rolname ~ '^butler_[a-z][a-z0-9_]*_rw$'
            LOOP
                EXECUTE format(
                    'GRANT EXECUTE ON FUNCTION public.butler_local_schedule_policy() TO %I',
                    r.rolname
                );
            END LOOP;
        END
        $grant$;
    """)


def downgrade() -> None:
    # No caller inventory exists at migration time. Keep the role fence and
    # policy intact until a separately reviewed replacement can retire readers.
    pass
