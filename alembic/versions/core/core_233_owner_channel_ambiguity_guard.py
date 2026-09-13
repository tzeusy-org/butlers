"""Refuse ambiguous owner-channel authorization matches.

Revision ID: core_233
Revises: core_232
Create Date: 2026-09-13 00:00:00.000000

``public.resolve_owner_triple`` is the least-privilege bridge used by
schema-isolated butlers to recognize owner-directed outbound communication.
The original function filtered to the owner before selecting a match, which
could erase an owner-plus-external collision on the same channel identifier.
Require one distinct live entity across the whole candidate set before
returning owner authorization.
"""

from __future__ import annotations

from alembic import op

revision = "core_233"
down_revision = "core_232"
branch_labels = None
depends_on = None

_AMBIGUITY_SAFE_FUNCTION = """
CREATE OR REPLACE FUNCTION public.resolve_owner_triple(
    p_predicate text,
    p_candidates text[]
)
RETURNS TABLE(entity_id uuid, is_primary boolean)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $fn$
BEGIN
    RETURN QUERY
    WITH live_matches AS MATERIALIZED (
        SELECT
            ef.subject,
            bool_or(ef."primary" IS TRUE) AS is_primary,
            bool_or('owner' = ANY(COALESCE(e.roles, ARRAY[]::text[]))) AS is_owner
        FROM relationship.entity_facts ef
        JOIN public.entities e ON e.id = ef.subject
        WHERE ef.validity = 'active'
          AND ef.object_kind = 'literal'
          AND ef.predicate = p_predicate
          AND ef.object = ANY(p_candidates)
          AND e.metadata ->> 'merged_into' IS NULL
          AND e.metadata ->> 'deleted_at' IS NULL
        GROUP BY ef.subject
    )
    SELECT matches.subject, matches.is_primary
    FROM live_matches matches
    WHERE (SELECT count(*) FROM live_matches) = 1
      AND matches.is_owner
    LIMIT 1;
END;
$fn$;
"""

_PRIOR_FUNCTION = """
CREATE OR REPLACE FUNCTION public.resolve_owner_triple(
    p_predicate text,
    p_candidates text[]
)
RETURNS TABLE(entity_id uuid, is_primary boolean)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $fn$
BEGIN
    RETURN QUERY
    SELECT ef.subject, ef."primary"
    FROM relationship.entity_facts ef
    JOIN public.entities e ON e.id = ef.subject
    WHERE ef.validity = 'active'
      AND ef.object_kind = 'literal'
      AND ('owner' = ANY(COALESCE(e.roles, ARRAY[]::text[])))
      AND ef.predicate = p_predicate
      AND ef.object = ANY(p_candidates)
    ORDER BY ef."primary" DESC NULLS LAST
    LIMIT 1;
END;
$fn$;
"""


def upgrade() -> None:
    op.execute(_AMBIGUITY_SAFE_FUNCTION)


def downgrade() -> None:
    op.execute(_PRIOR_FUNCTION)
