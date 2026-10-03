"""Add server-derived content authority to episodes, facts and rules (bu-q7vx1q.1).

Revision ID: mem_013
Revises: mem_012

``content_authority`` records who authored the content an artifact came from
(``owner`` | ``owner_device`` | ``third_party`` | ``system`` | ``mixed``), stamped
by the server from the Switchboard routing context.  ``authority_entity_id`` is
the sender entity that stamp was derived from.  Rules additionally carry
``endorsed_at`` / ``endorsed_by`` (the owner's explicit approval of a held rule).

Legacy rows are deliberately NOT backfilled: a NULL authority fails closed
(legacy rules are held, owner-anchored legacy facts are not Profile Facts).  This
migration records the counts it left unclassified as a
``content_authority_legacy_receipt`` row in ``memory_events`` (only when there are
legacy rows), and stales the discovery-catalog
rows of legacy rules so they stop steering other butlers until endorsed.

Additive-only: nullable columns need no table rewrite.
"""

from __future__ import annotations

from alembic import op

revision = "mem_013"
down_revision = "mem_012"
branch_labels = None
depends_on = None

_AUTHORITIES = "('owner', 'owner_device', 'third_party', 'system', 'mixed')"


def upgrade() -> None:
    for table in ("episodes", "facts", "rules"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS content_authority TEXT")
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS authority_entity_id UUID")
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_content_authority_check")
        op.execute(
            f"""
            ALTER TABLE {table}
            ADD CONSTRAINT {table}_content_authority_check
            CHECK (content_authority IS NULL OR content_authority IN {_AUTHORITIES})
            """
        )
    op.execute("ALTER TABLE rules ADD COLUMN IF NOT EXISTS endorsed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE rules ADD COLUMN IF NOT EXISTS endorsed_by UUID")

    op.execute(
        """
        DO $$
        DECLARE
            legacy_rules BIGINT;
            legacy_facts BIGINT;
            staled BIGINT := 0;
        BEGIN
            SELECT count(*) INTO legacy_rules FROM rules WHERE content_authority IS NULL;
            SELECT count(*) INTO legacy_facts FROM facts WHERE content_authority IS NULL;

            IF to_regclass('public.memory_catalog') IS NOT NULL THEN
                UPDATE public.memory_catalog mc
                SET confidence = 0, invalid_at = COALESCE(mc.invalid_at, now()), updated_at = now()
                WHERE mc.source_schema = current_schema()
                  AND mc.source_table = 'rules'
                  AND mc.invalid_at IS NULL
                  AND mc.source_id IN (SELECT id FROM rules WHERE content_authority IS NULL);
                GET DIAGNOSTICS staled = ROW_COUNT;
            END IF;

            IF legacy_rules + legacy_facts > 0 THEN
                INSERT INTO memory_events (event_type, actor, payload)
                VALUES (
                    'content_authority_legacy_receipt',
                    'migration',
                    jsonb_build_object(
                        'schema', current_schema(),
                        'legacy_rules_held', legacy_rules,
                        'legacy_facts_unclassified', legacy_facts,
                        'catalog_rows_staled', staled
                    )
                );
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE rules DROP COLUMN IF EXISTS endorsed_by")
    op.execute("ALTER TABLE rules DROP COLUMN IF EXISTS endorsed_at")
    for table in ("rules", "facts", "episodes"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_content_authority_check")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS authority_entity_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS content_authority")
