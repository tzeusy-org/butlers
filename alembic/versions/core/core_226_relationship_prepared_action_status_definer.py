"""Add public.resolve_relationship_prepared_action_status() SECURITY DEFINER lookup.

Revision ID: core_226
Revises: core_225
Create Date: 2026-09-09 00:00:01.000000

bu-2jtfw.11: rendering a prepared-action door in the insight digest needs the
action's live status. ``insight_candidates`` lives in ``public`` and is read
by the Switchboard daemon's own pool during ``delivery_cycle()``; the
referenced ``pending_actions`` row lives in ``relationship``'s own
schema-isolated table, which that pool cannot read directly (mirrors
core_145's ``resolve_owner_triple`` rationale exactly).

Rather than widen cross-schema grants, this adds a single, narrowly-scoped
``SECURITY DEFINER`` function -- hardcoded to ``relationship.pending_actions``,
not caller-parameterized by schema name (a caller-parameterized cross-schema
shortcut is the pattern this repo explicitly avoids; see
AGENTS.md's "Notes to self" on cross-butler canonical fetches). It returns
only ``status``, nothing else from the row. Scoped to the relationship butler
because that is the only prepared-action producer this bead ships; a second
producer butler needs its own equally-narrow function, not a widened one.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_226"
down_revision = "core_225"
branch_labels = None
depends_on = None


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str) -> None:
    op.execute(
        f"""
        DO $do$
        BEGIN
            EXECUTE {_quote_literal(statement)};
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $do$;
        """
    )


# plpgsql (not sql) so the reference to relationship.pending_actions resolves
# at call time, not at CREATE time -- the function can be created before the
# relationship schema exists. search_path is pinned to pg_catalog, the
# standard hardening for SECURITY DEFINER.
_CREATE_FN = """
CREATE OR REPLACE FUNCTION public.resolve_relationship_prepared_action_status(
    p_action_id uuid
)
RETURNS TABLE(status text)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $fn$
BEGIN
    RETURN QUERY
    SELECT pa.status
    FROM relationship.pending_actions pa
    WHERE pa.id = p_action_id
      AND pa.origin = 'prepared';
END;
$fn$;
"""


def upgrade() -> None:
    _execute_best_effort(_CREATE_FN)
    _execute_best_effort(
        "GRANT EXECUTE ON FUNCTION "
        "public.resolve_relationship_prepared_action_status(uuid) TO PUBLIC"
    )


def downgrade() -> None:
    _execute_best_effort(
        "DROP FUNCTION IF EXISTS public.resolve_relationship_prepared_action_status(uuid)"
    )
