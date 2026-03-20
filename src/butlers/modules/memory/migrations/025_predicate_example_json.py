"""predicate_example_json — mem_025

Add ``example_json`` JSONB column to ``predicate_registry`` so LLMs can see
concrete content/metadata templates when discovering predicates via search.

Changes:
  1. Add ``example_json JSONB`` column (nullable; NULL = no example provided).
  2. Backfill realistic examples for all domain predicates:
     - General predicates (seeded in 005, retained through 024)
     - Health predicates (seeded in 009)
     - Finance predicates (seeded in 010)
     - Relationship predicates (seeded in 011)
     - Edge predicates (seeded in 005)

Example format:
  {
    "content": "<example content string>",
    "metadata": { <example metadata keys/values> }
  }

For predicates without structured metadata, ``metadata`` may be omitted.

Revision ID: mem_025
Revises: mem_024
Create Date: 2026-03-20 00:00:00.000000

"""

from __future__ import annotations

import json

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_025"
down_revision = "mem_024"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Example payloads, keyed by predicate name.
# ---------------------------------------------------------------------------

_EXAMPLES: dict[str, dict] = {
    # -----------------------------------------------------------------------
    # General predicates (005, retained through 024)
    # -----------------------------------------------------------------------
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
    # -----------------------------------------------------------------------
    # Health predicates (009)
    # -----------------------------------------------------------------------
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
        "content": "37.1 °C",
        "metadata": {"value": 37.1, "unit": "°C", "notes": "oral measurement"},
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
    # -----------------------------------------------------------------------
    # Finance predicates (010)
    # -----------------------------------------------------------------------
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
    # -----------------------------------------------------------------------
    # Relationship predicates (011)
    # -----------------------------------------------------------------------
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
        "content": "Mentioned they prefer morning calls — avoid after 3pm",
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
    # -----------------------------------------------------------------------
    # Edge predicates (005) — global scope, relate two entities
    # -----------------------------------------------------------------------
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


def _escape_json(obj: dict) -> str:
    """Return a SQL-safe JSON literal from a Python dict.

    Uses json.dumps to produce a valid JSON string, then escapes any single
    quotes by doubling them for safe embedding in a SQL string literal.
    """
    return json.dumps(obj).replace("'", "''")


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Add example_json JSONB column (nullable; existing rows get NULL).
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE predicate_registry
        ADD COLUMN IF NOT EXISTS example_json JSONB
    """)

    # -------------------------------------------------------------------------
    # 2. Backfill example_json for all known domain predicates.
    #    Group into batches to keep individual SQL statements manageable.
    # -------------------------------------------------------------------------
    for name, example in _EXAMPLES.items():
        json_literal = _escape_json(example)
        op.execute(
            f"UPDATE predicate_registry SET example_json = '{json_literal}' WHERE name = '{name}'"
        )


def downgrade() -> None:
    op.execute("""
        ALTER TABLE predicate_registry
        DROP COLUMN IF EXISTS example_json
    """)
