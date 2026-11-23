"""resolve_home_arrival: the arrival card's premise, open/closed/unknown (bu-jv1moa.116).

Revision ID: core_338
Revises: core_337
Create Date: 2026-11-14 00:00:00.000000

The Home arrival producer queues a timed insight whose premise is the probe
``("home", "arrival_card_current")``.  The Switchboard checks that premise when the timed lane is
about to send, but it cannot read ``home.arrival_cards``, so it calls this SECURITY DEFINER
function instead.  It answers one boolean and never a label, an entity id or a state:

* ``true``  -- a card for the edge is ``open``, was rechecked within the last three minutes, and
  still says what it said when it was queued (current fingerprint = proposed fingerprint);
* ``false`` -- the card is ``closed`` (enter edge retracted or late, owner left again, nothing to
  report) or the home changed since it was queued, so the queued text is no longer true;
* ``NULL``  -- unknown: no card, or no recheck for three minutes.  The timed lane holds it.

``search_path`` is pinned and every relation is schema-qualified.  ``plpgsql`` resolves
``home.arrival_cards`` at call time, so a database without the home chain gets an error the
probe turns into unknown.  EXECUTE is revoked from PUBLIC as a standalone statement (an object
this migration owns, so it is never wrapped in a tolerant block) and granted to the Switchboard
role only.  The core chain runs once per butler schema over this shared function: every
statement is idempotent under an advisory lock, and only the last schema at core_338 drops it.
"""

from __future__ import annotations

from alembic import op

revision = "core_338"
down_revision = "core_337"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_338:home-arrival', 0))"
_FUNCTION = "public.resolve_home_arrival(uuid)"
_SWITCHBOARD_ROLE = "butler_switchboard_rw"
#: A card older than this is unknown: the home job rechecks every minute.
FRESHNESS = "3 minutes"

CREATE_FUNCTION = f"""
CREATE OR REPLACE FUNCTION public.resolve_home_arrival(p_edge_event_id uuid)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_state text;
    v_checked_at timestamptz;
    v_proposed text;
    v_current text;
BEGIN
    SELECT c.state, c.checked_at, c.proposed_fingerprint, c.fingerprint
      INTO v_state, v_checked_at, v_proposed, v_current
      FROM home.arrival_cards c
     WHERE c.edge_event_id = p_edge_event_id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    IF v_state = 'closed' THEN
        RETURN false;
    END IF;
    IF v_checked_at < pg_catalog.now() - interval '{FRESHNESS}' THEN
        RETURN NULL;
    END IF;
    RETURN v_proposed IS NOT NULL AND v_proposed = v_current;
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
                    THEN substring(version_num FROM 6)::integer >= 338
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
