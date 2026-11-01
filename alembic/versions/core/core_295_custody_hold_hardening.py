"""Harden endpoint custody holds: trusted function ownership and equivalent facts.

Revision ID: core_295
Revises: core_294
Create Date: 2026-11-02 00:00:00.000000

Review follow-up to core_293 (bu-s11n0s.25, openspec change
``endpoint-custody-holds``, REQ-endpoint-custody-holds-002 and -006).

- Ownership. core_293 gave the table and ``public.custody_target_held`` to the
  database owner but left ``public.custody_target_digest`` and
  ``public.guard_custody_holds`` with the migration executor. The definer
  ``custody_target_held`` calls the digest function, so whoever owns the digest
  can replace it and defeat every hold. All four custody objects now belong to
  the trusted database owner (re-asserted, so a database whose owner changed
  after core_293 is realigned too), with the same refusal when a runtime role
  could bypass row security or assume that owner.
- Equivalent phone spellings. Emails and handles have one normalized form, so
  one digest covers every spelling. Phones do not: ``+65 8765 0123`` and
  ``87650123`` digest differently, yet ``resolve_owner_triple`` suffix-matches
  a candidate against each separately. A matched owner phone fact is now
  treated as held when any phone fact of the same entity that is
  suffix-equivalent to it under that same rule is actively held, whatever that
  sibling's validity. A hold on one spelling therefore demotes every
  equivalent spelling, including one added or retired after the hold, and the
  hold writer does not have to find every variant to be safe.
- Legacy predicate mode. The core_145 exact-predicate mode (``p_predicate``
  other than ``owner-channel``) now matches only the holdable predicates
  ``has-email``, ``has-handle`` and ``has-phone``. Any other predicate has no
  hold kind, so an owner match on it could never be demoted; it now resolves
  no owner. There are no such callers. The function signature is unchanged.

Downgrade restores the core_293 resolver. Ownership stays with the database
owner, which the core_293 downgrade already requires of whoever runs it.
"""

from __future__ import annotations

from alembic import op

revision = "core_295"
down_revision = "core_294"
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

# Exactly the core_293 hold-aware body.
_CORE_293_RESOLVE = _RESOLVE_TEMPLATE.format(
    held_expression="public.custody_target_held(ef.predicate, ef.object)",
    legacy_filter="",
)

# A held matched fact still counts toward ambiguity; it only refuses owner. A
# phone fact is also held through any held sibling phone fact of the same
# entity, of any validity, that is equivalent under the suffix rule the
# owner-channel branch applies between a candidate and a stored phone.
_EQUIVALENT_AWARE_RESOLVE = _RESOLVE_TEMPLATE.format(
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
    legacy_filter=(
        "\n                    AND p_predicate IN ('has-email', 'has-handle', 'has-phone')"
    ),
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
    op.execute(_EQUIVALENT_AWARE_RESOLVE)


def downgrade() -> None:
    op.execute(_LOCK)
    op.execute(_CORE_293_RESOLVE)
