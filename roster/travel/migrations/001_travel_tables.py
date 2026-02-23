"""travel_tables

Revision ID: travel_001
Revises:
Create Date: 2026-02-23 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "travel_001"
down_revision = None
branch_labels = ("travel",)
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS travel")

    # --- travel.trips ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS travel.trips (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name        TEXT NOT NULL,
            destination TEXT NOT NULL,
            start_date  DATE NOT NULL,
            end_date    DATE NOT NULL CHECK (end_date >= start_date),
            status      TEXT NOT NULL
                            CHECK (status IN ('planned', 'active', 'completed', 'cancelled')),
            metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_trips_dates
            ON travel.trips (start_date, end_date)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_trips_status
            ON travel.trips (status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_trips_destination
            ON travel.trips (destination)
    """)

    # --- travel.legs ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS travel.legs (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id                  UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
            type                     TEXT NOT NULL
                                         CHECK (type IN ('flight', 'train', 'bus', 'ferry')),
            carrier                  TEXT,
            departure_airport_station TEXT,
            departure_city           TEXT,
            departure_at             TIMESTAMPTZ NOT NULL,
            arrival_airport_station  TEXT,
            arrival_city             TEXT,
            arrival_at               TIMESTAMPTZ NOT NULL CHECK (arrival_at >= departure_at),
            confirmation_number      TEXT,
            pnr                      TEXT,
            seat                     TEXT,
            metadata                 JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_legs_trip_departure
            ON travel.legs (trip_id, departure_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_legs_confirmation
            ON travel.legs (confirmation_number)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_legs_pnr
            ON travel.legs (pnr)
    """)

    # --- travel.accommodations ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS travel.accommodations (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id             UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
            type                TEXT NOT NULL
                                    CHECK (type IN ('hotel', 'airbnb', 'hostel')),
            name                TEXT,
            address             TEXT,
            check_in            TIMESTAMPTZ,
            check_out           TIMESTAMPTZ CHECK (check_out >= check_in),
            confirmation_number TEXT,
            metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_accommodations_trip_check_in
            ON travel.accommodations (trip_id, check_in)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_accommodations_confirmation
            ON travel.accommodations (confirmation_number)
    """)

    # --- travel.reservations ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS travel.reservations (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id             UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
            type                TEXT NOT NULL
                                    CHECK (type IN ('car_rental', 'restaurant', 'activity', 'tour')),
            provider            TEXT,
            datetime            TIMESTAMPTZ,
            confirmation_number TEXT,
            metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_reservations_trip_datetime
            ON travel.reservations (trip_id, datetime)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_reservations_confirmation
            ON travel.reservations (confirmation_number)
    """)

    # --- travel.documents ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS travel.documents (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id     UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
            type        TEXT NOT NULL
                            CHECK (type IN ('boarding_pass', 'visa', 'insurance', 'receipt')),
            blob_ref    TEXT,
            expiry_date DATE,
            metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_documents_trip_type
            ON travel.documents (trip_id, type)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_documents_expiry
            ON travel.documents (expiry_date)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS travel.documents")
    op.execute("DROP TABLE IF EXISTS travel.reservations")
    op.execute("DROP TABLE IF EXISTS travel.accommodations")
    op.execute("DROP TABLE IF EXISTS travel.legs")
    op.execute("DROP TABLE IF EXISTS travel.trips")
    op.execute("DROP SCHEMA IF EXISTS travel")
