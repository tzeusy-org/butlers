"""journey_identity

Revision ID: travel_003
Revises: travel_002
Create Date: 2026-09-10 00:00:00.000000

bu-2jtfw.8: PNR-keyed booking identity and a journey connection graph.

Adds ``travel.booking_records`` (one row per provider-scoped PNR/record locator,
converged with ``ON CONFLICT DO UPDATE``) and gives ``travel.legs`` a
``(booking_record_id, segment_index)`` identity so two segments of the same
PNR (e.g. an outbound and a return sharing one ``confirmation_number``) each
get their own leg row instead of the second segment deduping away against
the first (the historical bug: dedup keyed on ``confirmation_number`` alone).

Adds the traveller party (``travel.travellers`` optionally linked to an
already-known ``public.entities`` person, ``travel.leg_passengers`` joining
travellers to legs) so a two-passenger itinerary stores one leg row shared by
both passengers rather than one leg per passenger. Unresolved booking names
remain local because butler roles cannot write shared identity.

Adds ``travel.airport_minimum_connect`` (seeded with the airports already
present in ``travel.legs`` plus a curated set of major international hubs)
and ``travel.connections`` (one row per adjacent leg pair, derived --
recomputed by ``roster/travel/tools/connections.py`` and
``src/butlers/jobs/flight_status.py``, never hand-edited).

Downgrade restores the owner traveller's per-leg seat onto the legacy
``travel.legs.seat`` column before dropping the party tables. Derived
connection data is discarded, but owner seat data survives rollback.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "travel_003"
down_revision = "travel_002"
branch_labels = None
depends_on = None

# Curated international minimum-connect minutes for a small set of major hubs
# where a generic default is likely to be qualitatively wrong (long
# immigration/security transfer times). Airports not listed here fall back to
# the dynamic per-existing-leg seed below, or remain absent entirely (verdict
# 'unknown') when no leg has ever referenced them.
_CURATED_MINIMUM_CONNECT: tuple[tuple[str, int, int], ...] = (
    # (airport_code, minimum_connect_minutes, interline_buffer_minutes)
    ("PEK", 90, 45),
    ("LHR", 75, 45),
    ("JFK", 90, 45),
    ("DXB", 60, 30),
    ("NRT", 75, 45),
    ("SIN", 60, 30),
)

# Conservative generic default for any airport already seen in travel.legs
# but not covered by the curated list above -- see docstring "Slice plan" in
# the bu-2jtfw.8 design: "seeded with the airports present in travel.legs".
_DEFAULT_MINIMUM_CONNECT_MINUTES = 60
_DEFAULT_INTERLINE_BUFFER_MINUTES = 30


def upgrade() -> None:
    # --- travel.booking_records ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS travel.booking_records (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id            UUID REFERENCES travel.trips(id) ON DELETE CASCADE,
            record_locator     TEXT,
            source_message_id  TEXT,
            provider           TEXT NOT NULL DEFAULT '',
            metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_booking_records_provider_locator
            ON travel.booking_records (provider, record_locator)
            WHERE record_locator IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_booking_records_source_message_id
            ON travel.booking_records (source_message_id)
    """)

    # --- travel.legs: segment identity ---
    op.execute("""
        ALTER TABLE travel.legs
            ADD COLUMN IF NOT EXISTS segment_index INT,
            ADD COLUMN IF NOT EXISTS booking_record_id UUID
                REFERENCES travel.booking_records(id) ON DELETE SET NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_legs_booking_record_segment
            ON travel.legs (booking_record_id, segment_index)
            WHERE booking_record_id IS NOT NULL AND segment_index IS NOT NULL
    """)

    # --- travel.travellers (party, linked to public.entities) ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS travel.travellers (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id       UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
            entity_id     UUID REFERENCES public.entities(id),
            traveller_key TEXT NOT NULL,
            display_name  TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (trip_id, traveller_key)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_travellers_entity
            ON travel.travellers (entity_id)
    """)

    # --- travel.leg_passengers (join: which travellers rode which leg) ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS travel.leg_passengers (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            leg_id         UUID NOT NULL REFERENCES travel.legs(id) ON DELETE CASCADE,
            traveller_id   UUID NOT NULL REFERENCES travel.travellers(id) ON DELETE CASCADE,
            seat           TEXT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (leg_id, traveller_id)
        )
    """)

    # --- travel.airport_minimum_connect ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS travel.airport_minimum_connect (
            airport_code               TEXT PRIMARY KEY,
            minimum_connect_minutes    INT NOT NULL,
            interline_buffer_minutes   INT NOT NULL DEFAULT 30,
            source                     TEXT,
            created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    for airport_code, minimum_minutes, interline_buffer in _CURATED_MINIMUM_CONNECT:
        op.execute(
            "INSERT INTO travel.airport_minimum_connect "
            "(airport_code, minimum_connect_minutes, interline_buffer_minutes, source) "
            f"VALUES ('{airport_code}', {minimum_minutes}, {interline_buffer}, 'curated') "
            "ON CONFLICT (airport_code) DO NOTHING"
        )
    # Dynamic backfill: any airport already referenced by an existing leg that
    # the curated list above did not cover gets a conservative generic default
    # rather than staying silently absent (which would read as 'unknown').
    op.execute(f"""
        INSERT INTO travel.airport_minimum_connect
            (airport_code, minimum_connect_minutes, interline_buffer_minutes, source)
        SELECT DISTINCT airport_code, {_DEFAULT_MINIMUM_CONNECT_MINUTES},
               {_DEFAULT_INTERLINE_BUFFER_MINUTES}, 'backfill'
        FROM (
            SELECT departure_airport_station AS airport_code FROM travel.legs
            WHERE departure_airport_station IS NOT NULL
            UNION
            SELECT arrival_airport_station AS airport_code FROM travel.legs
            WHERE arrival_airport_station IS NOT NULL
        ) seen_airports
        ON CONFLICT (airport_code) DO NOTHING
    """)

    # --- travel.connections (derived, upserted by connections.py / flight_status.py) ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS travel.connections (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id            UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
            inbound_leg_id     UUID NOT NULL REFERENCES travel.legs(id) ON DELETE CASCADE,
            outbound_leg_id    UUID NOT NULL REFERENCES travel.legs(id) ON DELETE CASCADE,
            verdict            TEXT NOT NULL
                                    CHECK (verdict IN ('holds', 'tight', 'broken', 'unknown')),
            available_minutes  INT,
            evidence           JSONB NOT NULL DEFAULT '{}'::jsonb,
            computed_at        TIMESTAMPTZ NOT NULL,
            verdict_changed_at TIMESTAMPTZ NOT NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (inbound_leg_id, outbound_leg_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_connections_trip
            ON travel.connections (trip_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS travel.connections")
    op.execute("DROP TABLE IF EXISTS travel.airport_minimum_connect")
    op.execute("""
        UPDATE travel.legs AS leg
        SET seat = owner_seat.seat
        FROM (
            SELECT lp.leg_id, lp.seat
            FROM travel.leg_passengers AS lp
            JOIN travel.travellers AS traveller ON traveller.id = lp.traveller_id
            JOIN public.entities AS entity ON entity.id = traveller.entity_id
            WHERE 'owner' = ANY(COALESCE(entity.roles, '{}'::text[]))
              AND lp.seat IS NOT NULL
        ) AS owner_seat
        WHERE leg.id = owner_seat.leg_id
    """)
    op.execute("DROP TABLE IF EXISTS travel.leg_passengers")
    op.execute("DROP TABLE IF EXISTS travel.travellers")
    op.execute("DROP INDEX IF EXISTS travel.ux_legs_booking_record_segment")
    op.execute("ALTER TABLE travel.legs DROP COLUMN IF EXISTS booking_record_id")
    op.execute("ALTER TABLE travel.legs DROP COLUMN IF EXISTS segment_index")
    op.execute("DROP TABLE IF EXISTS travel.booking_records")
