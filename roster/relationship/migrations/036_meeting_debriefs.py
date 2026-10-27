"""Meeting debrief ledger.

Revision ID: rel_036
Revises: rel_035
Create Date: 2026-10-03 00:00:00.000000

Phase: meeting debrief (bu-q7vx1q.12).

``relationship.meeting_debriefs`` holds one row per ended calendar occurrence the owner
attended with other people. The zero-LLM ``meeting_debrief`` job inserts the row; the
owner's answer (``none_agreed`` or ``captured``) closes it. The ``(event_id,
occurrence_start)`` unique key is what makes the job idempotent and what lets a recurring
series be debriefed per occurrence.

``attendees`` is a frozen snapshot of the other people at selection time:
``[{"entity_id": "<uuid>|null", "name": "...", "email": "..."}]``. An attendee with no
resolved entity keeps ``entity_id`` null and is listed by email. ``event_id`` is not a
foreign key: the calendar projection may re-sync or drop an event and the owner's answer
must outlive that.

States: ``pending`` (recorded, possibly prompted), ``none_agreed`` and ``captured``
(answered), ``expired`` (never answered, or no longer askable).
"""

from __future__ import annotations

from alembic import op

revision = "rel_036"
down_revision = "rel_035"
branch_labels = None
depends_on = None

_RELATIONSHIP_ROLE = "butler_relationship_rw"


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _grant_best_effort(statement: str, role: str) -> None:
    """Run a GRANT only when *role* exists; tolerate older databases."""
    role_exists = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {role_exists} THEN
                {statement};
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
    op.execute("CREATE SCHEMA IF NOT EXISTS relationship")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS relationship.meeting_debriefs (
            id               UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            event_id         UUID        NOT NULL,
            occurrence_start TIMESTAMPTZ NOT NULL,
            occurrence_end   TIMESTAMPTZ NOT NULL,
            event_title      TEXT        NOT NULL DEFAULT '',
            attendees        JSONB       NOT NULL DEFAULT '[]'::jsonb,
            state            TEXT        NOT NULL DEFAULT 'pending'
                                 CHECK (state IN ('pending', 'none_agreed', 'captured', 'expired')),
            batch_position   INT,
            prompted_at      TIMESTAMPTZ,
            answered_at      TIMESTAMPTZ,
            session_id       TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_meeting_debriefs_occurrence UNIQUE (event_id, occurrence_start)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_meeting_debriefs_state_prompted
            ON relationship.meeting_debriefs (state, prompted_at)
        """
    )
    _grant_best_effort(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE relationship.meeting_debriefs "
        f'TO "{_RELATIONSHIP_ROLE}"',
        _RELATIONSHIP_ROLE,
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS relationship.meeting_debriefs")
