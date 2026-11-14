"""Away-safety declarations and streaks (bu-jv1moa.87, REQ-home-deterministic-jobs-002..007).

Revision ID: home_007
Revises: home_006
Create Date: 2026-11-14 00:00:00.000000

Every threshold here is an owner-confirmed value with no default: a missing row (or a NULL
``away_alert_after_minutes``) means ``unconfigured`` and the job neither alerts nor falls back to
a suggested value.  The CHECKs are validation bounds, not suggestions (design.md, Configuration).

``temperature_watch`` declares a freezer: the owner's label (the only name a message uses), the
reading's unit, and the maximum, hysteresis, dwell and maximum reading age.  ``max_c`` and
``hysteresis_c`` are always Celsius; a Fahrenheit sensor's reading is converted before it is
compared, so one set of bounds serves both units.

``away_safety_settings`` is a singleton holding only ``blind_after_minutes``.  The sensing group
itself is not owner data: it is registered once by a privileged act in the ``home.sensing``
perception profile, never here.

``away_safety_streaks`` holds the consecutive-run streaks the condition ledger cannot (it has no
pre-open state): one row per freezer entity or blind role, with the streak start and the time of
the run that last extended it.  ``last_run_at`` lets a run detect a gap of more than two job
periods and restart the streak instead of bridging it.

``departure_watch.away_alert_after_minutes`` arms an item for the away-door condition.  Its floor
is the departure card's whole delivery window (30 minutes), so a door on the card can never also
open the condition while the card may still be delivered.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_007"
down_revision = "home_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS temperature_watch (
            entity_id               TEXT        PRIMARY KEY
                                    CONSTRAINT ck_temperature_watch_entity
                                    CHECK (entity_id ~ '^sensor\\.[a-z0-9_]+$'),
            role                    TEXT        NOT NULL
                                    CONSTRAINT ck_temperature_watch_role
                                    CHECK (role = 'freezer'),
            owner_label             TEXT        NOT NULL
                                    CONSTRAINT ck_temperature_watch_label
                                    CHECK (char_length(btrim(owner_label)) BETWEEN 1 AND 80),
            unit                    TEXT        NOT NULL
                                    CONSTRAINT ck_temperature_watch_unit
                                    CHECK (unit IN ('°C', '°F')),
            max_c                   NUMERIC(5,2) NOT NULL
                                    CONSTRAINT ck_temperature_watch_max
                                    CHECK (max_c BETWEEN -30.0 AND 0.0),
            hysteresis_c            NUMERIC(4,2) NOT NULL
                                    CONSTRAINT ck_temperature_watch_hysteresis
                                    CHECK (hysteresis_c BETWEEN 0.5 AND 10.0),
            dwell_minutes           INTEGER     NOT NULL
                                    CONSTRAINT ck_temperature_watch_dwell
                                    CHECK (dwell_minutes BETWEEN 5 AND 240),
            max_reading_age_minutes INTEGER     NOT NULL
                                    CONSTRAINT ck_temperature_watch_age
                                    CHECK (max_reading_age_minutes BETWEEN 10 AND 360),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS away_safety_settings (
            singleton           BOOLEAN     PRIMARY KEY DEFAULT true
                                CONSTRAINT ck_away_safety_settings_singleton CHECK (singleton),
            blind_after_minutes INTEGER     NOT NULL
                                CONSTRAINT ck_away_safety_settings_blind
                                CHECK (blind_after_minutes BETWEEN 5 AND 1440),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS away_safety_streaks (
            kind         TEXT        NOT NULL
                         CONSTRAINT ck_away_safety_streaks_kind
                         CHECK (kind IN ('freezer', 'blind')),
            streak_key   TEXT        NOT NULL
                         CONSTRAINT ck_away_safety_streaks_key
                         CHECK (char_length(streak_key) BETWEEN 1 AND 255),
            streak_start TIMESTAMPTZ NOT NULL,
            last_run_at  TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (kind, streak_key),
            CONSTRAINT ck_away_safety_streaks_order CHECK (last_run_at >= streak_start)
        )
    """)
    op.execute("""
        ALTER TABLE departure_watch
            ADD COLUMN IF NOT EXISTS away_alert_after_minutes INTEGER
                CONSTRAINT ck_departure_watch_away_minutes
                CHECK (away_alert_after_minutes BETWEEN 30 AND 1440)
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE departure_watch DROP COLUMN IF EXISTS away_alert_after_minutes")
    op.execute("DROP TABLE IF EXISTS away_safety_streaks")
    op.execute("DROP TABLE IF EXISTS away_safety_settings")
    op.execute("DROP TABLE IF EXISTS temperature_watch")
