"""The encounter ledger: one state per person and per calendar occurrence (bu-5ii4gj.3 E2a).

Revision ID: rel_048
Revises: rel_046

``relationship.encounters`` keeps planned, moved, declined, happened and unknown occurrences
distinct per person (``happened_corroborated`` and the ``chronicler`` basis are reserved for the
Chronicler intent-outcome read, which a later slice adds), so "last met", cadence and series memory can derive only from what happened.
A row is *derived*: a deterministic reducer recomputes it from the occurrence's participant
records, the owner's and the guest's RSVP, and the owner's debrief answer, so a replay converges and
nothing here is a source of truth of its own. ``entity_id`` has no foreign key: the entity lives in
``public`` and a merge repoints it with the other per-schema references.

The same migration widens ``meeting_debriefs.state`` with ``did_not_happen`` and ``moved``, the two
owner answers the ledger reads. Nothing writes them yet (the answer tool changes in a later slice).
The constraint is dropped and recreated by name, so a re-run is a no-op; the downgrade maps the two
new states to ``expired`` before restoring the original four, because a row in a state the old
constraint forbids would otherwise block it.

Downgrade drops the ledger, which has no other writer.
"""

from __future__ import annotations

from alembic import op

revision = "rel_048"
down_revision = "rel_046"
branch_labels = None
depends_on = None

_ROLE = "butler_relationship_rw"
_STATES = (
    "scheduled",
    "moved",
    "cancelled",
    "declined_by_owner",
    "declined_by_them",
    "happened_attested",
    "happened_corroborated",
    "did_not_happen",
    "unknown",
)
_BASES = ("schedule", "rsvp", "owner_debrief", "chronicler")
_OLD_DEBRIEF_STATES = ("pending", "none_agreed", "captured", "expired")
_NEW_DEBRIEF_STATES = (*_OLD_DEBRIEF_STATES, "did_not_happen", "moved")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _debrief_constraint(states: tuple[str, ...]) -> tuple[str, ...]:
    """Drop the inline (auto-named) and the named state CHECK, then add the named one."""
    return (
        """
        DO $$ BEGIN
            IF to_regclass('relationship.meeting_debriefs') IS NOT NULL THEN
                ALTER TABLE relationship.meeting_debriefs
                    DROP CONSTRAINT IF EXISTS meeting_debriefs_state_check;
                ALTER TABLE relationship.meeting_debriefs
                    DROP CONSTRAINT IF EXISTS ck_meeting_debriefs_state;
            END IF;
        END $$
        """,
        f"""
        DO $$ BEGIN
            IF to_regclass('relationship.meeting_debriefs') IS NOT NULL THEN
                ALTER TABLE relationship.meeting_debriefs
                    ADD CONSTRAINT ck_meeting_debriefs_state
                    CHECK (state IN ({_quoted(states)}));
            END IF;
        END $$
        """,
    )


def upgrade_statements() -> tuple[str, ...]:
    return (
        f"""
        CREATE TABLE IF NOT EXISTS relationship.encounters (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_id UUID NOT NULL,
            event_id UUID NOT NULL,
            occurrence_start TIMESTAMPTZ NOT NULL,
            series_key TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ({_quoted(_STATES)})),
            basis TEXT NOT NULL CHECK (basis IN ({_quoted(_BASES)})),
            evidence_refs JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_encounters_occurrence UNIQUE (entity_id, event_id, occurrence_start)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_encounters_entity_start
            ON relationship.encounters (entity_id, occurrence_start DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_encounters_event
            ON relationship.encounters (event_id, occurrence_start)
        """,
        f"""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{_ROLE}') THEN
                GRANT SELECT,INSERT,UPDATE,DELETE ON relationship.encounters TO {_ROLE};
            END IF;
        END $$
        """,
        *_debrief_constraint(_NEW_DEBRIEF_STATES),
    )


def downgrade_statements() -> tuple[str, ...]:
    return (
        """
        DO $$ BEGIN
            IF to_regclass('relationship.meeting_debriefs') IS NOT NULL THEN
                UPDATE relationship.meeting_debriefs
                SET state = 'expired'
                WHERE state IN ('did_not_happen', 'moved');
            END IF;
        END $$
        """,
        *_debrief_constraint(_OLD_DEBRIEF_STATES),
        "DROP TABLE IF EXISTS relationship.encounters",
    )


def upgrade() -> None:
    for statement in upgrade_statements():
        op.execute(statement)


def downgrade() -> None:
    for statement in downgrade_statements():
        op.execute(statement)
