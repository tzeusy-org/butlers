"""Seed predicate_registry with all domain predicates.

Revision ID: mem_002
Revises: mem_001
Create Date: 2026-03-26 00:00:00.000000

Collapsed seed migration covering original mem_005 (baseline predicates),
mem_007 (meal temporals), mem_009 (health domain), mem_010 (finance domain),
mem_011 (relationship domain), mem_023c (scope backfill), mem_024 (deprecation),
mem_025b (example_json), mem_025c (inverse/symmetric), and mem_026 (preferences).

All predicates are inserted with their final-state values (scope, status,
superseded_by, is_temporal, inverse_of, is_symmetric, example_json).
Uses INSERT ... ON CONFLICT (name) DO NOTHING for idempotency.
"""

from __future__ import annotations

import json

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_002"
down_revision = "mem_001"
branch_labels = None
depends_on = None


def _ej(obj: dict) -> str:
    """Return a SQL-safe JSON literal from a Python dict."""
    return json.dumps(obj).replace("'", "''")


# ---------------------------------------------------------------------------
# Example JSON payloads (from mem_025b)
# ---------------------------------------------------------------------------

_EXAMPLES: dict[str, dict] = {
    # General predicates (005, retained through 024)
    "preference": {
        "content": "prefers dark roast coffee in the morning",
        "metadata": {"category": "food", "strength": "strong"},
    },
    "goal": {
        "content": "run a half-marathon by end of year",
        "metadata": {"deadline": "2026-12-31", "status": "in_progress"},
    },
    "resource": {
        "content": "https://docs.python.org/3/",
        "metadata": {"title": "Python 3 Docs", "tags": ["python", "reference"]},
    },
    "idea": {
        "content": "build a shared grocery list app for the household",
        "metadata": {"tags": ["app", "household"], "priority": "low"},
    },
    "note": {
        "content": "prefers email over phone for scheduling",
    },
    "deadline": {
        "content": "submit tax return",
        "metadata": {"due_at": "2026-04-15T00:00:00Z", "status": "pending"},
    },
    "status": {
        "content": "on_hold",
        "metadata": {"project": "home renovation", "reason": "waiting for contractor"},
    },
    "recommendation": {
        "content": "The Pragmatic Programmer by Hunt and Thomas",
        "metadata": {"category": "book", "rating": 5},
    },
    "birthday": {
        "content": "1985-07-22",
    },
    "lives_in": {
        "content": "Austin, TX",
    },
    # Health predicates (009)
    "measurement_weight": {
        "content": "82.5 kg",
        "metadata": {"value": 82.5, "unit": "kg", "notes": "morning, before breakfast"},
    },
    "measurement_blood_pressure": {
        "content": "118/76 mmHg",
        "metadata": {"value": "118/76", "unit": "mmHg", "notes": "seated, left arm"},
    },
    "measurement_heart_rate": {
        "content": "62 bpm",
        "metadata": {"value": 62, "unit": "bpm", "notes": "resting"},
    },
    "measurement_blood_sugar": {
        "content": "5.4 mmol/L",
        "metadata": {"value": 5.4, "unit": "mmol/L", "notes": "fasting"},
    },
    "measurement_temperature": {
        "content": "37.1 \u00b0C",
        "metadata": {"value": 37.1, "unit": "\u00b0C", "notes": "oral measurement"},
    },
    "symptom": {
        "content": "migraine",
        "metadata": {
            "severity": "moderate",
            "condition_id": None,
            "notes": "started around 2pm, light sensitivity",
        },
    },
    "took_dose": {
        "content": "Lisinopril",
        "metadata": {
            "medication_id": None,
            "skipped": False,
            "notes": "taken with breakfast",
        },
    },
    "medication": {
        "content": "Lisinopril 10mg once daily",
        "metadata": {
            "name": "Lisinopril",
            "dosage": "10mg",
            "frequency": "once daily",
            "schedule": "morning",
            "active": True,
            "notes": "for blood pressure",
        },
    },
    "condition": {
        "content": "Hypertension: managed",
        "metadata": {
            "name": "Hypertension",
            "status": "managed",
            "diagnosed_at": "2022-03-15",
            "notes": "controlled with medication",
        },
    },
    "research": {
        "content": "Magnesium glycinate may reduce migraine frequency",
        "metadata": {
            "title": "Magnesium and migraines",
            "tags": ["migraine", "supplement"],
            "source_url": "https://pubmed.ncbi.nlm.nih.gov/example",
            "condition_id": None,
        },
    },
    # Finance predicates (010)
    "transaction_debit": {
        "content": "Whole Foods Market $47.32 USD",
        "metadata": {
            "merchant": "Whole Foods Market",
            "amount_cents": 4732,
            "currency": "USD",
            "category": "groceries",
        },
    },
    "transaction_credit": {
        "content": "Payroll deposit $3200.00 USD",
        "metadata": {
            "merchant": "Employer Inc",
            "amount_cents": 320000,
            "currency": "USD",
            "category": "income",
        },
    },
    "account": {
        "content": "Chase Checking ****4821",
        "metadata": {
            "institution": "Chase",
            "type": "checking",
            "last_four": "4821",
            "currency": "USD",
        },
    },
    "subscription": {
        "content": "Spotify $9.99/month",
        "metadata": {
            "service": "Spotify",
            "amount_cents": 999,
            "currency": "USD",
            "frequency": "monthly",
            "active": True,
        },
    },
    "bill": {
        "content": "Electric bill $124.50 due 2026-04-05",
        "metadata": {
            "payee": "Austin Energy",
            "amount_cents": 12450,
            "currency": "USD",
            "due_date": "2026-04-05",
        },
    },
    # Relationship predicates (011)
    "interaction": {
        "content": "Caught up over coffee, talked about the new job",
        "metadata": {
            "type": "in_person",
            "direction": "outbound",
            "duration_minutes": 60,
            "notes": "seemed stressed about the move",
        },
    },
    "life_event": {
        "content": "Started new job at Anthropic",
        "metadata": {
            "life_event_type": "career",
            "description": "Joined as a senior engineer in March 2026",
        },
    },
    "contact_note": {
        "content": "Mentioned they prefer morning calls \u2014 avoid after 3pm",
        "metadata": {"emotion": "neutral"},
    },
    "activity": {
        "content": "Completed marathon training plan",
        "metadata": {
            "type": "milestone",
            "entity_type": "goal",
            "entity_id": None,
        },
    },
    "gift": {
        "content": "Handmade pottery mug",
        "metadata": {"occasion": "birthday", "status": "given"},
    },
    "loan": {
        "content": "$200 loan for concert tickets",
        "metadata": {
            "amount_cents": 20000,
            "currency": "USD",
            "direction": "lent",
            "settled": False,
            "settled_at": None,
        },
    },
    "contact_task": {
        "content": "Send thank-you note after dinner",
        "metadata": {
            "description": "Handwritten note for hosting us",
            "completed": False,
            "completed_at": None,
        },
    },
    "reminder": {
        "content": "Call on their birthday",
        "metadata": {
            "type": "birthday",
            "cron": "0 9 22 7 *",
            "due_at": "2026-07-22T09:00:00Z",
            "dismissed": False,
        },
    },
    # Edge predicates (005) - global scope
    "knows": {
        "content": "Alice knows Bob through the running club",
        "metadata": {"since": "2024-01-01", "context": "running club"},
    },
    "works_at": {
        "content": "Alice works at Anthropic as senior engineer",
        "metadata": {"role": "senior engineer", "since": "2026-03-01", "active": True},
    },
    "lives_with": {
        "content": "Alice lives with Bob",
        "metadata": {"since": "2023-06-01"},
    },
    "manages": {
        "content": "Alice manages Bob",
        "metadata": {"since": "2025-01-01", "context": "engineering team"},
    },
    "parent_of": {
        "content": "Alice is parent of Charlie",
        "metadata": {"relationship": "biological"},
    },
    "sibling_of": {
        "content": "Alice is sibling of David",
        "metadata": {"relationship": "full sibling"},
    },
}


def _example_clause(name: str) -> str:
    """Return the example_json SQL value for a predicate, or 'NULL' if none."""
    if name in _EXAMPLES:
        return f"'{_ej(_EXAMPLES[name])}'::jsonb"
    return "NULL"


# Deprecation timestamp (from mem_024)
_DEPRECATED_AT = "2026-03-20 00:00:00+00"


def upgrade() -> None:
    # =========================================================================
    # 1. Active baseline predicates from mem_005 (with scope from 023c)
    #    that were NOT deprecated by mem_024
    # =========================================================================

    # -- Relationship-scoped property predicates (retained from 005) --
    # preference, birthday, lives_in are retained as active
    _insert_predicate(
        "preference",
        None,
        None,
        False,
        False,
        "Food, activities, interests, dislikes",
        "relationship",
        "active",
        None,
        _example_clause("preference"),
    )
    _insert_predicate(
        "birthday",
        "person",
        None,
        False,
        False,
        "Date of birth",
        "relationship",
        "active",
        None,
        _example_clause("birthday"),
    )
    _insert_predicate(
        "lives_in",
        "person",
        None,
        False,
        False,
        "City or location",
        "relationship",
        "active",
        None,
        _example_clause("lives_in"),
    )

    # -- Global-scoped general predicates (retained from 005) --
    _insert_predicate(
        "goal",
        None,
        None,
        False,
        False,
        "Personal or project goal",
        "global",
        "active",
        None,
        _example_clause("goal"),
    )
    _insert_predicate(
        "resource",
        None,
        None,
        False,
        False,
        "Useful link, article, or tool",
        "global",
        "active",
        None,
        _example_clause("resource"),
    )
    _insert_predicate(
        "idea",
        None,
        None,
        False,
        False,
        "Brainstorming note or future plan",
        "global",
        "active",
        None,
        _example_clause("idea"),
    )
    _insert_predicate(
        "note",
        None,
        None,
        False,
        False,
        "General observation or reminder",
        "global",
        "active",
        None,
        _example_clause("note"),
    )
    _insert_predicate(
        "deadline",
        None,
        None,
        False,
        False,
        "Time-sensitive task or date",
        "global",
        "active",
        None,
        _example_clause("deadline"),
    )
    _insert_predicate(
        "status",
        None,
        None,
        False,
        False,
        "Current state of a project or activity",
        "global",
        "active",
        None,
        _example_clause("status"),
    )
    _insert_predicate(
        "recommendation",
        None,
        None,
        False,
        False,
        "Recommendation for a place, book, tool, etc.",
        "global",
        "active",
        None,
        _example_clause("recommendation"),
    )

    # -- Global-scoped edge predicates (retained from 005) --
    # knows, lives_with, sibling_of: is_symmetric=true (from 025c)
    _insert_predicate(
        "knows",
        "person",
        "person",
        True,
        False,
        "Social connection between two people",
        "global",
        "active",
        None,
        _example_clause("knows"),
        is_symmetric=True,
    )
    _insert_predicate(
        "works_at",
        "person",
        "organization",
        True,
        False,
        "Employment relationship",
        "global",
        "active",
        None,
        _example_clause("works_at"),
    )
    _insert_predicate(
        "lives_with",
        "person",
        "person",
        True,
        False,
        "Cohabitation relationship",
        "global",
        "active",
        None,
        _example_clause("lives_with"),
        is_symmetric=True,
    )
    # manages: inverse_of='managed_by' (from 025c)
    _insert_predicate(
        "manages",
        "person",
        "person",
        True,
        False,
        "Management relationship",
        "global",
        "active",
        None,
        _example_clause("manages"),
        inverse_of="managed_by",
    )
    # parent_of: inverse_of='child_of' (from 025c)
    _insert_predicate(
        "parent_of",
        "person",
        "person",
        True,
        False,
        "Parent-child relationship",
        "global",
        "active",
        None,
        _example_clause("parent_of"),
        inverse_of="child_of",
    )
    _insert_predicate(
        "sibling_of",
        "person",
        "person",
        True,
        False,
        "Sibling relationship",
        "global",
        "active",
        None,
        _example_clause("sibling_of"),
        is_symmetric=True,
    )

    # -- Inverse predicates added by 025c --
    _insert_predicate(
        "child_of",
        "person",
        "person",
        True,
        False,
        "Inverse of parent_of",
        "global",
        "active",
        None,
        "NULL",
        inverse_of="parent_of",
    )
    _insert_predicate(
        "managed_by",
        "person",
        "person",
        True,
        False,
        "Inverse of manages",
        "global",
        "active",
        None,
        "NULL",
        inverse_of="manages",
    )

    # =========================================================================
    # 2. Deprecated baseline predicates from mem_005 (deprecated by mem_024)
    # =========================================================================

    # -- Health domain deprecated predicates --
    _insert_predicate(
        "medication_frequency",
        None,
        None,
        False,
        False,
        "How often a medication is taken",
        "health",
        "deprecated",
        "medication",
        "NULL",
    )
    _insert_predicate(
        "dosage",
        None,
        None,
        False,
        False,
        "Amount per dose",
        "health",
        "deprecated",
        "medication",
        "NULL",
    )
    _insert_predicate(
        "condition_status",
        None,
        None,
        False,
        False,
        "Active, managed, or resolved condition",
        "health",
        "deprecated",
        "condition",
        "NULL",
    )
    _insert_predicate(
        "symptom_pattern",
        None,
        None,
        False,
        False,
        "Recurring symptoms or triggers",
        "health",
        "deprecated",
        "symptom",
        "NULL",
    )
    _insert_predicate(
        "symptom_trigger",
        None,
        None,
        False,
        False,
        "What causes or worsens a symptom",
        "health",
        "deprecated",
        "symptom",
        "NULL",
    )
    _insert_predicate(
        "measurement_baseline",
        None,
        None,
        False,
        False,
        "Typical or target measurement values",
        "health",
        "deprecated",
        "measurement_weight",
        "NULL",
    )
    _insert_predicate(
        "dietary_restriction",
        "person",
        None,
        False,
        False,
        "Food allergies or dietary restrictions",
        "health",
        "deprecated",
        "condition",
        "NULL",
    )
    _insert_predicate(
        "food_allergy",
        "person",
        None,
        False,
        False,
        "Food allergy or intolerance",
        "health",
        "deprecated",
        "condition",
        "NULL",
    )
    _insert_predicate(
        "allergy",
        "person",
        None,
        False,
        False,
        "Medication or substance allergy",
        "health",
        "deprecated",
        "condition",
        "NULL",
    )
    _insert_predicate(
        "exercise_routine",
        "person",
        None,
        False,
        False,
        "Regular physical activity",
        "health",
        "deprecated",
        None,
        "NULL",
    )
    _insert_predicate(
        "doctor_name",
        "person",
        None,
        False,
        False,
        "Healthcare provider name",
        "health",
        "deprecated",
        None,
        "NULL",
    )
    _insert_predicate(
        "pharmacy",
        "person",
        None,
        False,
        False,
        "Preferred pharmacy location",
        "health",
        "deprecated",
        None,
        "NULL",
    )

    # -- Relationship domain deprecated predicates --
    _insert_predicate(
        "relationship_to_user",
        "person",
        None,
        False,
        False,
        "Relationship label to the user",
        "relationship",
        "deprecated",
        None,
        "NULL",
    )
    _insert_predicate(
        "contact_phone",
        "person",
        None,
        False,
        False,
        "Phone number",
        "relationship",
        "deprecated",
        None,
        "NULL",
    )
    _insert_predicate(
        "contact_email",
        "person",
        None,
        False,
        False,
        "Email address",
        "relationship",
        "deprecated",
        None,
        "NULL",
    )
    _insert_predicate(
        "workplace",
        "person",
        None,
        False,
        False,
        "Company or organization name",
        "relationship",
        "deprecated",
        "works_at",
        "NULL",
    )
    _insert_predicate(
        "relationship_status",
        "person",
        None,
        False,
        False,
        "Married, single, dating, etc.",
        "relationship",
        "deprecated",
        None,
        "NULL",
    )
    _insert_predicate(
        "children",
        "person",
        None,
        False,
        False,
        "Names and ages of children",
        "relationship",
        "deprecated",
        "parent_of",
        "NULL",
    )
    _insert_predicate(
        "nickname",
        "person",
        None,
        False,
        False,
        "Preferred name or alias",
        "relationship",
        "deprecated",
        None,
        "NULL",
    )
    _insert_predicate(
        "travel_intent",
        "person",
        None,
        False,
        False,
        "Planned or potential travel",
        "relationship",
        "deprecated",
        "life_event",
        "NULL",
    )
    _insert_predicate(
        "anniversary",
        "person",
        None,
        False,
        False,
        "Date-based milestone",
        "relationship",
        "deprecated",
        "life_event",
        "NULL",
    )
    _insert_predicate(
        "current_interest",
        None,
        None,
        False,
        False,
        "Hobbies, projects, topics being explored",
        "relationship",
        "deprecated",
        "activity",
        "NULL",
    )
    _insert_predicate(
        "current_project",
        None,
        None,
        False,
        False,
        "Active project or work initiative",
        "relationship",
        "deprecated",
        "activity",
        "NULL",
    )

    # =========================================================================
    # 3. Meal temporal predicates from mem_007
    # =========================================================================
    _insert_predicate(
        "meal_breakfast",
        "person",
        None,
        False,
        True,
        "Breakfast meal eaten at specific time",
        "global",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "meal_lunch",
        "person",
        None,
        False,
        True,
        "Lunch meal eaten at specific time",
        "global",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "meal_dinner",
        "person",
        None,
        False,
        True,
        "Dinner meal eaten at specific time",
        "global",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "meal_snack",
        "person",
        None,
        False,
        True,
        "Snack eaten at specific time",
        "global",
        "active",
        None,
        "NULL",
    )

    # =========================================================================
    # 4. Health domain predicates from mem_009
    # =========================================================================

    # Temporal health predicates
    _insert_predicate(
        "measurement_weight",
        None,
        None,
        False,
        True,
        "Health measurement: body weight. Metadata: {value, unit, notes}.",
        "health",
        "active",
        None,
        _example_clause("measurement_weight"),
    )
    _insert_predicate(
        "measurement_blood_pressure",
        None,
        None,
        False,
        True,
        "Health measurement: blood pressure. Metadata: {value, unit, notes}.",
        "health",
        "active",
        None,
        _example_clause("measurement_blood_pressure"),
    )
    _insert_predicate(
        "measurement_heart_rate",
        None,
        None,
        False,
        True,
        "Health measurement: heart rate. Metadata: {value, unit, notes}.",
        "health",
        "active",
        None,
        _example_clause("measurement_heart_rate"),
    )
    _insert_predicate(
        "measurement_blood_sugar",
        None,
        None,
        False,
        True,
        "Health measurement: blood sugar / glucose. Metadata: {value, unit, notes}.",
        "health",
        "active",
        None,
        _example_clause("measurement_blood_sugar"),
    )
    _insert_predicate(
        "measurement_temperature",
        None,
        None,
        False,
        True,
        "Health measurement: body temperature. Metadata: {value, unit, notes}.",
        "health",
        "active",
        None,
        _example_clause("measurement_temperature"),
    )
    _insert_predicate(
        "symptom",
        None,
        None,
        False,
        True,
        "A symptom occurrence. Content = symptom name. Metadata: {severity, condition_id, notes}.",
        "health",
        "active",
        None,
        _example_clause("symptom"),
    )
    _insert_predicate(
        "took_dose",
        None,
        None,
        False,
        True,
        "A medication dose event. Content = medication name. "
        "Metadata: {medication_id, skipped, notes}.",
        "health",
        "active",
        None,
        _example_clause("took_dose"),
    )

    # Property health predicates (re-seeded in 009 with richer descriptions)
    _insert_predicate(
        "medication",
        None,
        None,
        False,
        False,
        "Current medication. Content = ''{name} {dosage} {frequency}''. "
        "Metadata: {name, dosage, frequency, schedule, active, notes}.",
        "health",
        "active",
        None,
        _example_clause("medication"),
    )
    _insert_predicate(
        "condition",
        None,
        None,
        False,
        False,
        "A health condition. Content = ''{name}: {status}''. "
        "Metadata: {name, status, diagnosed_at, notes}.",
        "health",
        "active",
        None,
        _example_clause("condition"),
    )
    _insert_predicate(
        "research",
        None,
        None,
        False,
        False,
        "A health research note. Content = research text. "
        "Metadata: {title, tags, source_url, condition_id}.",
        "health",
        "active",
        None,
        _example_clause("research"),
    )

    # =========================================================================
    # 5. Finance domain predicates from mem_010
    # =========================================================================
    _insert_predicate(
        "transaction_debit",
        "person",
        None,
        False,
        True,
        "Debit (money-out) transaction event, content = merchant amount currency",
        "finance",
        "active",
        None,
        _example_clause("transaction_debit"),
    )
    _insert_predicate(
        "transaction_credit",
        "person",
        None,
        False,
        True,
        "Credit (money-in / refund) transaction event, content = merchant amount currency",
        "finance",
        "active",
        None,
        _example_clause("transaction_credit"),
    )
    _insert_predicate(
        "account",
        "person",
        None,
        False,
        False,
        "Financial account, content = institution type ****last_four",
        "finance",
        "active",
        None,
        _example_clause("account"),
    )
    _insert_predicate(
        "subscription",
        "person",
        None,
        False,
        False,
        "Recurring service subscription, content = service amount/frequency",
        "finance",
        "active",
        None,
        _example_clause("subscription"),
    )
    _insert_predicate(
        "bill",
        "person",
        None,
        False,
        False,
        "Payable bill obligation, content = payee amount due due_date",
        "finance",
        "active",
        None,
        _example_clause("bill"),
    )

    # =========================================================================
    # 6. Relationship domain predicates from mem_011
    # =========================================================================

    # Temporal relationship predicates
    _insert_predicate(
        "interaction",
        "person",
        None,
        False,
        True,
        "Interaction with a contact. Content = summary. "
        "Metadata: {type, notes, direction, duration_minutes}. valid_at = occurred_at.",
        "relationship",
        "active",
        None,
        _example_clause("interaction"),
    )
    _insert_predicate(
        "life_event",
        "person",
        None,
        False,
        True,
        "Significant life event for a contact. Content = summary. "
        "Metadata: {life_event_type, description}. valid_at = happened_at.",
        "relationship",
        "active",
        None,
        _example_clause("life_event"),
    )
    _insert_predicate(
        "contact_note",
        "person",
        None,
        False,
        True,
        "Note about a contact (append-only). Content = note text. "
        "Metadata: {emotion}. valid_at = created_at.",
        "relationship",
        "active",
        None,
        _example_clause("contact_note"),
    )
    _insert_predicate(
        "activity",
        "person",
        None,
        False,
        True,
        "Activity feed entry for a contact. Content = description. "
        "Metadata: {type, entity_type, entity_id}. valid_at = created_at.",
        "relationship",
        "active",
        None,
        _example_clause("activity"),
    )

    # Property relationship predicates
    _insert_predicate(
        "gift",
        "person",
        None,
        False,
        False,
        "Gift tracked for a contact. Content = description. "
        "Metadata: {occasion, status}. Supersession per contact entity.",
        "relationship",
        "active",
        None,
        _example_clause("gift"),
    )
    _insert_predicate(
        "loan",
        "person",
        None,
        False,
        False,
        "Loan tracked for a contact. Content = description. "
        "Metadata: {amount_cents, currency, direction, settled, settled_at}. "
        "Supersession per contact entity + subject key.",
        "relationship",
        "active",
        None,
        _example_clause("loan"),
    )
    _insert_predicate(
        "contact_task",
        "person",
        None,
        False,
        False,
        "Task scoped to a contact. Content = title. "
        "Metadata: {description, completed, completed_at}. "
        "Supersession per contact entity + subject key.",
        "relationship",
        "active",
        None,
        _example_clause("contact_task"),
    )
    _insert_predicate(
        "reminder",
        "person",
        None,
        False,
        False,
        "Reminder for a contact. Content = message. "
        "Metadata: {type, cron, due_at, dismissed}. "
        "Supersession per contact entity + subject key.",
        "relationship",
        "active",
        None,
        _example_clause("reminder"),
    )

    # =========================================================================
    # 7. Preferences predicates from mem_026
    # =========================================================================

    # Travel domain
    _insert_predicate(
        "preferences:travel_flight_seat",
        "person",
        None,
        False,
        False,
        "User preference: in-flight seat type (aisle, window, or middle).",
        "travel",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:travel_flight_class",
        "person",
        None,
        False,
        False,
        "User preference: flight cabin class (economy, business, or first).",
        "travel",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:travel_hotel_type",
        "person",
        None,
        False,
        False,
        "User preference: hotel style (boutique, chain, budget, luxury, etc.).",
        "travel",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:travel_airline",
        "person",
        None,
        False,
        False,
        "User preference: preferred airline or alliance.",
        "travel",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:travel_meal",
        "person",
        None,
        False,
        False,
        "User preference: in-flight meal type (vegetarian, kosher, halal, etc.).",
        "travel",
        "active",
        None,
        "NULL",
    )

    # Health domain preferences
    _insert_predicate(
        "preferences:health_dietary_restriction",
        "person",
        None,
        False,
        False,
        "User preference: foods or ingredients to avoid (allergies, intolerances, or ethical).",
        "health",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:health_dietary_preference",
        "person",
        None,
        False,
        False,
        "User preference: food preferences (cuisines, flavors, dietary style).",
        "health",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:health_exercise_preference",
        "person",
        None,
        False,
        False,
        "User preference: preferred exercise types or workout styles.",
        "health",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:health_measurement_unit",
        "person",
        None,
        False,
        False,
        "User preference: measurement system for health metrics (metric or imperial).",
        "health",
        "active",
        None,
        "NULL",
    )

    # Finance domain preferences
    _insert_predicate(
        "preferences:finance_currency",
        "person",
        None,
        False,
        False,
        "User preference: preferred display currency (e.g. USD, EUR, GBP).",
        "finance",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:finance_budget_period",
        "person",
        None,
        False,
        False,
        "User preference: budget cycle period (weekly, monthly, or yearly).",
        "finance",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:finance_rounding",
        "person",
        None,
        False,
        False,
        "User preference: rounding mode for monetary amounts (up, down, nearest).",
        "finance",
        "active",
        None,
        "NULL",
    )

    # Relationship domain preferences
    _insert_predicate(
        "preferences:relationship_communication_style",
        "person",
        None,
        False,
        False,
        "User preference: preferred interpersonal communication style (formal, casual, etc.).",
        "relationship",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:relationship_contact_frequency",
        "person",
        None,
        False,
        False,
        "User preference: how often to reach out to contacts (weekly, monthly, etc.).",
        "relationship",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:relationship_birthday_reminder_days",
        "person",
        None,
        False,
        False,
        "User preference: how many days before a birthday to send a reminder.",
        "relationship",
        "active",
        None,
        "NULL",
    )

    # Home domain preferences
    _insert_predicate(
        "preferences:home_temperature_unit",
        "person",
        None,
        False,
        False,
        "User preference: temperature display unit (celsius or fahrenheit).",
        "home",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:home_comfort_temperature",
        "person",
        None,
        False,
        False,
        "User preference: preferred indoor temperature (numeric value with unit).",
        "home",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:home_wake_time",
        "person",
        None,
        False,
        False,
        "User preference: usual wake-up time (ISO-8601 time, e.g. 07:00).",
        "home",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:home_sleep_time",
        "person",
        None,
        False,
        False,
        "User preference: usual bedtime (ISO-8601 time, e.g. 23:00).",
        "home",
        "active",
        None,
        "NULL",
    )

    # General domain preferences (global scope)
    _insert_predicate(
        "preferences:general_communication_style",
        "person",
        None,
        False,
        False,
        "User preference: preferred communication style with the butler "
        "(formal, casual, concise, or detailed).",
        "global",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:general_language",
        "person",
        None,
        False,
        False,
        "User preference: preferred language for responses.",
        "global",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:general_timezone",
        "person",
        None,
        False,
        False,
        "User preference: preferred timezone (IANA tz name, e.g. America/New_York).",
        "global",
        "active",
        None,
        "NULL",
    )
    _insert_predicate(
        "preferences:general_name",
        "person",
        None,
        False,
        False,
        "User preference: preferred name or nickname to be addressed by.",
        "global",
        "active",
        None,
        "NULL",
    )


def _insert_predicate(
    name: str,
    expected_subject_type: str | None,
    expected_object_type: str | None,
    is_edge: bool,
    is_temporal: bool,
    description: str,
    scope: str,
    status: str,
    superseded_by: str | None,
    example_json_sql: str,
    *,
    inverse_of: str | None = None,
    is_symmetric: bool = False,
) -> None:
    """Insert a single predicate with ON CONFLICT DO NOTHING."""
    subj = f"'{expected_subject_type}'" if expected_subject_type else "NULL"
    obj = f"'{expected_object_type}'" if expected_object_type else "NULL"
    sup = f"'{superseded_by}'" if superseded_by else "NULL"
    inv = f"'{inverse_of}'" if inverse_of else "NULL"
    dep_at = f"'{_DEPRECATED_AT}'" if status == "deprecated" else "NULL"
    # Escape single quotes in description for SQL
    desc_escaped = description.replace("'", "''")

    op.execute(
        f"INSERT INTO predicate_registry"
        f" (name, expected_subject_type, expected_object_type, is_edge, is_temporal,"
        f"  description, scope, status, superseded_by, deprecated_at,"
        f"  inverse_of, is_symmetric, example_json)"
        f" VALUES"
        f" ('{name}', {subj}, {obj}, {is_edge}, {is_temporal},"
        f"  '{desc_escaped}', '{scope}', '{status}', {sup}, {dep_at},"
        f"  {inv}, {is_symmetric}, {example_json_sql})"
        f" ON CONFLICT (name) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM predicate_registry")
