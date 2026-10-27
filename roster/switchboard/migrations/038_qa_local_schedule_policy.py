"""Expose only QA's own administrative policy to its effective runtime role.

Revision ID: sw_038
Revises: sw_037
Create Date: 2026-09-27 00:00:00.000000

The function is retained on downgrade: migration state cannot prove that every
reader has stopped, and removing its authority would strand an active scheduler.
"""

from __future__ import annotations

from alembic import op

revision = "sw_038"
down_revision = "sw_037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The production chain lives in switchboard; migration fixtures can bind it
    # to another schema. Resolve the producer once, never from caller input.
    schema = str(op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    quoted_schema = '"' + schema.replace('"', '""') + '"'
    op.execute(f"""
        CREATE OR REPLACE FUNCTION public.qa_local_schedule_policy()
        RETURNS TABLE (policy_state text, policy_provenance text)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {quoted_schema}, pg_temp
        AS $fn$
        DECLARE
            v_state text;
            v_provenance text;
        BEGIN
            -- current_user is the definer here, not the runtime caller.
            IF current_setting('role', true) IS DISTINCT FROM 'butler_qa_rw' THEN
                RAISE EXCEPTION 'qa_policy_denied' USING ERRCODE = '42501';
            END IF;
            SELECT p.policy_state, p.policy_provenance INTO v_state, v_provenance
            FROM {quoted_schema}.butler_registry_control_plane AS p
            WHERE p.name = 'qa';
            IF NOT FOUND THEN
                RAISE EXCEPTION 'qa_policy_missing' USING ERRCODE = 'P0002';
            END IF;
            IF NOT COALESCE(
                (v_state = 'active' AND v_provenance IN ('none', 'legacy_ttl', 'operator'))
                OR (v_state IN ('paused', 'quarantined')
                    AND v_provenance IN ('legacy_operator', 'operator'))
                OR (v_state = 'review_required'
                    AND v_provenance IN ('legacy_ambiguous', 'operator')),
                false
            ) THEN
                RAISE EXCEPTION 'qa_policy_malformed' USING ERRCODE = '22023';
            END IF;
            RETURN QUERY SELECT v_state, v_provenance;
        END
        $fn$;
    """)
    op.execute("REVOKE ALL ON FUNCTION public.qa_local_schedule_policy() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.qa_local_schedule_policy() TO butler_qa_rw")


def downgrade() -> None:
    # No caller inventory exists at migration time. Keep the role fence and
    # policy intact until a separately reviewed replacement can retire readers.
    pass
