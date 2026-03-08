"""relationship_predicates

Seed predicate_registry with relationship-domain predicates used by the
relationship butler's SPO-backed tools.

Temporal predicates (is_temporal=true) — each fact coexists with others at a
different valid_at per contact entity:
  - interaction_{type}  (dynamic; one canonical predicate per interaction type)
    → stored as a single 'interaction' predicate with type in metadata
  - life_event
  - contact_note  (append-only; no supersession)
  - activity

Property/superseding predicates (is_temporal=false) — one active fact per
(entity_id, predicate) key, latest supersedes previous:
  - gift
  - loan
  - contact_task
  - reminder

Dynamic property predicates (quick_facts):
  - quick_facts keys become the predicate name directly at runtime.
    No static registry rows needed; seeded here for documentation only.

Revision ID: mem_011
Revises: mem_010
Create Date: 2026-03-08 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_011"
down_revision = "mem_010"
branch_labels = None
depends_on = None

_TEMPORAL_PREDICATES = [
    (
        "interaction",
        "person",
        True,
        "Interaction with a contact. Content = summary. "
        "Metadata: {type, notes, direction, duration_minutes}. valid_at = occurred_at.",
    ),
    (
        "life_event",
        "person",
        True,
        "Significant life event for a contact. Content = summary. "
        "Metadata: {life_event_type, description}. valid_at = happened_at.",
    ),
    (
        "contact_note",
        "person",
        True,
        "Note about a contact (append-only). Content = note text. "
        "Metadata: {emotion}. valid_at = created_at.",
    ),
    (
        "activity",
        "person",
        True,
        "Activity feed entry for a contact. Content = description. "
        "Metadata: {type, entity_type, entity_id}. valid_at = created_at.",
    ),
]

_PROPERTY_PREDICATES = [
    (
        "gift",
        "person",
        False,
        "Gift tracked for a contact. Content = description. "
        "Metadata: {occasion, status}. Supersession per contact entity.",
    ),
    (
        "loan",
        "person",
        False,
        "Loan tracked for a contact. Content = description. "
        "Metadata: {amount_cents, currency, direction, settled, settled_at}. "
        "Supersession per contact entity + subject key.",
    ),
    (
        "contact_task",
        "person",
        False,
        "Task scoped to a contact. Content = title. "
        "Metadata: {description, completed, completed_at}. "
        "Supersession per contact entity + subject key.",
    ),
    (
        "reminder",
        "person",
        False,
        "Reminder for a contact. Content = message. "
        "Metadata: {type, cron, due_at, dismissed}. "
        "Supersession per contact entity + subject key.",
    ),
]


def upgrade() -> None:
    # Ensure is_temporal column exists (added in mem_007; guard for older schemas).
    op.execute("""
        ALTER TABLE predicate_registry
        ADD COLUMN IF NOT EXISTS is_temporal BOOLEAN NOT NULL DEFAULT false
    """)

    _on_conflict = (
        "ON CONFLICT (name) DO UPDATE SET"
        " is_temporal = EXCLUDED.is_temporal,"
        " expected_subject_type = EXCLUDED.expected_subject_type,"
        " description = EXCLUDED.description"
    )
    for name, subject_type, is_temporal, description in _TEMPORAL_PREDICATES + _PROPERTY_PREDICATES:
        op.execute(
            f"INSERT INTO predicate_registry"
            f" (name, expected_subject_type, is_temporal, description) VALUES"
            f" ('{name}', '{subject_type}', {is_temporal}, '{description}')"
            f" {_on_conflict}"
        )


def downgrade() -> None:
    names = ", ".join(f"'{name}'" for name, *_ in _TEMPORAL_PREDICATES + _PROPERTY_PREDICATES)
    op.execute(f"DELETE FROM predicate_registry WHERE name IN ({names})")
