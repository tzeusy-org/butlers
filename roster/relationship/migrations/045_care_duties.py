"""Care duties and their occurrences, owner-declared and owner-private (bu-s11n0s.13 S1).

Revision ID: rel_045
Revises: rel_044

Owner decision relationship-duty-of-care (2026-11-21, option A): the owner declares recurring
duties for dependents (a school pickup, a parent's medication, pet care), each with an assumed
carer. The subject and the carer are ``public.entities`` rows, never users, and nothing here
contacts anyone. There is no coverage job in this slice: S2 stays deferred past the cutover, so
an occurrence's state is only ever what the owner set (``assumed`` or ``confirmed``) until then.

``care_duties`` holds the declaration. A calendar-anchored duty carries an RFC 5545 ``rrule`` and
a first ``anchor_at``; a completion-anchored duty carries ``interval_days`` and the last
completion in ``anchor_at``. A CHECK fixes that shape so the two kinds cannot be mixed.
``retired_at`` retires a duty without deleting its history.

``care_occurrences`` is one row per (duty, occurrence_start). The owner's per-occurrence carer
assignment is stored here, and a later job must never overwrite it. The state vocabulary is the
full design set so S2 adds no migration; S1 writes only ``assumed`` and ``confirmed``.

Downgrade drops both tables, which have no other writer.
"""

from __future__ import annotations

from alembic import op

revision = "rel_045"
down_revision = "rel_044"
branch_labels = None
depends_on = None


def upgrade_statements() -> tuple[str, ...]:
    return (
        """
        CREATE TABLE relationship.care_duties (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_entity_id UUID NOT NULL,
            title TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
            kind TEXT NOT NULL CHECK (kind IN ('calendar_anchored', 'completion_anchored')),
            rrule TEXT CHECK (rrule IS NULL OR char_length(rrule) BETWEEN 1 AND 500),
            interval_days INTEGER CHECK (interval_days IS NULL OR interval_days BETWEEN 1 AND 3650),
            anchor_at TIMESTAMPTZ NOT NULL,
            window_minutes INTEGER NOT NULL DEFAULT 0 CHECK (window_minutes BETWEEN 0 AND 1440),
            lead_time_hours INTEGER NOT NULL DEFAULT 24 CHECK (lead_time_hours BETWEEN 0 AND 720),
            default_carrier_entity_id UUID,
            retired_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_care_duty_kind_shape CHECK (
                (kind = 'calendar_anchored' AND rrule IS NOT NULL AND interval_days IS NULL)
                OR (kind = 'completion_anchored' AND interval_days IS NOT NULL
                    AND rrule IS NULL))
        )
        """,
        """
        CREATE INDEX ix_care_duties_live ON relationship.care_duties (subject_entity_id)
            WHERE retired_at IS NULL
        """,
        """
        CREATE TABLE relationship.care_occurrences (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            duty_id UUID NOT NULL REFERENCES relationship.care_duties (id) ON DELETE CASCADE,
            occurrence_start TIMESTAMPTZ NOT NULL,
            carrier_entity_id UUID,
            state TEXT NOT NULL CHECK (state IN
                ('assumed', 'confirmed', 'at_risk', 'uncovered', 'done', 'missed', 'unknown')),
            reason TEXT CHECK (reason IS NULL OR char_length(reason) <= 200),
            evidence_ref JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_care_occurrence UNIQUE (duty_id, occurrence_start)
        )
        """,
        """
        CREATE INDEX ix_care_occurrences_start ON relationship.care_occurrences
            (occurrence_start)
        """,
        """
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='butler_relationship_rw') THEN
                GRANT SELECT,INSERT,UPDATE,DELETE ON relationship.care_duties,
                    relationship.care_occurrences TO butler_relationship_rw;
            END IF;
        END $$
        """,
    )


def upgrade() -> None:
    for statement in upgrade_statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS relationship.care_occurrences")
    op.execute("DROP TABLE IF EXISTS relationship.care_duties")
