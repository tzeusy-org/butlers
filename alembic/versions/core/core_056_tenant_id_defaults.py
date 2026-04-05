"""Fix tenant_id defaults and migrate stale 'owner' data.

Revision ID: core_056
Revises: core_055
Create Date: 2026-04-05 00:00:00.000000

Problem: The memory module's collapsed migration (001_memory_schema.py) set
DEFAULT 'owner' on episodes/facts/rules tenant_id columns, but the Python
layer defaults to 'shared'. LLM agents occasionally hallucinated tenant_id=
'owner', creating orphan entities invisible to resolution.

Changes:
  1. ALTER DEFAULT on all butler schema facts/episodes/rules tables from
     'owner' to 'shared'.
  2. Merge the 2 orphan 'owner' entities into their 'shared' counterparts
     (re-point facts, then delete the duplicates).
  3. UPDATE any remaining tenant_id='owner' rows to 'shared'.
"""

from alembic import op

revision = "core_056"
down_revision = "core_055"
branch_labels = None
depends_on = None

# All butler schemas with memory tables
_SCHEMAS = [
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "relationship",
    "switchboard",
    "travel",
]


def upgrade() -> None:
    # 1. Fix schema defaults on all butler schemas
    for schema in _SCHEMAS:
        for table in ("episodes", "facts", "rules"):
            op.execute(
                f'ALTER TABLE "{schema}"."{table}" '
                "ALTER COLUMN tenant_id SET DEFAULT 'shared'"
            )

    # 2. Merge orphan 'owner' entities into their 'shared' counterparts.
    #
    #    Entity 3685b07d ("Tze How Lee", owner) → c64f5aed ("Tze How Lee", shared)
    #    Entity 9ff28432 ("tzeusii", owner)     → eaced35c ("tzeusii", shared)
    #
    #    For each pair: re-point facts, then delete the orphan.

    _merge_pairs = [
        ("3685b07d-59a8-4c56-af67-42122367d177", "c64f5aed-9b1f-492e-bab2-86c986c31ebd"),
        ("9ff28432-6c52-48bc-9c2e-e5a1860972e0", "eaced35c-a0d3-4957-946c-61647c45fc19"),
    ]

    for source_id, target_id in _merge_pairs:
        # Re-point subject-side facts
        for schema in _SCHEMAS:
            op.execute(
                f'UPDATE "{schema}".facts '
                f"SET entity_id = '{target_id}'::uuid "
                f"WHERE entity_id = '{source_id}'::uuid"
            )
            # Re-point object-side edge facts
            op.execute(
                f'UPDATE "{schema}".facts '
                f"SET object_entity_id = '{target_id}'::uuid "
                f"WHERE object_entity_id = '{source_id}'::uuid"
            )

        # Delete the orphan entity
        op.execute(
            f"DELETE FROM public.entities WHERE id = '{source_id}'::uuid"
        )

    # 3. Flip any remaining tenant_id='owner' rows to 'shared'
    for schema in _SCHEMAS:
        for table in ("episodes", "facts", "rules"):
            op.execute(
                f'UPDATE "{schema}"."{table}" '
                "SET tenant_id = 'shared' WHERE tenant_id = 'owner'"
            )

    # Also fix any entities with tenant_id='owner' that weren't in the merge pairs
    op.execute(
        "UPDATE public.entities SET tenant_id = 'shared' WHERE tenant_id = 'owner'"
    )


def downgrade() -> None:
    # Restore old defaults (data migration is not reversed)
    for schema in _SCHEMAS:
        for table in ("episodes", "facts", "rules"):
            op.execute(
                f'ALTER TABLE "{schema}"."{table}" '
                "ALTER COLUMN tenant_id SET DEFAULT 'owner'"
            )
