"""entity_predicate_registry: seed long-tail relational predicates.

Revision ID: rel_026
Revises: rel_025
Create Date: 2026-06-17 00:00:00.000000

Phase: relational-edges-triage (bu-kgh8g; parent epic bu-5vpyh).

Seeds seven new predicates into ``relationship.entity_predicate_registry``
that were identified in the backfill dry-run (bu-1fu8c) as graph-worthy
relational edges — durable standing relationships between two tracked entities.
These were previously unmapped by the alias map and would have defaulted to
narrative storage; this migration promotes them to the relational registry so
``relationship_assert_fact()`` accepts them and routes them to
``relationship.entity_facts``.

New predicates
--------------

*  ``manages``            — management role: subject (person) manages object (person/org).
*  ``managed-by``         — inverse of manages: subject (person) is managed by object (person).
*  ``manages-property``   — property role: subject (person) manages object (place/org).
*  ``participant-of``     — durable group/org participation: subject (person) participates in object.
*  ``invited-by``         — referral/sponsorship: subject (person) was invited/referred by object (person).
*  ``rental-agent``       — professional role: subject (person) is rental agent for object (org/place).
*  ``rental-location``    — rental relationship: subject (person) has rental relationship with object (place).

All are ``kind='relational'``, ``object_kind='entity'`` — directed edges between
two tracked entities in the relational family.

Idempotency
-----------
``INSERT ... ON CONFLICT (predicate) DO NOTHING`` for all rows — safe to replay.

Downgrade
---------
Deletes exactly the seven rows seeded here.  Does NOT drop the registry table,
the relationship schema, or any other predicate (all owned by earlier migrations).
"""

from __future__ import annotations

from alembic import op

revision = "rel_026"
down_revision = "rel_025"
branch_labels = None
depends_on = None

_NEW_PREDICATES: list[tuple[str, str, str, str]] = [
    (
        "manages",
        "relational",
        "entity",
        "Management role: subject (person) manages the object (person or organization).",
    ),
    (
        "managed-by",
        "relational",
        "entity",
        "Inverse of manages: subject (person) is managed by the object (person).",
    ),
    (
        "manages-property",
        "relational",
        "entity",
        "Property management role: subject (person) manages the object (place or organization).",
    ),
    (
        "participant-of",
        "relational",
        "entity",
        "Durable participation: subject (person) is a participant of the object (group, org, or event series).",
    ),
    (
        "invited-by",
        "relational",
        "entity",
        "Referral or sponsorship: subject (person) was invited or referred by the object (person).",
    ),
    (
        "rental-agent",
        "relational",
        "entity",
        "Professional role: subject (person) acts as rental agent for the object (organization or place).",
    ),
    (
        "rental-location",
        "relational",
        "entity",
        "Rental relationship: subject (person) rents from or has a rental relationship with the object (place).",
    ),
]


def upgrade() -> None:
    # Defensive: schema + registry table are created by rel_013/rel_014; guard
    # against an out-of-order or partial DB state.
    op.execute("CREATE SCHEMA IF NOT EXISTS relationship")

    for predicate, kind, object_kind, description in _NEW_PREDICATES:
        safe_desc = description.replace("'", "''")
        op.execute(
            f"""
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, description)
            VALUES ('{predicate}', '{kind}', '{object_kind}', '{safe_desc}')
            ON CONFLICT (predicate) DO NOTHING
            """
        )


def downgrade() -> None:
    # Remove exactly the seven rows this migration seeded. Other predicates,
    # the registry table, and the schema are owned by earlier migrations and survive.
    for predicate, _, _, _ in _NEW_PREDICATES:
        op.execute(
            f"DELETE FROM relationship.entity_predicate_registry WHERE predicate = '{predicate}'"
        )
