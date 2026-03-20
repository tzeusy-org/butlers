"""deprecate_baseline_predicates — mem_024

Deprecate the unused baseline predicates from migration 005 that were
superseded by domain-specific versions in migrations 009-011.

Background
----------
Migration 005 seeded a broad taxonomy of predicates as a starting point.
Migrations 009 (health), 010 (finance), and 011 (relationship) introduced
richer, domain-specific replacements with proper is_temporal flags, metadata
shapes, and subject-type constraints. The original 005 predicates covering
those domains are now ambiguous or misleading for LLM extractors.

Deprecation policy
------------------
- status is set to 'deprecated'
- deprecated_at is set to the migration timestamp
- superseded_by names the canonical replacement predicate
  (NULL when the whole class is deprecated with no single replacement)
- The rows are NOT deleted — all historical facts referencing these
  predicates remain valid. Write-time warnings guide LLMs toward the
  canonical alternatives.

Active predicates retained from 005 (not deprecated here):
  Edge predicates (used in relationship domain):
    knows, works_at, lives_with, manages, parent_of, sibling_of
  General predicates with no domain-specific replacement:
    preference, goal, resource, idea, note, deadline, status,
    recommendation, birthday, lives_in

Revision ID: mem_024
Revises: mem_023
Create Date: 2026-03-20 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_024"
down_revision = "mem_023"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Health domain — superseded by migration 009 health predicates
# ---------------------------------------------------------------------------
# Each tuple: (name, superseded_by_or_None)
_HEALTH_DEPRECATED: list[tuple[str, str | None]] = [
    # medication_frequency / dosage merged into the richer 009 'medication' predicate
    # (009 re-seeded 'medication' with structured metadata shape)
    ("medication_frequency", "medication"),
    ("dosage", "medication"),
    # condition_status merged into 009 'condition' predicate
    ("condition_status", "condition"),
    # symptom_pattern / symptom_trigger merged into 009 'symptom' (temporal)
    ("symptom_pattern", "symptom"),
    ("symptom_trigger", "symptom"),
    # measurement_baseline replaced by specific measurement_* temporal predicates
    ("measurement_baseline", "measurement_weight"),
    # dietary_restriction / food_allergy / allergy: use health condition records
    ("dietary_restriction", "condition"),
    ("food_allergy", "condition"),
    ("allergy", "condition"),
    # exercise_routine, doctor_name, pharmacy: tracked via health module quick-facts
    ("exercise_routine", None),
    ("doctor_name", None),
    ("pharmacy", None),
]

# ---------------------------------------------------------------------------
# Relationship domain — superseded by migration 011 relationship predicates
# ---------------------------------------------------------------------------
_RELATIONSHIP_DEPRECATED: list[tuple[str, str | None]] = [
    # relationship_to_user: use quick_facts in relationship module
    ("relationship_to_user", None),
    # contact_phone / contact_email: use quick_facts (relationship module)
    ("contact_phone", None),
    ("contact_email", None),
    # workplace: use 'works_at' edge predicate (also seeded in 005)
    ("workplace", "works_at"),
    # relationship_status: use quick_facts
    ("relationship_status", None),
    # children: use 'parent_of' edge predicate
    ("children", "parent_of"),
    # nickname: use quick_facts
    ("nickname", None),
    # travel_intent: use 'life_event' or 'activity' temporal predicates
    ("travel_intent", "life_event"),
    # anniversary: use 'life_event' temporal predicate
    ("anniversary", "life_event"),
    # current_interest: use 'activity' temporal predicate
    ("current_interest", "activity"),
    # current_project: use 'activity' temporal predicate
    ("current_project", "activity"),
]

# All predicates to deprecate
_ALL_DEPRECATED: list[tuple[str, str | None]] = _HEALTH_DEPRECATED + _RELATIONSHIP_DEPRECATED

# Migration timestamp used for deprecated_at
_DEPRECATED_AT = "2026-03-20 00:00:00+00"


def upgrade() -> None:
    # Group predicates by their superseded_by value and issue one UPDATE per group.
    # This mirrors the bulk approach used in downgrade() and avoids N round-trips.
    from collections import defaultdict

    groups: dict[str | None, list[str]] = defaultdict(list)
    for name, superseded_by in _ALL_DEPRECATED:
        groups[superseded_by].append(name)

    for superseded_by, names in groups.items():
        names_quoted = ", ".join(f"'{n}'" for n in names)
        if superseded_by is not None:
            op.execute(
                f"UPDATE predicate_registry"
                f" SET status = 'deprecated',"
                f"     superseded_by = '{superseded_by}',"
                f"     deprecated_at = '{_DEPRECATED_AT}'"
                f" WHERE name IN ({names_quoted}) AND status = 'active'"
            )
        else:
            op.execute(
                f"UPDATE predicate_registry"
                f" SET status = 'deprecated',"
                f"     deprecated_at = '{_DEPRECATED_AT}'"
                f" WHERE name IN ({names_quoted}) AND status = 'active'"
            )


def downgrade() -> None:
    names_quoted = ", ".join(f"'{name}'" for name, _ in _ALL_DEPRECATED)
    op.execute(
        f"UPDATE predicate_registry"
        f" SET status = 'active', superseded_by = NULL, deprecated_at = NULL"
        f" WHERE name IN ({names_quoted})"
    )
