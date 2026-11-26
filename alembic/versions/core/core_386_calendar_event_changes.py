"""read_calendar_event_changes: organizer-made changes from the revision journal (bu-5ii4gj.11 N3).

Revision ID: core_386
Revises: core_384
Create Date: 2026-11-27 09:00:00.000000

``connectors.calendar_event_revisions`` (core_369) keeps titles, attendees and raw payloads, so no
runtime role but the writer and the chronicler may read it. The Needs-you queue (slice N3) must show
what an organizer moved under the owner. This SECURITY DEFINER function returns exactly that and
nothing else: for each revision observed after ``p_since`` whose start, end, status or location
differs from the same event's previous journaled revision (a revision with no earlier version is
not a change: a newly seen invitation belongs to the invitations list), the
event id, calendar id, observation time, the prior and new values of those fields, whether a prior
version was known, and ``organizer_self``. No title, attendee, description, conference or raw
column.

Rows the owner made themselves (the organizer is the owner) and rows of an event the owner has
declined are not returned. A policy-blocked row keeps times and status only: its organizer cannot
be told, so ``organizer_self`` is NULL and it is returned, never hidden. The window is clamped to
14 days and the result to 201 rows (one past the route's cap of 200, so it can say "truncated"),
newest first. Only events still in the future count (the prior start, for a cancellation), and one
change journaled on several of the owner's calendars is one row, keyed on iCalUID plus the new
values, keeping the earliest observation. ``p_owner_emails`` are the owner's own connected account
addresses: an organizer among them is the owner (``organizer_self`` true).

``search_path`` is pinned and every relation is schema-qualified. EXECUTE is revoked from PUBLIC
as its own statement and granted to the Switchboard role only (the dashboard reads through it, like
``read_calendar_event_heads``). The core chain runs once per butler schema over this shared
function: every statement is idempotent under an advisory lock, and only the last schema at
core_386 drops it.
"""

from __future__ import annotations

from alembic import op

revision = "core_386"
down_revision = "core_384"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_386:calendar-changes', 0))"
_FUNCTION = "public.read_calendar_event_changes(timestamptz, integer, text[])"
_SWITCHBOARD_ROLE = "butler_switchboard_rw"

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION public.read_calendar_event_changes(
    p_since timestamptz, p_limit integer, p_owner_emails text[])
RETURNS TABLE (
    event_id text, calendar_id text, observed_at timestamptz, prior_known boolean,
    all_day boolean,
    prior_start timestamptz, new_start timestamptz,
    prior_end timestamptz, new_end timestamptz,
    prior_status text, new_status text,
    prior_location text, new_location text,
    organizer_self boolean)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT d.event_id, d.calendar_id, d.observed_at, d.prior_known, d.all_day,
           d.prior_start, d.new_start, d.prior_end, d.new_end,
           d.prior_status, d.new_status, d.prior_location, d.new_location, d.organizer_self
      FROM (
    SELECT DISTINCT ON (c.dedupe_key, c.new_start, c.new_end, c.new_status, c.new_location)
           c.event_id, c.calendar_id, c.observed_at, c.id, c.prior_known, c.all_day,
           c.prior_start, c.new_start, c.prior_end, c.new_end,
           c.prior_status, c.new_status, c.prior_location, c.new_location, c.organizer_self
      FROM (
        SELECT r.event_id, r.calendar_id, r.observed_at, r.id, r.all_day,
               (p.id IS NOT NULL) AS prior_known,
               p.starts_at AS prior_start, r.starts_at AS new_start,
               p.ends_at AS prior_end, r.ends_at AS new_end,
               p.status AS prior_status, r.status AS new_status,
               p.raw ->> 'location' AS prior_location, r.raw ->> 'location' AS new_location,
               CASE WHEN r.raw IS NULL THEN NULL
                    ELSE COALESCE((r.raw -> 'organizer' ->> 'self') = 'true', false)
                         OR pg_catalog.lower(r.raw -> 'organizer' ->> 'email') = ANY (
                              ARRAY(SELECT pg_catalog.lower(m)
                                      FROM pg_catalog.unnest(COALESCE(p_owner_emails, '{}')) AS m))
                    END AS organizer_self,
               COALESCE(r.raw ->> 'iCalUID', r.event_id) AS dedupe_key,
               COALESCE((
                   SELECT bool_or(a ->> 'self' = 'true' AND a ->> 'responseStatus' = 'declined')
                     FROM pg_catalog.jsonb_array_elements(
                            CASE WHEN pg_catalog.jsonb_typeof(r.raw -> 'attendees') = 'array'
                                 THEN r.raw -> 'attendees' ELSE '[]'::jsonb END) AS a
               ), false) AS owner_declined
          FROM connectors.calendar_event_revisions r
          LEFT JOIN LATERAL (
              SELECT q.id, q.status, q.starts_at, q.ends_at, q.raw
                FROM connectors.calendar_event_revisions q
               WHERE q.endpoint_identity = r.endpoint_identity
                 AND q.calendar_id = r.calendar_id
                 AND q.event_id = r.event_id
                 AND (q.observed_at, COALESCE(q.provider_updated, '-infinity'::timestamptz),
                      COALESCE(q.sequence, -1), q.id)
                     < (r.observed_at, COALESCE(r.provider_updated, '-infinity'::timestamptz),
                        COALESCE(r.sequence, -1), r.id)
               ORDER BY q.observed_at DESC,
                        COALESCE(q.provider_updated, '-infinity'::timestamptz) DESC,
                        COALESCE(q.sequence, -1) DESC, q.id DESC
               LIMIT 1
          ) AS p ON TRUE
         WHERE r.observation IN ('change', 'resync')
           AND r.observed_at > GREATEST(p_since, pg_catalog.now() - interval '14 days')
      ) AS c
     WHERE c.organizer_self IS NOT TRUE
       AND NOT c.owner_declined
       AND c.prior_known
       AND (c.new_start IS DISTINCT FROM c.prior_start
            OR c.new_end IS DISTINCT FROM c.prior_end
            OR c.new_status IS DISTINCT FROM c.prior_status
            OR c.new_location IS DISTINCT FROM c.prior_location)
       AND COALESCE(CASE WHEN c.new_status = 'cancelled' THEN c.prior_start ELSE c.new_start END,
                    c.new_start, c.prior_start) > pg_catalog.now()
     ORDER BY c.dedupe_key, c.new_start, c.new_end, c.new_status, c.new_location,
              c.observed_at ASC, c.id ASC
      ) AS d
     ORDER BY d.observed_at DESC, d.id DESC
     LIMIT LEAST(GREATEST(p_limit, 1), 201)
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
                    THEN substring(version_num FROM 6)::integer >= 386
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
