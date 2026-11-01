"""Record endpoint custody holds and stop held endpoints resolving to owner.

Revision ID: core_293
Revises: core_291
Create Date: 2026-11-02 00:00:00.000000

First slice of bu-s11n0s.1 (openspec change ``endpoint-custody-holds``,
REQ-endpoint-custody-holds-002 and -006). A lost, stolen or replaced owner
channel must stop carrying owner authority. This revision adds:

- ``public.custody_holds``: one authoritative hold record per
  canonical owner endpoint target and generation. The target key is a digest
  (``public.custody_target_digest``) over the typed fact universe of
  ``public.resolve_owner_triple`` (``has-email``, ``has-handle``,
  ``has-phone``) with the same normalization; no raw identifier is copied
  into the hold table. ``owner_entity_id`` is checked at insert by the
  guard rather than a foreign key: ``TRUNCATE public.entities CASCADE``
  truncates every referencing table regardless of ``ON DELETE``, and
  immutable hold history must neither be wiped by nor block an entity
  reset. Entities are soft-deleted and merged in place, so the pointer
  stays meaningful. Generations are
  monotonic per target and never reset on release or rehold. History is
  immutable: no DELETE or TRUNCATE, and the only permitted change is a single
  ``released_at`` stamp.
- Row level security with no policy. The bootstrap may regrant public DML, so
  every runtime role that is not the trusted maintenance owner reads no rows
  and cannot insert, release, delete or reset a hold. Like core_275 the table
  is owned by the database owner without FORCE, so ordinary owner pg_dump
  keeps every row.
- ``public.custody_target_held(predicate, object)``: a content-blind
  SECURITY DEFINER eligibility read owned by that same maintenance owner.
- ``public.resolve_owner_triple``: the core_233 ambiguity guard plus one more
  refusal. When any matched fact of the sole owner is actively held, the
  function returns no owner row. Every owner-only consumer of the definer
  (Telegram approval callbacks, Switchboard accepted-source owner stamping,
  owner-recipient handoff and approval corroboration) therefore treats the
  held endpoint as not owner. Ambiguity, inactive, merged and deleted rules
  are unchanged, and a held fact still counts toward ambiguity.

No runtime writer is installed here: the admitted Switchboard hold, release
and recovery engine is a later slice. Downgrade refuses while any hold is
active, so a rollback can never silently restore trust in a held endpoint.
"""

from __future__ import annotations

from alembic import op

revision = "core_293"
down_revision = "core_291"
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

_TABLE = """
CREATE TABLE IF NOT EXISTS public.custody_holds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_entity_id UUID NOT NULL,
    target_kind TEXT NOT NULL
        CHECK (target_kind IN ('has-email', 'has-handle', 'has-phone')),
    target_digest TEXT NOT NULL CHECK (target_digest ~ '^[0-9a-f]{64}$'),
    generation BIGINT NOT NULL CHECK (generation > 0),
    episode_id UUID NOT NULL,
    reason TEXT NOT NULL
        CHECK (reason IN ('lost', 'stolen', 'replaced', 'disowned_access')),
    source_command_id UUID NOT NULL,
    held_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ,
    UNIQUE (target_kind, target_digest, generation),
    CHECK (released_at IS NULL OR released_at >= held_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_custody_holds_active_target
    ON public.custody_holds (target_kind, target_digest)
    WHERE released_at IS NULL;
"""

# The one canonical target key. The normalization is the one
# resolve_owner_triple applies to stored facts (emails and handles trimmed and
# lower-cased, phones digits only), so a hold keyed on an owner fact matches
# every candidate spelling that resolves to that fact. Only the digest is
# stored; the hold table never copies a raw identifier.
_TARGET_DIGEST = """
CREATE OR REPLACE FUNCTION public.custody_target_digest(
    p_predicate text,
    p_object text
)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT pg_catalog.encode(
        pg_catalog.sha256(pg_catalog.convert_to(
            p_predicate || ':' || CASE
                WHEN p_predicate = 'has-phone'
                    THEN pg_catalog.regexp_replace(p_object, '\\D', '', 'g')
                ELSE pg_catalog.lower(pg_catalog.btrim(p_object))
            END,
            'UTF8'
        )),
        'hex'
    )
$fn$;
"""

_GUARD = """
CREATE OR REPLACE FUNCTION public.guard_custody_holds() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
DECLARE next_generation BIGINT;
BEGIN
    IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'endpoint custody hold history cannot be deleted'
            USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT COALESCE(max(h.generation), 0) + 1 INTO next_generation
        FROM public.custody_holds h
        WHERE h.target_kind = NEW.target_kind AND h.target_digest = NEW.target_digest;
        IF NOT EXISTS (SELECT 1 FROM public.entities e WHERE e.id = NEW.owner_entity_id) THEN
            RAISE EXCEPTION 'endpoint custody hold must name an existing owner entity'
                USING ERRCODE = '23503';
        END IF;
        IF NEW.generation <> next_generation OR NEW.released_at IS NOT NULL THEN
            RAISE EXCEPTION 'endpoint custody hold must open the next generation'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.owner_entity_id IS DISTINCT FROM OLD.owner_entity_id
       OR NEW.target_kind IS DISTINCT FROM OLD.target_kind
       OR NEW.target_digest IS DISTINCT FROM OLD.target_digest
       OR NEW.generation IS DISTINCT FROM OLD.generation
       OR NEW.episode_id IS DISTINCT FROM OLD.episode_id
       OR NEW.reason IS DISTINCT FROM OLD.reason
       OR NEW.source_command_id IS DISTINCT FROM OLD.source_command_id
       OR NEW.held_from IS DISTINCT FROM OLD.held_from
       OR OLD.released_at IS NOT NULL
       OR NEW.released_at IS NULL THEN
        RAISE EXCEPTION 'endpoint custody hold may only be released once'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$fn$;
DROP TRIGGER IF EXISTS guard_custody_holds_rows ON public.custody_holds;
CREATE TRIGGER guard_custody_holds_rows
    BEFORE INSERT OR UPDATE OR DELETE ON public.custody_holds
    FOR EACH ROW EXECUTE FUNCTION public.guard_custody_holds();
DROP TRIGGER IF EXISTS guard_custody_holds_truncate ON public.custody_holds;
CREATE TRIGGER guard_custody_holds_truncate
    BEFORE TRUNCATE ON public.custody_holds
    FOR EACH STATEMENT EXECUTE FUNCTION public.guard_custody_holds();
"""

# Content-blind eligibility read: a boolean for one stored fact, nothing else.
_HELD_READ = """
CREATE OR REPLACE FUNCTION public.custody_target_held(
    p_predicate text,
    p_object text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT EXISTS (
        SELECT 1
        FROM public.custody_holds h
        WHERE h.released_at IS NULL
          AND h.target_kind = p_predicate
          AND h.target_digest = public.custody_target_digest(p_predicate, p_object)
    )
$fn$;
REVOKE ALL ON FUNCTION public.custody_target_held(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.custody_target_held(text, text) TO PUBLIC;
"""

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
            bool_or('owner' = ANY(COALESCE(e.roles, ARRAY[]::text[]))) AS is_owner{held_column}
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
                    p_predicate <> 'owner-channel'
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
      AND matches.is_owner{held_filter}
    LIMIT 1;
END;
$fn$;
"""

# A held matched fact still counts toward ambiguity; it only refuses owner.
_HOLD_AWARE_RESOLVE = _RESOLVE_TEMPLATE.format(
    held_column=(
        ",\n            bool_or(public.custody_target_held(ef.predicate, ef.object)) AS is_held"
    ),
    held_filter="\n      AND NOT matches.is_held",
)

# Exactly the core_233 body, with the search path core_252 pinned.
_CORE_233_RESOLVE = _RESOLVE_TEMPLATE.format(held_column="", held_filter="")


def _maintenance_owner() -> None:
    # Same posture as core_275: the database's trusted owner keeps the table
    # and its eligibility read, without FORCE, and no runtime may assume it.
    roles = ",".join(f"'butler_{name}_rw'" for name in _RUNTIME_ROLES)
    op.execute(f"""DO $$ DECLARE trusted_owner oid; BEGIN
        SELECT datdba INTO STRICT trusted_owner FROM pg_database
            WHERE datname = current_database();
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ({roles}, 'connector_writer')
            AND (rolsuper OR rolbypassrls OR pg_has_role(oid, trusted_owner, 'MEMBER'))) THEN
            RAISE EXCEPTION 'Runtime may not bypass or assume custody maintenance owner';
        END IF;
        EXECUTE format('ALTER TABLE public.custody_holds OWNER TO %I',
            pg_get_userbyid(trusted_owner));
        EXECUTE format(
            'ALTER FUNCTION public.custody_target_held(text, text) OWNER TO %I',
            pg_get_userbyid(trusted_owner));
    END $$""")
    op.execute("""
        ALTER TABLE public.custody_holds ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.custody_holds NO FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON TABLE public.custody_holds FROM PUBLIC;
    """)


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(_TABLE)
    op.execute(_TARGET_DIGEST)
    op.execute(_GUARD)
    op.execute(_HELD_READ)
    _maintenance_owner()
    op.execute(_HOLD_AWARE_RESOLVE)


def downgrade() -> None:
    op.execute(_LOCK)
    # Row security hides holds from anyone but the maintenance owner, so a
    # login that cannot see every row must not decide that none is active.
    op.execute("""DO $$ BEGIN
        IF to_regclass('public.custody_holds') IS NULL THEN
            RETURN;
        END IF;
        IF NOT pg_has_role(current_user, (SELECT relowner FROM pg_class
                WHERE oid = 'public.custody_holds'::regclass), 'USAGE') THEN
            RAISE EXCEPTION 'core_293 downgrade refused: only the custody maintenance owner '
                'can prove no endpoint custody hold is active' USING ERRCODE = '42501';
        END IF;
        IF EXISTS (SELECT 1 FROM public.custody_holds WHERE released_at IS NULL) THEN
            RAISE EXCEPTION 'core_293 downgrade refused: an endpoint custody hold is active'
                USING ERRCODE = '55000';
        END IF;
    END $$""")
    op.execute(_CORE_233_RESOLVE)
    op.execute("DROP FUNCTION IF EXISTS public.custody_target_held(text, text)")
    op.execute("DROP TABLE IF EXISTS public.custody_holds")
    op.execute("DROP FUNCTION IF EXISTS public.guard_custody_holds()")
    op.execute("DROP FUNCTION IF EXISTS public.custody_target_digest(text, text)")
