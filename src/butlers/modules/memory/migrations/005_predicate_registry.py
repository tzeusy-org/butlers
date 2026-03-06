"""predicate_registry

Adds an optional predicate registry table for guiding consistent predicate
usage across fact extraction.  Not enforced — used for prompt injection so
LLM extractors prefer known predicates.

Revision ID: mem_005
Revises: mem_004
Create Date: 2026-03-06 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_005"
down_revision = "mem_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS predicate_registry (
            name TEXT PRIMARY KEY,
            expected_subject_type TEXT,
            expected_object_type TEXT,
            is_edge BOOLEAN NOT NULL DEFAULT false,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Seed with predicates from the fact-extraction taxonomy
    # noqa: E501 — SQL seed data, line length not applicable
    op.execute(  # noqa: E501
        "INSERT INTO predicate_registry"
        " (name, expected_subject_type, expected_object_type,"
        " is_edge, description) VALUES"
        # Relationship domain predicates
        " ('relationship_to_user', 'person', NULL, false,"
        "  'Relationship label to the user'),"
        " ('birthday', 'person', NULL, false, 'Date of birth'),"
        " ('anniversary', 'person', NULL, false, 'Date-based milestone'),"
        " ('preference', NULL, NULL, false,"
        "  'Food, activities, interests, dislikes'),"
        " ('current_interest', NULL, NULL, false,"
        "  'Hobbies, projects, topics being explored'),"
        " ('contact_phone', 'person', NULL, false, 'Phone number'),"
        " ('contact_email', 'person', NULL, false, 'Email address'),"
        " ('workplace', 'person', NULL, false,"
        "  'Company or organization name'),"
        " ('lives_in', 'person', NULL, false, 'City or location'),"
        " ('relationship_status', 'person', NULL, false,"
        "  'Married, single, dating, etc.'),"
        " ('children', 'person', NULL, false,"
        "  'Names and ages of children'),"
        " ('nickname', 'person', NULL, false,"
        "  'Preferred name or alias'),"
        " ('food_allergy', 'person', NULL, false,"
        "  'Food allergy or intolerance'),"
        " ('current_project', NULL, NULL, false,"
        "  'Active project or work initiative'),"
        " ('travel_intent', 'person', NULL, false,"
        "  'Planned or potential travel'),"
        # General domain predicates
        " ('goal', NULL, NULL, false, 'Personal or project goal'),"
        " ('resource', NULL, NULL, false,"
        "  'Useful link, article, or tool'),"
        " ('idea', NULL, NULL, false,"
        "  'Brainstorming note or future plan'),"
        " ('note', NULL, NULL, false,"
        "  'General observation or reminder'),"
        " ('deadline', NULL, NULL, false,"
        "  'Time-sensitive task or date'),"
        " ('status', NULL, NULL, false,"
        "  'Current state of a project or activity'),"
        " ('recommendation', NULL, NULL, false,"
        "  'Recommendation for a place, book, tool, etc.'),"
        # Health domain predicates
        " ('medication', NULL, NULL, false,"
        "  'Current medication with dosage'),"
        " ('medication_frequency', NULL, NULL, false,"
        "  'How often a medication is taken'),"
        " ('dosage', NULL, NULL, false, 'Amount per dose'),"
        " ('condition_status', NULL, NULL, false,"
        "  'Active, managed, or resolved condition'),"
        " ('symptom_pattern', NULL, NULL, false,"
        "  'Recurring symptoms or triggers'),"
        " ('symptom_trigger', NULL, NULL, false,"
        "  'What causes or worsens a symptom'),"
        " ('measurement_baseline', NULL, NULL, false,"
        "  'Typical or target measurement values'),"
        " ('dietary_restriction', 'person', NULL, false,"
        "  'Food allergies or dietary restrictions'),"
        " ('exercise_routine', 'person', NULL, false,"
        "  'Regular physical activity'),"
        " ('doctor_name', 'person', NULL, false,"
        "  'Healthcare provider name'),"
        " ('pharmacy', 'person', NULL, false,"
        "  'Preferred pharmacy location'),"
        " ('allergy', 'person', NULL, false,"
        "  'Medication or substance allergy'),"
        # Edge predicates (relate two entities)
        " ('knows', 'person', 'person', true,"
        "  'Social connection between two people'),"
        " ('works_at', 'person', 'organization', true,"
        "  'Employment relationship'),"
        " ('lives_with', 'person', 'person', true,"
        "  'Cohabitation relationship'),"
        " ('manages', 'person', 'person', true,"
        "  'Management relationship'),"
        " ('parent_of', 'person', 'person', true,"
        "  'Parent-child relationship'),"
        " ('sibling_of', 'person', 'person', true,"
        "  'Sibling relationship')"
        " ON CONFLICT (name) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS predicate_registry CASCADE")
