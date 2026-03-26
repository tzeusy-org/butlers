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

Revision ID: mem_026
Revises: mem_025c
Create Date: 2026-03-26 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_026"
down_revision = "mem_025c"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Preference predicate definitions
# ---------------------------------------------------------------------------

_PREFERENCES_PREDICATES: list[tuple[str, str]] = [
    # Travel domain
    (
        "preferences:travel_flight_seat",
        "User preference: in-flight seat type (aisle, window, or middle).",
    ),
    (
        "preferences:travel_flight_class",
        "User preference: flight cabin class (economy, business, or first).",
    ),
    (
        "preferences:travel_hotel_type",
        "User preference: hotel style (boutique, chain, budget, luxury, etc.).",
    ),
    (
        "preferences:travel_airline",
        "User preference: preferred airline or alliance.",
    ),
    (
        "preferences:travel_meal",
        "User preference: in-flight meal type (vegetarian, kosher, halal, etc.).",
    ),
    # Health domain
    (
        "preferences:health_dietary_restriction",
        "User preference: foods or ingredients to avoid (allergies, intolerances, or ethical).",
    ),
    (
        "preferences:health_dietary_preference",
        "User preference: food preferences (cuisines, flavors, dietary style).",
    ),
    (
        "preferences:health_exercise_preference",
        "User preference: preferred exercise types or workout styles.",
    ),
    (
        "preferences:health_measurement_unit",
        "User preference: measurement system for health metrics (metric or imperial).",
    ),
    # Finance domain
    (
        "preferences:finance_currency",
        "User preference: preferred display currency (e.g. USD, EUR, GBP).",
    ),
    (
        "preferences:finance_budget_period",
        "User preference: budget cycle period (weekly, monthly, or yearly).",
    ),
    (
        "preferences:finance_rounding",
        "User preference: rounding mode for monetary amounts (up, down, nearest).",
    ),
    # Relationship domain
    (
        "preferences:relationship_communication_style",
        "User preference: preferred interpersonal communication style (formal, casual, etc.).",
    ),
    (
        "preferences:relationship_contact_frequency",
        "User preference: how often to reach out to contacts (weekly, monthly, etc.).",
    ),
    (
        "preferences:relationship_birthday_reminder_days",
        "User preference: how many days before a birthday to send a reminder.",
    ),
    # Home domain
    (
        "preferences:home_temperature_unit",
        "User preference: temperature display unit (celsius or fahrenheit).",
    ),
    (
        "preferences:home_comfort_temperature",
        "User preference: preferred indoor temperature (numeric value with unit).",
    ),
    (
        "preferences:home_wake_time",
        "User preference: usual wake-up time (ISO-8601 time, e.g. 07:00).",
    ),
    (
        "preferences:home_sleep_time",
        "User preference: usual bedtime (ISO-8601 time, e.g. 23:00).",
    ),
    # General domain
    (
        "preferences:general_communication_style",
        "User preference: preferred communication style with the butler "
        "(formal, casual, concise, or detailed).",
    ),
    (
        "preferences:general_language",
        "User preference: preferred language for responses.",
    ),
    (
        "preferences:general_timezone",
        "User preference: preferred timezone (IANA tz name, e.g. America/New_York).",
    ),
    (
        "preferences:general_name",
        "User preference: preferred name or nickname to be addressed by.",
    ),
]


def upgrade() -> None:
    values = ", ".join(
        f"('{name}', 'person', false, false, '{desc}')" for name, desc in _PREFERENCES_PREDICATES
    )
    op.execute(
        "INSERT INTO predicate_registry"
        " (name, expected_subject_type, is_temporal, is_edge, description) VALUES "
        + values
        + " ON CONFLICT (name) DO NOTHING"
    )


def downgrade() -> None:
    names = ", ".join(f"'{name}'" for name, _ in _PREFERENCES_PREDICATES)
    op.execute(f"DELETE FROM predicate_registry WHERE name IN ({names})")
