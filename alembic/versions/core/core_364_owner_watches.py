"""Durable owner watches (bu-4c7nvp.1.5 S3b).

Revision ID: core_364
Revises: core_363
Create Date: 2026-11-23 00:00:00.000000

REQ-owner-watches-003/004. Persistence for ``butlers.watches``: one row per owner-approved,
notify-only watch. The watch is an evaluator instance (``public.condition_instances``, core_323)
approved on an exact card; this table holds the lifecycle around it. It lives in ``public``
because condition instances do, and a butler role sees only its own schema plus ``public``.

==============================  ===================================================
invariant (trigger-enforced)
==============================  ===================================================
identity, predicate, digests, source and category boundary, creation time: immutable
version rises by exactly one per update; generation never moves backward and rises by at most
one; expiry only moves forward and only with a new generation (a renewal)
state: active -> paused | expired | deleted; paused -> active | expired | deleted; an expired or
deleted row is frozen
at most 50 rows are active (an insert or activation takes one advisory lock, then counts)
a row is never deleted or truncated, by any role: pausing, expiry and deletion keep it
==============================  ===================================================

Only the Switchboard runtime role (``butler_switchboard_rw``, the trusted evaluator) is granted
anything. ``scripts/init-db.sql`` re-grants public-table DML to every runtime role on a bootstrap
replay, so row-level security (ENABLE, not FORCE, the core_241/core_317/core_323 posture) is the
durable fence: every operation requires ``current_user = 'butler_switchboard_rw'``.

The core chain runs once per butler schema over this shared table, so every statement is
idempotent and only the last schema to leave core_364 drops it, and only while it is empty.
"""

from __future__ import annotations

from alembic import op
from butlers.db_authority.roles import butler_roles

revision = "core_364"
down_revision = "core_363"
branch_labels = None
depends_on = None

_SWITCHBOARD = "butler_switchboard_rw"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_364:owner-watches', 0))"
_ROLLBACK_REFUSAL = (
    "core_364 cannot drop the owner watches while any exist; keep core_364 installed."
)
_HEX64 = "'^[0-9a-f]{64}$'"
MAX_ACTIVE = 50

_DDL = f"""
CREATE TABLE IF NOT EXISTS public.owner_watches (
    id              TEXT PRIMARY KEY CHECK (char_length(id) BETWEEN 1 AND 128),
    version         BIGINT NOT NULL CHECK (version >= 1),
    generation      INTEGER NOT NULL CHECK (generation >= 1),
    predicate       JSONB NOT NULL CHECK (jsonb_typeof(predicate) = 'object'),
    predicate_digest TEXT NOT NULL CHECK (predicate_digest ~ {_HEX64}),
    grant_digest    TEXT NOT NULL CHECK (grant_digest ~ {_HEX64}),
    sources         TEXT[] NOT NULL CHECK (cardinality(sources) BETWEEN 1 AND 64),
    categories      TEXT[] NOT NULL CHECK (cardinality(categories) <= 6),
    delivery        TEXT NOT NULL CHECK (delivery IN ('digest', 'right_away')),
    state           TEXT NOT NULL CHECK (state IN ('active', 'paused', 'expired', 'deleted')),
    created_at      TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    approval_digest TEXT NOT NULL CHECK (approval_digest ~ {_HEX64}),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (created_at < expires_at)
);
CREATE INDEX IF NOT EXISTS owner_watches_state_idx ON public.owner_watches (state);
"""

_TRIGGERS = rf"""
CREATE OR REPLACE FUNCTION public.owner_watch_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'owner watches are durable' USING ERRCODE = '42501';
    END IF;
    IF ROW(NEW.id, NEW.predicate, NEW.predicate_digest, NEW.grant_digest, NEW.sources,
           NEW.categories, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.predicate, OLD.predicate_digest, OLD.grant_digest, OLD.sources,
           OLD.categories, OLD.created_at) THEN
        RAISE EXCEPTION 'an owner watch boundary is immutable' USING ERRCODE = '42501';
    END IF;
    IF OLD.state IN ('expired', 'deleted') THEN
        RAISE EXCEPTION 'a closed owner watch is frozen' USING ERRCODE = '42501';
    END IF;
    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'owner watch version must advance by one' USING ERRCODE = '40001';
    END IF;
    IF NEW.generation < OLD.generation OR NEW.generation > OLD.generation + 1 THEN
        RAISE EXCEPTION 'owner watch generation moves forward by at most one'
            USING ERRCODE = '42501';
    END IF;
    IF NEW.expires_at < OLD.expires_at
       OR (NEW.expires_at > OLD.expires_at AND NEW.generation = OLD.generation) THEN
        RAISE EXCEPTION 'owner watch expiry moves forward only with a renewal'
            USING ERRCODE = '42501';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.owner_watch_cap() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.state = 'active' AND (TG_OP = 'INSERT' OR OLD.state <> 'active') THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('butlers:owner-watch-cap', 0));
        IF (SELECT count(*) FROM public.owner_watches w
            WHERE w.state = 'active' AND w.id <> NEW.id) >= {MAX_ACTIVE} THEN
            RAISE EXCEPTION 'at most {MAX_ACTIVE} owner watches may be active'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.owner_watch_no_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'owner watches are durable' USING ERRCODE = '42501';
END $$;

DROP TRIGGER IF EXISTS owner_watch_guard ON public.owner_watches;
CREATE TRIGGER owner_watch_guard BEFORE UPDATE OR DELETE ON public.owner_watches
    FOR EACH ROW EXECUTE FUNCTION public.owner_watch_guard();
DROP TRIGGER IF EXISTS owner_watch_cap ON public.owner_watches;
CREATE TRIGGER owner_watch_cap BEFORE INSERT OR UPDATE ON public.owner_watches
    FOR EACH ROW EXECUTE FUNCTION public.owner_watch_cap();
DROP TRIGGER IF EXISTS owner_watches_no_truncate ON public.owner_watches;
CREATE TRIGGER owner_watches_no_truncate BEFORE TRUNCATE ON public.owner_watches
    FOR EACH STATEMENT EXECUTE FUNCTION public.owner_watch_no_truncate();
"""


def _for_existing_role(role: str, statement: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE '{statement}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege OR undefined_object OR undefined_table THEN NULL;
        END $$
        """
    )


def _grants_and_policy() -> None:
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.owner_watches FROM PUBLIC")
    for role in (*butler_roles(), "connector_writer"):
        # Switchboard too: init-db default privileges hand it blanket DML (with DELETE)
        # before the exact grant below.
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON TABLE public.owner_watches FROM {role}")
    _for_existing_role(
        _SWITCHBOARD,
        f"GRANT SELECT, INSERT, UPDATE ON TABLE public.owner_watches TO {_SWITCHBOARD}",
    )
    op.execute("ALTER TABLE public.owner_watches ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.owner_watches NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS owner_watches_switchboard ON public.owner_watches")
    op.execute(
        f"""
        CREATE POLICY owner_watches_switchboard ON public.owner_watches
            FOR ALL TO PUBLIC
            USING (current_user = '{_SWITCHBOARD}')
            WITH CHECK (current_user = '{_SWITCHBOARD}')
        """
    )


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the shared table."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND left(namespace.nspname, 3) <> 'pg_'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 364
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


# Catalog-only: the table, its three triggers and functions, the index, the policy, the RLS flags
# (enabled, not forced) and the exact ACL (the Switchboard role exactly SELECT+INSERT+UPDATE, no
# other grantee) are all in place. A later schema's chain that finds them returns without DDL,
# because CREATE OR REPLACE TRIGGER and CREATE INDEX IF NOT EXISTS lock the table even as a no-op.
# ``to_regclass`` takes no relation lock.
_ALREADY_INSTALLED = f"""
SELECT to_regclass('public.owner_watches') IS NOT NULL
AND (
    SELECT count(*) FROM pg_trigger
    WHERE tgrelid = to_regclass('public.owner_watches') AND NOT tgisinternal
      AND tgname IN ('owner_watch_guard', 'owner_watch_cap', 'owner_watches_no_truncate')
) = 3
AND (
    SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.proname IN ('owner_watch_guard', 'owner_watch_cap', 'owner_watch_no_truncate')
) = 3
AND EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'owner_watches_state_idx'
)
AND EXISTS (
    SELECT 1 FROM pg_policy
    WHERE polrelid = to_regclass('public.owner_watches')
      AND polname = 'owner_watches_switchboard'
)
AND EXISTS (
    SELECT 1 FROM pg_class
    WHERE oid = to_regclass('public.owner_watches')
      AND relrowsecurity AND NOT relforcerowsecurity
)
AND NOT EXISTS (
    SELECT 1 FROM pg_class c
    CROSS JOIN LATERAL aclexplode(c.relacl) AS a
    LEFT JOIN pg_roles r ON r.oid = a.grantee
    WHERE c.oid = to_regclass('public.owner_watches') AND a.grantee <> c.relowner
      AND (r.rolname IS DISTINCT FROM '{_SWITCHBOARD}'
           OR a.privilege_type NOT IN ('SELECT', 'INSERT', 'UPDATE'))
)
AND (
    NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_SWITCHBOARD}')
    OR (
        SELECT count(*) FROM pg_class c
        CROSS JOIN LATERAL aclexplode(c.relacl) AS a
        JOIN pg_roles r ON r.oid = a.grantee
        WHERE c.oid = to_regclass('public.owner_watches')
          AND r.rolname = '{_SWITCHBOARD}'
          AND a.privilege_type IN ('SELECT', 'INSERT', 'UPDATE')
    ) = 3
)
"""


def upgrade() -> None:
    op.execute(_LOCK)
    if op.get_bind().exec_driver_sql(_ALREADY_INSTALLED).scalar():
        return
    op.execute(_DDL)
    op.execute(_TRIGGERS)
    _grants_and_policy()


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.owner_watches') IS NOT NULL THEN
                -- Wait out every uncommitted insert first: a row the check cannot see yet would
                -- otherwise commit while the DROP waits, and the DROP would erase it.
                LOCK TABLE public.owner_watches IN ACCESS EXCLUSIVE MODE;
                IF EXISTS (SELECT 1 FROM public.owner_watches) THEN
                    RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
                END IF;
            END IF;
        END
        $$
        """
    )
    op.execute("DROP TABLE IF EXISTS public.owner_watches CASCADE")
    for function in ("owner_watch_guard", "owner_watch_cap", "owner_watch_no_truncate"):
        op.execute(f"DROP FUNCTION IF EXISTS public.{function}()")
