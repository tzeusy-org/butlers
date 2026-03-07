"""health_predicates

Seed predicate_registry with health-domain predicates used by the health butler's
SPO-backed tools (measurements, symptoms, medication doses, medications, conditions,
and research).

Temporal predicates (is_temporal=true) — each fact coexists with others at a
different valid_at:
  - measurement_weight, measurement_blood_pressure, measurement_heart_rate,
    measurement_blood_sugar, measurement_temperature
  - symptom
  - took_dose

Property/superseding predicates (is_temporal=false) — the latest fact supersedes
the previous one for the same (entity_id, scope, predicate) key:
  - medication
  - condition
  - research

Revision ID: mem_009
Revises: mem_008
Create Date: 2026-03-08 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_009"
down_revision = "mem_008"
branch_labels = None
depends_on = None

# Predicates seeded by this migration
_TEMPORAL_PREDICATES = [
    ("measurement_weight", "Health measurement: body weight. Metadata: {value, unit, notes}."),
    (
        "measurement_blood_pressure",
        "Health measurement: blood pressure. Metadata: {value, unit, notes}.",
    ),
    (
        "measurement_heart_rate",
        "Health measurement: heart rate. Metadata: {value, unit, notes}.",
    ),
    (
        "measurement_blood_sugar",
        "Health measurement: blood sugar / glucose. Metadata: {value, unit, notes}.",
    ),
    (
        "measurement_temperature",
        "Health measurement: body temperature. Metadata: {value, unit, notes}.",
    ),
    (
        "symptom",
        "A symptom occurrence. Content = symptom name. Metadata: {severity, condition_id, notes}.",
    ),
    (
        "took_dose",
        "A medication dose event. Content = medication name. "
        "Metadata: {medication_id, skipped, notes}.",
    ),
]

_PROPERTY_PREDICATES = [
    (
        "medication",
        "Current medication. Content = ''{name} {dosage} {frequency}''. "
        "Metadata: {name, dosage, frequency, schedule, active, notes}.",
    ),
    (
        "condition",
        "A health condition. Content = ''{name}: {status}''. "
        "Metadata: {name, status, diagnosed_at, notes}.",
    ),
    (
        "research",
        "A health research note. Content = research text. "
        "Metadata: {title, tags, source_url, condition_id}.",
    ),
]


def upgrade() -> None:
    # Ensure is_temporal column exists (added in a later migration than 005).
    # Use IF NOT EXISTS guard so this is safe to run on older schemas.
    op.execute("""
        ALTER TABLE predicate_registry
        ADD COLUMN IF NOT EXISTS is_temporal BOOLEAN NOT NULL DEFAULT false
    """)

    _on_conflict = "ON CONFLICT (name) DO UPDATE SET is_temporal = EXCLUDED.is_temporal, description = EXCLUDED.description"  # noqa: E501
    for name, description in _TEMPORAL_PREDICATES:
        op.execute(
            "INSERT INTO predicate_registry (name, is_temporal, description) "
            f"VALUES ('{name}', true, '{description}') " + _on_conflict
        )

    for name, description in _PROPERTY_PREDICATES:
        op.execute(
            "INSERT INTO predicate_registry (name, is_temporal, description) "
            f"VALUES ('{name}', false, '{description}') " + _on_conflict
        )


def downgrade() -> None:
    names = [n for n, _ in _TEMPORAL_PREDICATES + _PROPERTY_PREDICATES]
    quoted = ", ".join(f"'{n}'" for n in names)
    op.execute(f"DELETE FROM predicate_registry WHERE name IN ({quoted})")
