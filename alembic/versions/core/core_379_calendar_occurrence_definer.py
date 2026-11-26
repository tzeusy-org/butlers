"""resolve_calendar_occurrence: has the occurrence changed since the owner said yes (bu-5ii4gj.10).

Revision ID: core_379
Revises: core_378
Create Date: 2026-11-26 13:00:00.000000

The "changed since you said yes" candidate (slice A-S4) is queued with a premise the Switchboard
checks when the timed lane is about to send, and it cannot read a butler's
``calendar_owner_agreements`` table, so it calls this SECURITY DEFINER function.
It answers one boolean and never a time, a digest or an address:

* ``true``  -- an agreement row exists and the event's current start, end, location or attendees
  differ from what the owner accepted;
* ``false`` -- the row matches again, or the agreement ended (declined, cancelled, no seat);
* ``NULL``  -- unknown: no row for the occurrence, no butler named, a name that is not a schema
  holding the table, or no id.

The butler schema is a parameter because every calendar-hosting butler keeps its own agreements.
It is accepted only when it names an existing relation, and is quoted with ``%I``.
``search_path`` is pinned. EXECUTE is revoked from PUBLIC as its own statement and granted to the
Switchboard role only. The core chain runs once per butler schema over this shared function:
every statement is idempotent under an advisory lock, and only the last schema at core_379 drops it.
"""

from __future__ import annotations

from alembic import op

revision = "core_379"
down_revision = "core_378"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_379:calendar-occurrence', 0))"
_FUNCTION = "public.resolve_calendar_occurrence(text, uuid, timestamptz)"
_SWITCHBOARD_ROLE = "butler_switchboard_rw"

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION public.resolve_calendar_occurrence(
    p_butler text, p_event_id uuid, p_original_start timestamptz)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_changed boolean;
BEGIN
    IF p_butler IS NULL OR p_event_id IS NULL OR p_original_start IS NULL
       OR pg_catalog.to_regclass(
              pg_catalog.format('%I.calendar_owner_agreements', p_butler)) IS NULL
    THEN
        RETURN NULL;
    END IF;
    EXECUTE pg_catalog.format(
        'SELECT ended_at IS NULL AND ('
        'current_starts_at IS DISTINCT FROM agreed_starts_at'
        ' OR current_ends_at IS DISTINCT FROM agreed_ends_at'
        ' OR current_location_digest IS DISTINCT FROM location_digest'
        ' OR current_attendees_digest IS DISTINCT FROM attendees_digest)'
        ' FROM %I.calendar_owner_agreements WHERE event_id = $1 AND occurrence_start = $2',
        p_butler)
        INTO v_changed USING p_event_id, p_original_start;
    RETURN v_changed;
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
                    THEN substring(version_num FROM 6)::integer >= 379
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
