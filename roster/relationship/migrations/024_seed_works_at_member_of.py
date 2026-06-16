"""entity_predicate_registry: seed ``works-at`` and ``member-of`` relational predicates.

Revision ID: rel_024
Revises: rel_023
Create Date: 2026-06-16 00:00:00.000000

Phase: relational-edges-single-home (bu-i3sps; epic bu-5vpyh).

Seeds two new predicates into ``relationship.entity_predicate_registry``:

* ``works-at``   (``kind='relational'``, ``object_kind='entity'``) —
  employment relationship; subject (person) is employed by the object (organization).
* ``member-of``  (``kind='relational'``, ``object_kind='entity'``) —
  membership relationship; subject (person) is a member of the object (organization).

Both are **person→organization** directed edges in the relational family.  They
are required by the spec (``relationship-facts`` §"Scenario: Person-to-organization
edges are registered as relational") so that ``relationship_assert_fact()`` accepts
them and routes them to ``relationship.entity_facts``.

Idempotency
-----------
``INSERT ... ON CONFLICT (predicate) DO NOTHING`` for both rows — safe to replay
on a database where a prior partial run already seeded one or both rows.

Downgrade
---------
Deletes exactly the two rows seeded here.  Does NOT drop the registry table,
the relationship schema, or any other predicate (all owned by earlier migrations).
"""

from __future__ import annotations

from alembic import op

revision = "rel_024"
down_revision = "rel_023"
branch_labels = None
depends_on = None

_NEW_PREDICATES: list[tuple[str, str, str, str]] = [
    (
        "works-at",
        "relational",
        "entity",
        "Employment relationship: subject (person) works at the object (organization).",
    ),
    (
        "member-of",
        "relational",
        "entity",
        "Membership relationship: subject (person) is a member of the object (organization).",
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
    # Remove exactly the two rows this migration seeded. Other predicates,
    # the registry table, and the schema are owned by earlier migrations and survive.
    for predicate, _, _, _ in _NEW_PREDICATES:
        op.execute(
            f"DELETE FROM relationship.entity_predicate_registry WHERE predicate = '{predicate}'"
        )
