"""Add public.resolve_owner_triple() SECURITY DEFINER owner-channel lookup.

Revision ID: core_145
Revises: core_144
Create Date: 2026-06-22 00:00:00.000000

The approval gate auto-approves owner-directed outbound sends (notify / telegram /
email) only when the target resolves to the owner entity on a *primary* channel
(RFC 0017 §2.1). That resolution reads ``relationship.entity_facts`` (where the
owner's contact handles live). A non-relationship butler (e.g. messenger) runs
under ``SET ROLE butler_<schema>_rw`` and is schema-isolated from the relationship
schema, so the lookup raises ``insufficient_privilege`` → "unresolvable target" →
the owner-directed message is parked for approval instead of auto-approving.

Rather than widen cross-schema grants (which would weaken schema isolation), this
adds a single ``SECURITY DEFINER`` function in the shared ``public`` schema that
performs the owner-only triple lookup as its owner (a role that *can* read the
relationship schema) and returns just ``(entity_id, is_primary)``. Any butler can
EXECUTE it without gaining read access to ``relationship.entity_facts`` itself.

The channel-type → predicate mapping and value normalisation (telegram prefixing)
stay in Python (``src/butlers/identity.py``); this function is a generic,
owner-scoped triple lookup taking the predicate and pre-normalised candidate
object values, so there is no SQL/Python mapping duplication to drift.

All DDL is best-effort so the migration is safe on partially-provisioned databases.
"""

from __future__ import annotations

from alembic import op

revision = "core_145"
down_revision = "core_144"
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


# plpgsql (not sql) so the body's references to relationship.entity_facts are
# resolved at call time, not at CREATE time — the function can be created before
# the relationship schema exists. search_path is pinned to pg_catalog and every
# table is schema-qualified, the standard hardening for SECURITY DEFINER.
_CREATE_FN = """
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
    _execute_best_effort(_CREATE_FN)
    # Read-only owner-channel probe; callable by every butler runtime role.
    _execute_best_effort(
        "GRANT EXECUTE ON FUNCTION public.resolve_owner_triple(text, text[]) TO PUBLIC"
    )


def downgrade() -> None:
    _execute_best_effort("DROP FUNCTION IF EXISTS public.resolve_owner_triple(text, text[])")
