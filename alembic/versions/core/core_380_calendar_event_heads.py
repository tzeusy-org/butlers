"""read_calendar_event_heads: the journal's newest version of each event (bu-5ii4gj.10).

Revision ID: core_380
Revises: core_379
Create Date: 2026-11-26 14:00:00.000000

``connectors.calendar_event_revisions`` (core_369) keeps titles, locations and attendees verbatim,
so no runtime role but the writer and the chronicler may read it. The drift job (slice A-S2)
needs only to know, for the provider events it is checking, what the newest observed version says
about their status and times, which can be newer than a butler's projection. This SECURITY DEFINER
function returns exactly that and nothing else: the provider event id, status, start, end and when
it was observed. No title, location, attendee or raw payload, and the input is capped at 500 ids.

``search_path`` is pinned and every relation is schema-qualified. EXECUTE is revoked from PUBLIC
as its own statement and granted to the Switchboard role only. The core chain runs once per butler
schema over this shared function: every statement is idempotent under an advisory lock, and only
the last schema at core_380 drops it.
"""

from __future__ import annotations

from alembic import op

revision = "core_380"
down_revision = "core_379"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_380:calendar-heads', 0))"
_FUNCTION = "public.read_calendar_event_heads(text[])"
_SWITCHBOARD_ROLE = "butler_switchboard_rw"

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION public.read_calendar_event_heads(p_event_ids text[])
RETURNS TABLE (
    event_id text, status text, starts_at timestamptz, ends_at timestamptz,
    observed_at timestamptz)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT DISTINCT ON (r.event_id)
           r.event_id, r.status, r.starts_at, r.ends_at, r.observed_at
      FROM connectors.calendar_event_revisions r
     WHERE r.event_id = ANY (p_event_ids[1:500])
       AND r.recurring_event_id IS NULL
     ORDER BY r.event_id, r.observed_at DESC, r.id DESC
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
                    THEN substring(version_num FROM 6)::integer >= 380
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
