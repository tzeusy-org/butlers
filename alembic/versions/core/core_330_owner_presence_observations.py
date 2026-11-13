"""Coordinate-free owner presence observations for Home (bu-jv1moa.66).

Revision ID: core_330
Revises: core_329
Create Date: 2026-11-13 00:00:00.000000

``public.owner_presence_observations`` is the one typed feed the Home presence reducer reads
OwnTracks through. Home has no read on ``connectors.owntracks_points`` and never will; this
function classifies fixes against the owner's live places inside the database and returns only
``(observed_at, accuracy_m, place_ref, side, ssid_match)``. Coordinates, SSIDs and the raw
payload never leave it.

Rules, mirrored in ``butlers.perception.presence`` (a contract test pins the parity):

* outside: accuracy <= 100 m and distance > radius + max(accuracy, 50 m);
* inside: accuracy <= 100 m and distance <= radius;
* the band between them is no signal (hysteresis), as is any fix with a worse or missing
  accuracy;
* an exact, case-sensitive match of the payload ``SSID`` to one of the place's SSIDs is an
  inside row with ``ssid_match`` true whatever the GPS says.

SECURITY DEFINER with a pinned search_path and schema-qualified objects. Inside a definer
function ``current_user`` is the definer, so there is no in-body caller check: the gate is
``REVOKE ALL FROM PUBLIC`` plus ``GRANT EXECUTE`` to ``butler_home_rw`` alone. No new table.

The core chain runs once per butler schema over this shared object. Replacing it is
idempotent, and only the last schema still at core_330 drops it (core_319 pattern).
"""

from __future__ import annotations

from alembic import op

revision = "core_330"
down_revision = "core_329"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_330:presence-feed', 0))"
_SIGNATURE = "public.owner_presence_observations(timestamptz, timestamptz, integer)"
_HOME_ROLE = "butler_home_rw"

_FUNCTION = """
CREATE OR REPLACE FUNCTION public.owner_presence_observations(
    p_since timestamptz, p_until timestamptz, p_limit integer
) RETURNS TABLE (
    observed_at timestamptz, accuracy_m integer, place_ref uuid, side text, ssid_match boolean
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    -- One constant block; butlers.perception.presence carries the same numbers.
    c_max_accuracy_m constant double precision := 100;
    c_margin_floor_m constant double precision := 50;
    c_earth_radius_m constant double precision := 6371008.8;
BEGIN
    IF p_since IS NULL OR p_until IS NULL OR p_limit IS NULL
       OR NOT isfinite(p_since) OR NOT isfinite(p_until) OR p_since >= p_until
       OR p_until - p_since > interval '24 hours' OR p_limit NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'bounded presence window required' USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    WITH scored AS (
        SELECT pt.ts AS ts, pt.accuracy AS acc, pl.id AS place_id, pl.radius_m AS radius,
               (nullif(pt.raw_payload ->> 'SSID', '') = ANY (pl.ssids)) IS TRUE AS ssid_hit,
               2 * c_earth_radius_m * asin(least(1.0, sqrt(
                   sin(radians(pl.lat - pt.lat) / 2) ^ 2
                   + cos(radians(pt.lat)) * cos(radians(pl.lat))
                     * sin(radians(pl.lon - pt.lon) / 2) ^ 2))) AS dist
        FROM connectors.owntracks_points pt
        CROSS JOIN public.owner_places pl
        WHERE pt.ts >= p_since AND pt.ts < p_until
          AND pl.deleted_at IS NULL AND NOT pl.paused
    )
    SELECT s.ts, round(s.acc)::integer, s.place_id,
           CASE WHEN s.ssid_hit THEN 'inside'
                WHEN s.dist <= s.radius THEN 'inside'
                ELSE 'outside' END,
           s.ssid_hit
    FROM scored s
    WHERE s.ssid_hit
       OR (s.acc IS NOT NULL AND s.acc >= 0 AND s.acc <= c_max_accuracy_m
           AND (s.dist <= s.radius
                OR s.dist > s.radius + greatest(s.acc, c_margin_floor_m)))
    ORDER BY s.ts ASC, s.place_id ASC
    LIMIT p_limit;
END
$fn$
"""


def _installation_count() -> int:
    """Count schema-local core chains still at or after core_330 (core_319 pattern)."""
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
                    THEN substring(version_num FROM 6)::integer >= 330
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(_FUNCTION)
    op.execute(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_HOME_ROLE}') THEN
                EXECUTE 'GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO {_HOME_ROLE}';
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(f"DROP FUNCTION IF EXISTS {_SIGNATURE}")
