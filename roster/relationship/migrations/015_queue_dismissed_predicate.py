"""predicate_registry: add queue.dismissed state-marker predicate.

Revision ID: rel_015
Revises: rel_014
Create Date: 2026-05-18 00:00:00.000000

Phase: entity-redesign (bu-297lj — POST /entities/queue/dismiss).

Adds ``queue.dismissed`` to ``relationship.predicate_registry``.  This
predicate is written by ``POST /api/butlers/relationship/entities/queue/dismiss``
via the central writer ``relationship_assert_fact()`` to mark that an operator
has explicitly dismissed an entity from the curation queue.

Triple shape
-----------
subject  entity UUID (FK to public.entities)
object   ``'dismissed'`` (literal)
object_kind  ``'literal'``
kind     ``'state'``

The triple is idempotent: re-dismissing the same entity produces an
``unchanged`` outcome from the central writer (same subject/predicate/object).
"""

from __future__ import annotations

from alembic import op

revision = "rel_015"
down_revision = "rel_014"
branch_labels = None
depends_on = None

_RELATIONSHIP_ROLE = "butler_relationship_rw"
_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
_TABLE_FQN = "relationship.predicate_registry"

_PREDICATE = "queue.dismissed"
_KIND = "state"
_OBJECT_KIND = "literal"
_DESCRIPTION = (
    "Operator dismissed this entity from the curation queue. "
    "Object is always the literal string 'dismissed'. "
    "Written by POST /entities/queue/dismiss via relationship_assert_fact()."
)


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object      THEN NULL;
            WHEN undefined_table       THEN NULL;
            WHEN invalid_schema_name   THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # Add the kind='state' value to the CHECK constraint if it doesn't already allow it.
    # The existing constraint only allows ('contact', 'relational', 'override').
    # We alter the column to add 'state' as a permitted value before inserting.
    op.execute("""
        ALTER TABLE relationship.predicate_registry
        DROP CONSTRAINT IF EXISTS predicate_registry_kind_check
    """)
    op.execute("""
        ALTER TABLE relationship.predicate_registry
        ADD CONSTRAINT predicate_registry_kind_check
        CHECK (kind IN ('contact', 'relational', 'override', 'state'))
    """)

    safe_desc = _DESCRIPTION.replace("'", "''")
    op.execute(f"""
        INSERT INTO relationship.predicate_registry
            (predicate, kind, object_kind, description)
        VALUES ('{_PREDICATE}', '{_KIND}', '{_OBJECT_KIND}', '{safe_desc}')
        ON CONFLICT (predicate) DO NOTHING
    """)

    _grant_best_effort(_TABLE_FQN, _TABLE_PRIVILEGES, _RELATIONSHIP_ROLE)


def downgrade() -> None:
    op.execute(f"DELETE FROM relationship.predicate_registry WHERE predicate = '{_PREDICATE}'")
    # Restore original constraint (without 'state').
    op.execute("""
        ALTER TABLE relationship.predicate_registry
        DROP CONSTRAINT IF EXISTS predicate_registry_kind_check
    """)
    op.execute("""
        ALTER TABLE relationship.predicate_registry
        ADD CONSTRAINT predicate_registry_kind_check
        CHECK (kind IN ('contact', 'relational', 'override'))
    """)
