"""fix_owner_tenant_id

Back-fill entities that were created with tenant_id='owner' (an LLM
hallucination — there is no 'owner' tenant; the intended value is 'shared').
These entities were invisible to the dashboard queries because those queries
previously filtered on tenant_id IN ('default', 'shared').

After this migration all entities with tenant_id='owner' are updated to
tenant_id='shared' so they appear in entity lists and can be retrieved by ID.

The entities table has a unique constraint on (tenant_id, canonical_name,
entity_type).  If a 'shared' duplicate already exists the back-fill would
violate the constraint.  We skip any such rows and leave the de-duplication to
the entity-merge workflow (no data loss — the 'shared' copy is already
visible).

Revision ID: mem_013
Revises: mem_012
Create Date: 2026-03-10 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_013"
down_revision = "mem_012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Update entities where tenant_id='owner' to 'shared', skipping any that
    # would create a duplicate (tenant_id, canonical_name, entity_type) triple.
    op.execute("""
        UPDATE entities
        SET tenant_id = 'shared', updated_at = now()
        WHERE tenant_id = 'owner'
          AND NOT EXISTS (
              SELECT 1 FROM entities AS dup
               WHERE dup.tenant_id = 'shared'
                 AND dup.canonical_name = entities.canonical_name
                 AND dup.entity_type   = entities.entity_type
          )
    """)


def downgrade() -> None:
    # There is no safe way to reverse this — we don't know which 'shared'
    # entities were originally 'owner'.  Downgrade is intentionally a no-op.
    pass
