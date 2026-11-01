"""Make a custody phone hold cover every same-entity spelling of the number.

Revision ID: core_296
Revises: core_295
Create Date: 2026-11-02 00:00:00.000000

Review follow-up to core_295 (bu-s11n0s.29, openspec change
``endpoint-custody-holds``, REQ-endpoint-custody-holds-006 "Equivalent phone
spelling stays held").

core_295 demoted a matched owner phone fact when a held sibling phone fact of
the same entity was suffix-equivalent to it under the candidate rule (suffix
match, lengths within two digits). That relation is not transitive: a held
``0065 8765 0123`` (12 digits) and an unheld ``87650123`` (8 digits) differ by
four, so the candidate ``phone-digits:87650123`` matched only the unheld
spelling and resolved the owner despite the hold.

The sibling relation is now an equivalence: two phone facts of the same entity,
each with at least eight digits, are the same number when their trailing eight
digits agree. Any candidate the owner-channel branch relates to a stored phone
shares that phone's trailing eight digits, so a hold on one spelling demotes
every spelling a candidate could reach through any chain of matches. The class
is wider than the candidate rule, which fails closed: an owner phone that only
shares its trailing eight digits with a held one is demoted too. Candidate
matching, ambiguity and the legacy predicate mode are unchanged.

Ownership of the custody objects is re-asserted for the trusted database owner
exactly as core_295 does. Downgrade restores the core_295 resolver.
"""

from __future__ import annotations

from alembic import op

revision = "core_296"
down_revision = "core_295"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_293:custody_holds', 0))"

_RUNTIME_ROLES = (
    "chronicler",
    "concierge",
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "qa",
    "relationship",
    "switchboard",
    "travel",
    "calendar",
)

_RESOLVE_TEMPLATE = """
CREATE OR REPLACE FUNCTION public.resolve_owner_triple(
    p_predicate text,
    p_candidates text[]
)
RETURNS TABLE(entity_id uuid, is_primary boolean)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
BEGIN
    RETURN QUERY
    WITH live_matches AS MATERIALIZED (
        SELECT
            ef.subject,
            bool_or(ef."primary" IS TRUE) AS is_primary,
            bool_or('owner' = ANY(COALESCE(e.roles, ARRAY[]::text[]))) AS is_owner,
            bool_or({held_expression}) AS is_held
        FROM relationship.entity_facts ef
        JOIN public.entities e ON e.id = ef.subject
        WHERE ef.validity = 'active'
          AND ef.object_kind = 'literal'
          AND (
                (
                    p_predicate = 'owner-channel'
                    AND (
                        (
                            ef.predicate = 'has-email'
                            AND 'has-email:' || lower(btrim(ef.object)) = ANY(p_candidates)
                        )
                        OR (
                            ef.predicate = 'has-handle'
                            AND 'has-handle:' || lower(btrim(ef.object)) = ANY(p_candidates)
                        )
                        OR (
                            ef.predicate = 'has-phone'
                            AND EXISTS (
                                SELECT 1
                                FROM unnest(p_candidates) AS candidate(value)
                                CROSS JOIN LATERAL (
                                    SELECT split_part(candidate.value, ':', 2) AS digits
                                ) AS normalized
                                WHERE candidate.value LIKE 'phone-digits:%'
                                  AND normalized.digits ~ '^[0-9]+$'
                                  AND length(normalized.digits) >= 8
                                  AND length(regexp_replace(ef.object, '\\D', '', 'g')) >= 8
                                  AND abs(
                                      length(regexp_replace(ef.object, '\\D', '', 'g'))
                                      - length(normalized.digits)
                                  ) <= 2
                                  AND (
                                      regexp_replace(ef.object, '\\D', '', 'g')
                                          LIKE '%' || normalized.digits
                                      OR normalized.digits LIKE '%'
                                          || regexp_replace(ef.object, '\\D', '', 'g')
                                  )
                            )
                        )
                    )
                )
                OR (
                    -- Preserve the core_145 public ABI for exact legacy
                    -- predicate callers; owner-channel is the new closed mode.
                    p_predicate <> 'owner-channel'{legacy_filter}
                    AND ef.predicate = p_predicate
                    AND ef.object = ANY(p_candidates)
                )
          )
          AND e.metadata ->> 'merged_into' IS NULL
          AND e.metadata ->> 'deleted_at' IS NULL
        GROUP BY ef.subject
    )
    SELECT matches.subject, matches.is_primary
    FROM live_matches matches
    WHERE (SELECT count(*) FROM live_matches) = 1
      AND matches.is_owner
      AND NOT matches.is_held
    LIMIT 1;
END;
$fn$;
"""

_LEGACY_FILTER = "\n                    AND p_predicate IN ('has-email', 'has-handle', 'has-phone')"

# Exactly the core_295 body: siblings related by the candidate suffix rule.
_CORE_295_RESOLVE = _RESOLVE_TEMPLATE.format(
    held_expression="""
                public.custody_target_held(ef.predicate, ef.object)
                OR (
                    ef.predicate = 'has-phone'
                    AND EXISTS (
                        SELECT 1
                        FROM relationship.entity_facts sibling
                        CROSS JOIN LATERAL (
                            SELECT
                                regexp_replace(sibling.object, '\\D', '', 'g') AS held,
                                regexp_replace(ef.object, '\\D', '', 'g') AS matched
                        ) AS digits
                        WHERE sibling.subject = ef.subject
                          AND sibling.predicate = 'has-phone'
                          AND sibling.object_kind = 'literal'
                          AND sibling.id <> ef.id
                          AND length(digits.held) >= 8
                          AND length(digits.matched) >= 8
                          AND abs(length(digits.held) - length(digits.matched)) <= 2
                          AND (
                              digits.held LIKE '%' || digits.matched
                              OR digits.matched LIKE '%' || digits.held
                          )
                          AND public.custody_target_held('has-phone', sibling.object)
                    )
                )
            """,
    legacy_filter=_LEGACY_FILTER,
)

# A held matched fact still counts toward ambiguity; it only refuses owner. A
# phone fact is also held through any held phone fact of the same entity, of
# any validity, with the same trailing eight digits. Every candidate the
# owner-channel branch relates to a stored phone shares that phone's trailing
# eight digits, so this one equivalence class covers every chain of spellings.
_TRANSITIVE_RESOLVE = _RESOLVE_TEMPLATE.format(
    held_expression="""
                public.custody_target_held(ef.predicate, ef.object)
                OR (
                    ef.predicate = 'has-phone'
                    AND EXISTS (
                        SELECT 1
                        FROM relationship.entity_facts sibling
                        CROSS JOIN LATERAL (
                            SELECT
                                regexp_replace(sibling.object, '\\D', '', 'g') AS held,
                                regexp_replace(ef.object, '\\D', '', 'g') AS matched
                        ) AS digits
                        WHERE sibling.subject = ef.subject
                          AND sibling.predicate = 'has-phone'
                          AND sibling.object_kind = 'literal'
                          AND sibling.id <> ef.id
                          AND length(digits.held) >= 8
                          AND length(digits.matched) >= 8
                          AND right(digits.held, 8) = right(digits.matched, 8)
                          AND public.custody_target_held('has-phone', sibling.object)
                    )
                )
            """,
    legacy_filter=_LEGACY_FILTER,
)


def _maintenance_owner() -> None:
    # Same posture as core_275/core_293: the database's trusted owner keeps
    # every custody object, and no runtime may bypass or assume it.
    roles = ",".join(f"'butler_{name}_rw'" for name in _RUNTIME_ROLES)
    op.execute(f"""DO $$ DECLARE trusted_owner oid; owner_name text; BEGIN
        SELECT datdba INTO STRICT trusted_owner FROM pg_database
            WHERE datname = current_database();
        owner_name := pg_get_userbyid(trusted_owner);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ({roles}, 'connector_writer')
            AND (rolsuper OR rolbypassrls OR pg_has_role(oid, trusted_owner, 'MEMBER'))) THEN
            RAISE EXCEPTION 'Runtime may not bypass or assume custody maintenance owner';
        END IF;
        EXECUTE format('ALTER TABLE public.custody_holds OWNER TO %I', owner_name);
        EXECUTE format(
            'ALTER FUNCTION public.custody_target_held(text, text) OWNER TO %I', owner_name);
        EXECUTE format(
            'ALTER FUNCTION public.custody_target_digest(text, text) OWNER TO %I', owner_name);
        EXECUTE format('ALTER FUNCTION public.guard_custody_holds() OWNER TO %I', owner_name);
    END $$""")


def upgrade() -> None:
    op.execute(_LOCK)
    _maintenance_owner()
    op.execute(_TRANSITIVE_RESOLVE)


def downgrade() -> None:
    op.execute(_LOCK)
    op.execute(_CORE_295_RESOLVE)
