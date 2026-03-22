"""predicate_scope — mem_023c

Add a ``scope`` column to ``predicate_registry`` for domain namespacing.

The scope aligns with fact-level scope values and serves three purposes:
  1. Namespace — avoids name collisions across butler domains
  2. UI grouping — group predicates by domain in dashboards
  3. Search filter — ``memory_predicate_search(scope=...)`` filters to a domain

Valid scope values (matches fact-level scope):
  health, relationship, finance, home, global

Backfill assigns each existing predicate to its originating domain.
Predicates from 005 that were comment-labelled "relationship domain" → relationship,
"health domain" → health, "general domain" → global, edge predicates → global.
Predicates added by 009 (health measurements) → health.
Predicates added by 010 (finance transactions/accounts) → finance.
Predicates added by 011 (contact interactions, gifts, loans) → relationship.

Revision ID: mem_023c
Revises: mem_023b
Create Date: 2026-03-20 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_023c"
down_revision = "mem_023b"
branch_labels = None
depends_on = None

# Predicate → scope backfill mapping.
# Predicates not listed here fall back to the DEFAULT 'global'.
_BACKFILL: dict[str, str] = {
    # --- relationship domain (from 005 + 011) ---
    "relationship_to_user": "relationship",
    "birthday": "relationship",
    "anniversary": "relationship",
    "preference": "relationship",
    "current_interest": "relationship",
    "contact_phone": "relationship",
    "contact_email": "relationship",
    "workplace": "relationship",
    "lives_in": "relationship",
    "relationship_status": "relationship",
    "children": "relationship",
    "nickname": "relationship",
    "food_allergy": "relationship",
    "current_project": "relationship",
    "travel_intent": "relationship",
    # Edge predicates — global per spec (available to all butlers, graph topology)
    "knows": "global",
    "works_at": "global",
    "lives_with": "global",
    "manages": "global",
    "parent_of": "global",
    "sibling_of": "global",
    # From migration 011
    "interaction": "relationship",
    "life_event": "relationship",
    "contact_note": "relationship",
    "activity": "relationship",
    "gift": "relationship",
    "loan": "relationship",
    "contact_task": "relationship",
    "reminder": "relationship",
    # --- health domain (from 005 + 009) ---
    "medication": "health",
    "medication_frequency": "health",
    "dosage": "health",
    "condition_status": "health",
    "symptom_pattern": "health",
    "symptom_trigger": "health",
    "measurement_baseline": "health",
    "dietary_restriction": "health",
    "exercise_routine": "health",
    "doctor_name": "health",
    "pharmacy": "health",
    "allergy": "health",
    # From migration 009
    "measurement_weight": "health",
    "measurement_blood_pressure": "health",
    "measurement_heart_rate": "health",
    "measurement_blood_sugar": "health",
    "measurement_temperature": "health",
    "symptom": "health",
    "took_dose": "health",
    "condition": "health",
    "research": "health",
    # --- finance domain (from 010) ---
    "transaction_debit": "finance",
    "transaction_credit": "finance",
    "account": "finance",
    "subscription": "finance",
    "bill": "finance",
    # --- global domain (general predicates from 005) ---
    "goal": "global",
    "resource": "global",
    "idea": "global",
    "note": "global",
    "deadline": "global",
    "status": "global",
    "recommendation": "global",
}


def upgrade() -> None:
    # Add scope column with DEFAULT 'global' — non-destructive, all existing rows
    # get 'global' until the backfill UPDATE runs below.
    op.execute("""
        ALTER TABLE predicate_registry
        ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'global'
        CHECK (scope IN ('global', 'health', 'relationship', 'finance', 'home'))
    """)

    # Backfill known predicates to their correct domain.
    # Group by scope value to minimise the number of UPDATE statements.
    by_scope: dict[str, list[str]] = {}
    for name, scope in _BACKFILL.items():
        by_scope.setdefault(scope, []).append(name)

    for scope_val, names in by_scope.items():
        quoted = ", ".join(f"'{n}'" for n in names)
        op.execute(f"UPDATE predicate_registry SET scope = '{scope_val}' WHERE name IN ({quoted})")

    # Index for scope-filtered queries (used by predicate_search).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_predicate_registry_scope
        ON predicate_registry (scope)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_predicate_registry_scope")
    op.execute("ALTER TABLE predicate_registry DROP COLUMN IF EXISTS scope")
