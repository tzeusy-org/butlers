"""Durable condition store: instances, cursors, receipts, fire intents (bu-dhloi1.194).

Revision ID: core_323
Revises: core_322
Create Date: 2026-11-09 00:00:00.000000

RFC 0038 S4, REQ-condition-evaluation-001/002. Persistence for ``butlers.conditions``: the
trusted evaluator's instance row (immutable predicate/grant/source boundary and replay floor,
version CAS), its advancing per-source high-water cursors, an append-only receipt per
evaluation, and the unique fire intent (the outbox row the goal runner consumes). The four
tables live in ``public`` because RFC 0038 S3 makes condition instances shared coordination
projections, and a butler role sees only its own schema plus ``public``.

==============================  ===================================================
table                           invariant (trigger-enforced unless noted)
==============================  ===================================================
``condition_instances``         identity, predicate, digests, boundary, floor, expiry and
                                generation immutable; version rises by exactly one per
                                update; a fired/expired/cancelled row is frozen; never
                                deleted
``condition_cursors``           one row per ``(instance, source)``; position never moves
                                backward; never deleted
``condition_receipts``          append-only
``condition_fire_intents``      one per ``fire_key`` and per ``(instance, generation)``;
                                inserted only for a fired instance of that generation;
                                only ``claimed_by`` may be set, once; never deleted
==============================  ===================================================

Only the Switchboard runtime role (``butler_switchboard_rw``, the trusted evaluator) is
granted anything. ``scripts/init-db.sql`` re-grants public-table DML to every runtime role on
a bootstrap replay, so row-level security (ENABLE, not FORCE, the core_241/core_317
posture: the table owner used by migrations and backups keeps its bypass) is the durable
fence: every operation requires ``current_user = 'butler_switchboard_rw'``. No row is ever
deleted or truncated, by any role.

The core chain runs once per butler schema over these shared tables, so every statement is
idempotent and only the last schema to leave core_323 drops them, and only while no
instance exists.
"""

from __future__ import annotations

from alembic import op
from butlers.db_authority.roles import butler_roles

revision = "core_323"
down_revision = "core_322"
branch_labels = None
depends_on = None

_SWITCHBOARD = "butler_switchboard_rw"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_323:condition-store', 0))"
_TABLES = (
    "condition_fire_intents",
    "condition_receipts",
    "condition_cursors",
    "condition_instances",
)
_ROLLBACK_REFUSAL = (
    "core_323 cannot drop the condition store while it holds instances; keep core_323 installed."
)
_HEX64 = "'^[0-9a-f]{64}$'"

_DDL = f"""
CREATE TABLE IF NOT EXISTS public.condition_instances (
    id               TEXT PRIMARY KEY CHECK (char_length(id) BETWEEN 1 AND 128),
    goal_id          TEXT NOT NULL CHECK (char_length(goal_id) BETWEEN 1 AND 128),
    predicate        JSONB NOT NULL CHECK (jsonb_typeof(predicate) = 'object'),
    predicate_digest TEXT NOT NULL CHECK (predicate_digest ~ {_HEX64}),
    grant_digest     TEXT NOT NULL CHECK (grant_digest ~ {_HEX64}),
    source_boundary  TEXT[] NOT NULL CHECK (cardinality(source_boundary) BETWEEN 1 AND 16),
    starts_at        TIMESTAMPTZ NOT NULL,
    expires_at       TIMESTAMPTZ NOT NULL,
    generation       INTEGER NOT NULL CHECK (generation >= 1),
    state            TEXT NOT NULL DEFAULT 'open'
                     CHECK (state IN ('open', 'fired', 'expired', 'cancelled')),
    result           TEXT NOT NULL CHECK (result IN ('true', 'false', 'unknown')),
    leaves           JSONB NOT NULL CHECK (jsonb_typeof(leaves) = 'object'),
    floor            JSONB NOT NULL CHECK (jsonb_typeof(floor) = 'object'),
    version          BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (starts_at < expires_at),
    UNIQUE (id, generation)
);

CREATE TABLE IF NOT EXISTS public.condition_cursors (
    instance_id TEXT NOT NULL REFERENCES public.condition_instances (id),
    source      TEXT NOT NULL CHECK (char_length(source) BETWEEN 1 AND 128),
    position    BIGINT NOT NULL CHECK (position >= 0),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (instance_id, source)
);

CREATE TABLE IF NOT EXISTS public.condition_receipts (
    id                  BIGSERIAL PRIMARY KEY,
    instance_id         TEXT NOT NULL REFERENCES public.condition_instances (id),
    generation          INTEGER NOT NULL CHECK (generation >= 1),
    kind                TEXT NOT NULL CHECK (kind IN ('register', 'evaluate', 'expire', 'cancel')),
    result              TEXT NOT NULL CHECK (result IN ('true', 'false', 'unknown')),
    state               TEXT NOT NULL CHECK (state IN ('open', 'fired', 'expired', 'cancelled')),
    reason              TEXT NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 256),
    trigger_event_id    UUID,
    cursors             JSONB NOT NULL CHECK (jsonb_typeof(cursors) = 'object'),
    at                  TIMESTAMPTZ NOT NULL,
    snapshot_generation BIGINT NOT NULL DEFAULT 0,
    clock               JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS condition_receipts_instance_idx
    ON public.condition_receipts (instance_id, id);

CREATE TABLE IF NOT EXISTS public.condition_fire_intents (
    fire_key            UUID PRIMARY KEY,
    instance_id         TEXT NOT NULL,
    generation          INTEGER NOT NULL CHECK (generation >= 1),
    event_id            UUID NOT NULL,
    reason              TEXT NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 256),
    fired_at            TIMESTAMPTZ NOT NULL,
    snapshot_generation BIGINT NOT NULL DEFAULT 0,
    cursors             JSONB NOT NULL CHECK (jsonb_typeof(cursors) = 'object'),
    claimed_by          TEXT
                        CHECK (claimed_by IS NULL OR char_length(claimed_by) BETWEEN 1 AND 128),
    claimed_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (instance_id, generation)
        REFERENCES public.condition_instances (id, generation),
    UNIQUE (instance_id, generation),
    CHECK ((claimed_by IS NULL) = (claimed_at IS NULL))
);
"""

_TRIGGERS = r"""
CREATE OR REPLACE FUNCTION public.condition_instance_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'condition instances are durable' USING ERRCODE = '42501';
    END IF;
    IF ROW(NEW.id, NEW.goal_id, NEW.predicate, NEW.predicate_digest, NEW.grant_digest,
           NEW.source_boundary, NEW.starts_at, NEW.expires_at, NEW.generation, NEW.floor,
           NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.goal_id, OLD.predicate, OLD.predicate_digest, OLD.grant_digest,
           OLD.source_boundary, OLD.starts_at, OLD.expires_at, OLD.generation, OLD.floor,
           OLD.created_at) THEN
        RAISE EXCEPTION 'condition instance boundary is immutable' USING ERRCODE = '42501';
    END IF;
    IF OLD.state <> 'open' THEN
        RAISE EXCEPTION 'a closed condition instance is frozen' USING ERRCODE = '42501';
    END IF;
    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'condition instance version must advance by one' USING ERRCODE = '40001';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.condition_cursor_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'condition cursors are durable' USING ERRCODE = '42501';
    END IF;
    IF NEW.instance_id <> OLD.instance_id OR NEW.source <> OLD.source
       OR NEW.position < OLD.position THEN
        RAISE EXCEPTION 'a condition cursor never moves backward' USING ERRCODE = '42501';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.condition_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'condition receipts are append-only' USING ERRCODE = '42501';
END $$;

CREATE OR REPLACE FUNCTION public.condition_intent_fence() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.condition_instances i
                   WHERE i.id = NEW.instance_id AND i.generation = NEW.generation
                     AND i.state = 'fired') THEN
        RAISE EXCEPTION 'a fire intent needs its fired instance' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.condition_intent_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'condition fire intents are durable' USING ERRCODE = '42501';
    END IF;
    IF ROW(NEW.fire_key, NEW.instance_id, NEW.generation, NEW.event_id, NEW.reason,
           NEW.fired_at, NEW.snapshot_generation, NEW.cursors, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.fire_key, OLD.instance_id, OLD.generation, OLD.event_id, OLD.reason,
           OLD.fired_at, OLD.snapshot_generation, OLD.cursors, OLD.created_at)
       OR OLD.claimed_by IS NOT NULL THEN
        RAISE EXCEPTION 'a fire intent is immutable once written; its claim is set once'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.condition_no_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'the condition store is durable' USING ERRCODE = '42501';
END $$;

DROP TRIGGER IF EXISTS condition_instance_guard ON public.condition_instances;
CREATE TRIGGER condition_instance_guard BEFORE UPDATE OR DELETE ON public.condition_instances
    FOR EACH ROW EXECUTE FUNCTION public.condition_instance_guard();
DROP TRIGGER IF EXISTS condition_cursor_guard ON public.condition_cursors;
CREATE TRIGGER condition_cursor_guard BEFORE UPDATE OR DELETE ON public.condition_cursors
    FOR EACH ROW EXECUTE FUNCTION public.condition_cursor_guard();
DROP TRIGGER IF EXISTS condition_receipts_append_only ON public.condition_receipts;
CREATE TRIGGER condition_receipts_append_only BEFORE UPDATE OR DELETE ON public.condition_receipts
    FOR EACH ROW EXECUTE FUNCTION public.condition_append_only();
DROP TRIGGER IF EXISTS condition_intent_fence ON public.condition_fire_intents;
CREATE TRIGGER condition_intent_fence BEFORE INSERT ON public.condition_fire_intents
    FOR EACH ROW EXECUTE FUNCTION public.condition_intent_fence();
DROP TRIGGER IF EXISTS condition_intent_guard ON public.condition_fire_intents;
CREATE TRIGGER condition_intent_guard BEFORE UPDATE OR DELETE ON public.condition_fire_intents
    FOR EACH ROW EXECUTE FUNCTION public.condition_intent_guard();
"""

_TRUNCATE_GUARDS = "\n".join(
    f"DROP TRIGGER IF EXISTS {table}_no_truncate ON public.{table};\n"
    f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON public.{table}\n"
    "    FOR EACH STATEMENT EXECUTE FUNCTION public.condition_no_truncate();"
    for table in _TABLES
)


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


def _grants_and_policies() -> None:
    tables = ", ".join(f"public.{t}" for t in _TABLES)
    sequence = "public.condition_receipts_id_seq"
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {tables} FROM PUBLIC")
    op.execute(f"REVOKE ALL PRIVILEGES ON SEQUENCE {sequence} FROM PUBLIC")
    for role in (*butler_roles(), "connector_writer"):
        # Switchboard too: init-db default privileges hand it blanket DML (with DELETE)
        # before the exact grants below.
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON TABLE {tables} FROM {role}")
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON SEQUENCE {sequence} FROM {role}")
    grants = (
        "GRANT SELECT, INSERT, UPDATE ON TABLE public.condition_instances, "
        "public.condition_cursors, public.condition_fire_intents "
        f"TO {_SWITCHBOARD}",
        f"GRANT SELECT, INSERT ON TABLE public.condition_receipts TO {_SWITCHBOARD}",
        f"GRANT USAGE, SELECT ON SEQUENCE {sequence} TO {_SWITCHBOARD}",
    )
    for grant in grants:
        _for_existing_role(_SWITCHBOARD, grant)
    for table in _TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_switchboard ON public.{table}")
        op.execute(
            f"""
            CREATE POLICY {table}_switchboard ON public.{table}
                FOR ALL TO PUBLIC
                USING (current_user = '{_SWITCHBOARD}')
                WITH CHECK (current_user = '{_SWITCHBOARD}')
            """
        )


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the shared tables."""
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
                    THEN substring(version_num FROM 6)::integer >= 323
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(_DDL)
    op.execute(_TRIGGERS)
    op.execute(_TRUNCATE_GUARDS)
    _grants_and_policies()


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.condition_instances') IS NOT NULL
               AND EXISTS (SELECT 1 FROM public.condition_instances) THEN
                RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
            END IF;
        END
        $$
        """
    )
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
    for function in (
        "condition_instance_guard",
        "condition_cursor_guard",
        "condition_append_only",
        "condition_intent_fence",
        "condition_intent_guard",
        "condition_no_truncate",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS public.{function}()")
