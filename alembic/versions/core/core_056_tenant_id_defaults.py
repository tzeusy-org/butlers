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

import sqlalchemy as sa

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


def _table_exists(schema: str, table: str) -> bool:
    bind = op.get_bind()
    relname = f"{schema}.{table}"
    return (
        bind.execute(sa.text("SELECT to_regclass(:relname)"), {"relname": relname}).scalar()
        is not None
    )


def _column_exists(schema: str, table: str, column: str) -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = :schema
                  AND table_name = :table
                  AND column_name = :column
                """
            ),
            {"schema": schema, "table": table, "column": column},
        ).scalar()
        is not None
    )


def upgrade() -> None:
    # 1. Fix schema defaults on all butler schemas
    for schema in _SCHEMAS:
        for table in ("episodes", "facts", "rules"):
            if not _table_exists(schema, table) or not _column_exists(schema, table, "tenant_id"):
                continue
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
        for schema in _SCHEMAS:
            if not _table_exists(schema, "facts"):
                continue
            # Delete source property facts that would collide with existing
            # target facts on the partial unique index
            # (entity_id, scope, predicate) WHERE object_entity_id IS NULL
            #   AND validity='active' AND valid_at IS NULL.
            op.execute(
                f'DELETE FROM "{schema}".facts AS src '
                f"WHERE src.entity_id = '{source_id}'::uuid "
                f"  AND src.object_entity_id IS NULL "
                f"  AND src.validity = 'active' "
                f"  AND src.valid_at IS NULL "
                f"  AND EXISTS ( "
                f'    SELECT 1 FROM "{schema}".facts tgt '
                f"    WHERE tgt.entity_id = '{target_id}'::uuid "
                f"      AND tgt.scope = src.scope "
                f"      AND tgt.predicate = src.predicate "
                f"      AND tgt.object_entity_id IS NULL "
                f"      AND tgt.validity = 'active' "
                f"      AND tgt.valid_at IS NULL "
                f"  )"
            )
            # Delete source edge facts (entity_id side) that would collide
            # on idx_facts_edge_scope_predicate_active
            # (entity_id, object_entity_id, scope, predicate)
            op.execute(
                f'DELETE FROM "{schema}".facts AS src '
                f"WHERE src.entity_id = '{source_id}'::uuid "
                f"  AND src.object_entity_id IS NOT NULL "
                f"  AND src.validity = 'active' "
                f"  AND src.valid_at IS NULL "
                f"  AND EXISTS ( "
                f'    SELECT 1 FROM "{schema}".facts tgt '
                f"    WHERE tgt.entity_id = '{target_id}'::uuid "
                f"      AND tgt.object_entity_id = src.object_entity_id "
                f"      AND tgt.scope = src.scope "
                f"      AND tgt.predicate = src.predicate "
                f"      AND tgt.validity = 'active' "
                f"      AND tgt.valid_at IS NULL "
                f"  )"
            )
            # Re-point remaining subject-side facts
            op.execute(
                f'UPDATE "{schema}".facts '
                f"SET entity_id = '{target_id}'::uuid "
                f"WHERE entity_id = '{source_id}'::uuid"
            )
            # Delete object-side edge facts that would collide
            op.execute(
                f'DELETE FROM "{schema}".facts AS src '
                f"WHERE src.object_entity_id = '{source_id}'::uuid "
                f"  AND src.validity = 'active' "
                f"  AND src.valid_at IS NULL "
                f"  AND EXISTS ( "
                f'    SELECT 1 FROM "{schema}".facts tgt '
                f"    WHERE tgt.entity_id = src.entity_id "
                f"      AND tgt.object_entity_id = '{target_id}'::uuid "
                f"      AND tgt.scope = src.scope "
                f"      AND tgt.predicate = src.predicate "
                f"      AND tgt.validity = 'active' "
                f"      AND tgt.valid_at IS NULL "
                f"  )"
            )
            # Re-point remaining object-side edge facts
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
            if not _table_exists(schema, table) or not _column_exists(schema, table, "tenant_id"):
                continue
            op.execute(
                f'UPDATE "{schema}"."{table}" '
                "SET tenant_id = 'shared' WHERE tenant_id = 'owner'"
            )

    # Also fix any entities with tenant_id='owner' that weren't in the merge pairs
    if _table_exists("public", "entities") and _column_exists("public", "entities", "tenant_id"):
        op.execute(
            "UPDATE public.entities SET tenant_id = 'shared' WHERE tenant_id = 'owner'"
        )


def downgrade() -> None:
    # Restore old defaults (data migration is not reversed)
    for schema in _SCHEMAS:
        for table in ("episodes", "facts", "rules"):
            if not _table_exists(schema, table) or not _column_exists(schema, table, "tenant_id"):
                continue
            op.execute(
                f'ALTER TABLE "{schema}"."{table}" '
                "ALTER COLUMN tenant_id SET DEFAULT 'owner'"
            )
