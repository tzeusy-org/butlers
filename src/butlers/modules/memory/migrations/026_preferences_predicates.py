"""preferences_predicates

Seeds standard user-preference predicates into predicate_registry.

All preference predicates use the naming format ``preferences:<domain>_<name>``.
Each is a property predicate (is_temporal=false, is_edge=false) with
expected_subject_type='person'.

Domains seeded:
  travel       — flight seat/class, hotel type, airline, meal
  health       — dietary restrictions/preferences, exercise, measurement units
  finance      — currency, budget period, rounding
  relationship — communication style, contact frequency, birthday reminder days
  home         — temperature unit, comfort temperature, wake/sleep times
  general      — communication style, language, timezone, preferred name

Scope mapping:
  preferences:general_*     → 'global'
  preferences:<domain>_*    → '<domain>'  (travel, health, finance, relationship, home)

Because ``predicate_registry.scope`` has a CHECK constraint (added in mem_023c)
that does not include 'travel', this migration first extends the constraint to
include 'travel' before inserting the travel-domain predicates.  The downgrade
restores the original constraint values after removing the seeded rows.

Revision ID: mem_026
Revises: mem_025c
Create Date: 2026-03-26 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "mem_026"
down_revision = "mem_025c"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Preference predicate definitions: (name, scope, description)
# ---------------------------------------------------------------------------

_PREFERENCES_PREDICATES: list[tuple[str, str, str]] = [
    # Travel domain
    (
        "preferences:travel_flight_seat",
        "travel",
        "User preference: in-flight seat type (aisle, window, or middle).",
    ),
    (
        "preferences:travel_flight_class",
        "travel",
        "User preference: flight cabin class (economy, business, or first).",
    ),
    (
        "preferences:travel_hotel_type",
        "travel",
        "User preference: hotel style (boutique, chain, budget, luxury, etc.).",
    ),
    (
        "preferences:travel_airline",
        "travel",
        "User preference: preferred airline or alliance.",
    ),
    (
        "preferences:travel_meal",
        "travel",
        "User preference: in-flight meal type (vegetarian, kosher, halal, etc.).",
    ),
    # Health domain
    (
        "preferences:health_dietary_restriction",
        "health",
        "User preference: foods or ingredients to avoid (allergies, intolerances, or ethical).",
    ),
    (
        "preferences:health_dietary_preference",
        "health",
        "User preference: food preferences (cuisines, flavors, dietary style).",
    ),
    (
        "preferences:health_exercise_preference",
        "health",
        "User preference: preferred exercise types or workout styles.",
    ),
    (
        "preferences:health_measurement_unit",
        "health",
        "User preference: measurement system for health metrics (metric or imperial).",
    ),
    # Finance domain
    (
        "preferences:finance_currency",
        "finance",
        "User preference: preferred display currency (e.g. USD, EUR, GBP).",
    ),
    (
        "preferences:finance_budget_period",
        "finance",
        "User preference: budget cycle period (weekly, monthly, or yearly).",
    ),
    (
        "preferences:finance_rounding",
        "finance",
        "User preference: rounding mode for monetary amounts (up, down, nearest).",
    ),
    # Relationship domain
    (
        "preferences:relationship_communication_style",
        "relationship",
        "User preference: preferred interpersonal communication style (formal, casual, etc.).",
    ),
    (
        "preferences:relationship_contact_frequency",
        "relationship",
        "User preference: how often to reach out to contacts (weekly, monthly, etc.).",
    ),
    (
        "preferences:relationship_birthday_reminder_days",
        "relationship",
        "User preference: how many days before a birthday to send a reminder.",
    ),
    # Home domain
    (
        "preferences:home_temperature_unit",
        "home",
        "User preference: temperature display unit (celsius or fahrenheit).",
    ),
    (
        "preferences:home_comfort_temperature",
        "home",
        "User preference: preferred indoor temperature (numeric value with unit).",
    ),
    (
        "preferences:home_wake_time",
        "home",
        "User preference: usual wake-up time (ISO-8601 time, e.g. 07:00).",
    ),
    (
        "preferences:home_sleep_time",
        "home",
        "User preference: usual bedtime (ISO-8601 time, e.g. 23:00).",
    ),
    # General domain → maps to 'global' scope per _derive_scope convention
    (
        "preferences:general_communication_style",
        "global",
        "User preference: preferred communication style with the butler "
        "(formal, casual, concise, or detailed).",
    ),
    (
        "preferences:general_language",
        "global",
        "User preference: preferred language for responses.",
    ),
    (
        "preferences:general_timezone",
        "global",
        "User preference: preferred timezone (IANA tz name, e.g. America/New_York).",
    ),
    (
        "preferences:general_name",
        "global",
        "User preference: preferred name or nickname to be addressed by.",
    ),
]

# ---------------------------------------------------------------------------
# Scope constraint helpers
# ---------------------------------------------------------------------------

# The scope CHECK constraint was introduced without an explicit name in
# mem_023c; PostgreSQL auto-assigns a name based on table+column.  We locate
# the constraint by querying pg_constraint so the logic is robust across
# schemas and PostgreSQL versions.
_SCOPE_CHECK_FIND = """
    SELECT conname
    FROM   pg_constraint
    WHERE  conrelid = 'predicate_registry'::regclass
      AND  contype  = 'c'
      AND  conname  LIKE '%scope%'
    LIMIT 1
"""

_SCOPE_VALUES_EXPANDED = "('global', 'health', 'relationship', 'finance', 'home', 'travel')"
_SCOPE_VALUES_ORIGINAL = "('global', 'health', 'relationship', 'finance', 'home')"


def _alter_scope_constraint(new_values: str) -> None:
    """Replace the scope CHECK constraint with an updated IN-list."""
    conn = op.get_bind()
    row = conn.execute(text(_SCOPE_CHECK_FIND)).fetchone()
    if row:
        constraint_name = row[0]
        op.execute(f"ALTER TABLE predicate_registry DROP CONSTRAINT IF EXISTS {constraint_name}")
    op.execute(
        f"ALTER TABLE predicate_registry"
        f" ADD CONSTRAINT predicate_registry_scope_check"
        f" CHECK (scope IN {new_values})"
    )


def upgrade() -> None:
    # 1. Extend scope CHECK to include 'travel' before inserting travel predicates.
    _alter_scope_constraint(_SCOPE_VALUES_EXPANDED)

    # 2. Seed all preference predicates — idempotent via ON CONFLICT DO NOTHING.
    values = ", ".join(
        f"('{name}', '{scope}', 'person', false, false, '{desc}')"
        for name, scope, desc in _PREFERENCES_PREDICATES
    )
    op.execute(
        "INSERT INTO predicate_registry"
        " (name, scope, expected_subject_type, is_temporal, is_edge, description) VALUES "
        + values
        + " ON CONFLICT (name) DO NOTHING"
    )


def downgrade() -> None:
    # 1. Remove seeded predicates.
    names = ", ".join(f"'{name}'" for name, _, _ in _PREFERENCES_PREDICATES)
    op.execute(f"DELETE FROM predicate_registry WHERE name IN ({names})")

    # 2. Restore original scope CHECK constraint (without 'travel').
    _alter_scope_constraint(_SCOPE_VALUES_ORIGINAL)
