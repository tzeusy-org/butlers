"""atmosphere_readings / atmosphere_feed_status: shared weather/AQI/pollen context feed.

Revision ID: core_188
Revises: core_187
Create Date: 2026-07-26 00:00:00.000000

bu-ep4ks.16 (2026-07-25 JARVIS pursuit dossier, ranked move #16, slice 1).

WHY: Weather/AQI/pollen is the cheapest cross-butler context feed in the
ecosystem (home pre-conditioning, health advisories, travel destination
outlook) behind Home's explicit air-quality promise (``roster/home/MANIFESTO.md``),
but nothing populates it today -- Home only sees Home Assistant device
state, never ambient conditions.

Two tables, mirroring the ``public.owner_conditions`` (core_184) design of a
shared cross-butler feed table with broad per-role grants:

``atmosphere_readings``
    Append-only successful fetches. One row per poll cycle. Latest row
    (``ORDER BY fetched_at DESC LIMIT 1``) is "current conditions". Pollen
    columns are nullable -- Open-Meteo's air-quality API only reports pollen
    for European locations, so a NULL pollen value for a non-European home
    location is a legitimately-absent field, not a fetch failure (see
    ``pollen_available``).

``atmosphere_feed_status``
    Singleton status row (``id = 1``) updated on every poll attempt,
    success or failure, so the honest degraded-mode envelope
    (CLAUDE.md "Degraded-Mode Response Envelope") can distinguish
    "not configured" (no home location on file) from "configured but the
    last fetch failed" (``last_error`` set, ``consecutive_failures > 0``)
    without scanning ``atmosphere_readings`` (which only gets rows on
    success).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_188"
down_revision = "core_187"
branch_labels = None
depends_on = None

# Every butler role that may read the shared context feed (home is the sole
# writer today; health/travel/general are the named follow-up consumers).
# Mirrors core_184's _ALL_RUNTIME_ROLES.
_ALL_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE"


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.atmosphere_readings (
            id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            latitude                        DOUBLE PRECISION NOT NULL,
            longitude                       DOUBLE PRECISION NOT NULL,
            observed_at                     TIMESTAMPTZ NOT NULL,
            temperature_c                   DOUBLE PRECISION,
            apparent_temperature_c          DOUBLE PRECISION,
            relative_humidity_pct           DOUBLE PRECISION,
            precipitation_mm                DOUBLE PRECISION,
            weather_code                    INTEGER,
            wind_speed_kph                  DOUBLE PRECISION,
            aqi_us                          INTEGER,
            aqi_european                    INTEGER,
            pm2_5                           DOUBLE PRECISION,
            pm10                            DOUBLE PRECISION,
            pollen_tree                     DOUBLE PRECISION,
            pollen_grass                    DOUBLE PRECISION,
            pollen_weed                     DOUBLE PRECISION,
            pollen_available                BOOLEAN NOT NULL DEFAULT false,
            source                          TEXT NOT NULL DEFAULT 'open-meteo',
            raw                             JSONB,
            fetched_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # "Current conditions" lookup: latest successful fetch.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_atmosphere_readings_fetched_at
        ON public.atmosphere_readings (fetched_at DESC)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS public.atmosphere_feed_status (
            id                      SMALLINT PRIMARY KEY DEFAULT 1,
            configured              BOOLEAN NOT NULL DEFAULT false,
            latitude                DOUBLE PRECISION,
            longitude               DOUBLE PRECISION,
            last_attempt_at         TIMESTAMPTZ,
            last_success_at         TIMESTAMPTZ,
            last_error              TEXT,
            consecutive_failures    INTEGER NOT NULL DEFAULT 0,
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_atmosphere_feed_status_singleton CHECK (id = 1)
        )
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.atmosphere_readings", _TABLE_PRIVILEGES, role)
        _grant_best_effort("public.atmosphere_feed_status", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.atmosphere_feed_status CASCADE")
    op.execute("DROP TABLE IF EXISTS public.atmosphere_readings CASCADE")
