"""Canonical owner-delivery ledger: subjects, presentations and action tokens (bu-dhloi1.13).

Revision ID: core_317
Revises: core_316
Create Date: 2026-11-05 00:00:00.000000

RFC 0038 S6, REQ-owner-delivery-001/002. One subject references exactly one existing
authority row (an approval ``pending_actions`` id, through which a parked goal effect is
also decided; or, representation only, a goal answer or pending ask). It is not another
approval authority: the decision itself stays the owning schema's
``transition_pending_action``. The tables live in ``public`` because S6 makes subject CAS,
the approval decision and the receipt one transaction, and a butler role sees only its own
schema plus ``public``.

==============================  ===================================================
table                           invariant (trigger-enforced unless noted)
==============================  ===================================================
``owner_delivery_subjects``     identity and digests immutable; generation never
                                decreases; closed state edges; terminal rows frozen;
                                decision set once, only on ``decided``
``owner_delivery_presentations``  one row per ``(subject, surface, generation)``
                                (``retry_key``); inserted only at the subject's
                                current generation while it is open; provider ref
                                set once; closed state edges (a proven failed send
                                may return to pending; its tokens are revoked)
``owner_delivery_tokens``       only a SHA-256 verifier is stored; minted only at the
                                current generation of an open subject; every binding
                                immutable; consumed exactly once
``owner_delivery_receipts``     append-only
``owner_delivery_source_map``   append-only old-store id -> subject map
``owner_delivery_rollout``      singleton, default off; no runtime write policy
==============================  ===================================================

No row is ever deleted by a runtime role. Row security is ENABLE (not FORCE), the
core_303 posture: the table owner (dashboard, migrations, backups) keeps its bypass, and
a runtime connection sees only rows whose ``owning_schema`` is its own
(``butler_<schema>_rw``), whatever ``init-db.sql`` re-grants. ``connector_writer`` holds
nothing.

The core chain runs once per butler schema over these shared tables, so every statement
is idempotent and only the last schema to leave core_317 drops them, and only while no
subject exists: receipts and old-id mappings survive a source rollback, which disables
the rollout flag instead.
"""

from __future__ import annotations

from alembic import op

revision = "core_317"
down_revision = "core_316"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_317:owner-delivery', 0))"
_TABLES = (
    "owner_delivery_tokens",
    "owner_delivery_receipts",
    "owner_delivery_source_map",
    "owner_delivery_presentations",
    "owner_delivery_subjects",
    "owner_delivery_rollout",
)
_OWNED = _TABLES[:-1]
_HEX64 = "'^[0-9a-f]{64}$'"
_SCHEMA = "'^[a-z][a-z0-9_]*$'"
_ROLLBACK_REFUSAL = (
    "core_317 cannot drop the owner-delivery ledger while it holds subjects. "
    "Disable public.owner_delivery_rollout instead and keep core_317 installed."
)

_DDL = f"""
CREATE TABLE IF NOT EXISTS public.owner_delivery_rollout (
    singleton  BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    enabled    BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO public.owner_delivery_rollout (singleton) VALUES (true) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS public.owner_delivery_subjects (
    id              UUID PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('approval', 'goal_answer', 'pending_ask')),
    owning_schema   TEXT NOT NULL CHECK (owning_schema ~ {_SCHEMA}),
    source_ref      TEXT NOT NULL CHECK (char_length(source_ref) BETWEEN 1 AND 256),
    goal_effect_id  UUID,
    owner_entity    UUID NOT NULL,
    content_digest  TEXT NOT NULL CHECK (content_digest ~ {_HEX64}),
    action_digest   TEXT NOT NULL CHECK (action_digest ~ {_HEX64}),
    generation      INTEGER NOT NULL DEFAULT 1 CHECK (generation >= 1),
    state           TEXT NOT NULL DEFAULT 'pending' CHECK (state IN (
                        'pending', 'delivered', 'acknowledged', 'decided',
                        'cancelled', 'expired', 'unknown')),
    decision        TEXT CHECK (decision IN ('approve', 'reject')),
    decided_surface TEXT CHECK (decided_surface IN ('dashboard', 'telegram')),
    resolved_at     TIMESTAMPTZ,
    -- Typed slots for bu-dhloi1.22 (urgency) and bu-dhloi1.10 (owner-law holding
    -- receipt). No urgency vocabulary is defined here, so none is admissible yet.
    urgency_class   TEXT CHECK (urgency_class IS NULL),
    holding_reason  JSONB CHECK (holding_reason IS NULL OR (
                        jsonb_typeof(holding_reason) = 'object'
                        AND jsonb_typeof(holding_reason -> 'stage') = 'string'
                        AND jsonb_typeof(holding_reason -> 'generation') = 'number'
                        AND jsonb_typeof(holding_reason -> 'terms') = 'array')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (owning_schema, kind, source_ref),
    UNIQUE (id, owning_schema),
    CHECK ((state = 'decided') = (decision IS NOT NULL)),
    CHECK ((state IN ('decided', 'cancelled', 'expired')) = (resolved_at IS NOT NULL)),
    CHECK (decided_surface IS NULL OR state IN ('decided', 'acknowledged'))
);

CREATE TABLE IF NOT EXISTS public.owner_delivery_presentations (
    id            UUID PRIMARY KEY,
    subject_id    UUID NOT NULL,
    owning_schema TEXT NOT NULL,
    generation    INTEGER NOT NULL CHECK (generation >= 1),
    surface       TEXT NOT NULL CHECK (surface IN ('dashboard', 'telegram')),
    route_ref     TEXT NOT NULL CHECK (char_length(route_ref) BETWEEN 1 AND 256),
    retry_key     TEXT NOT NULL UNIQUE,
    provider_ref  TEXT CHECK (provider_ref IS NULL OR char_length(provider_ref) BETWEEN 1 AND 256),
    state         TEXT NOT NULL DEFAULT 'pending' CHECK (state IN (
                      'pending', 'sent', 'failed', 'unknown', 'retracted')),
    legacy_ref    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (subject_id, owning_schema)
        REFERENCES public.owner_delivery_subjects (id, owning_schema),
    UNIQUE (subject_id, surface, generation),
    UNIQUE (id, subject_id),
    CHECK (retry_key = subject_id::text || ':' || surface || ':' || generation::text)
);

CREATE TABLE IF NOT EXISTS public.owner_delivery_tokens (
    verifier        TEXT PRIMARY KEY CHECK (verifier ~ {_HEX64}),
    subject_id      UUID NOT NULL,
    owning_schema   TEXT NOT NULL,
    presentation_id UUID NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'ack')),
    action_digest   TEXT NOT NULL CHECK (action_digest ~ {_HEX64}),
    owner_entity    UUID NOT NULL,
    surface         TEXT NOT NULL CHECK (surface IN ('dashboard', 'telegram')),
    generation      INTEGER NOT NULL CHECK (generation >= 1),
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    consumed_at     TIMESTAMPTZ,
    outcome         JSONB CHECK (outcome IS NULL OR jsonb_typeof(outcome) = 'object'),
    FOREIGN KEY (subject_id, owning_schema)
        REFERENCES public.owner_delivery_subjects (id, owning_schema),
    FOREIGN KEY (presentation_id, subject_id)
        REFERENCES public.owner_delivery_presentations (id, subject_id),
    CHECK ((consumed_at IS NULL) = (outcome IS NULL)),
    CHECK (expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS ix_owner_delivery_tokens_subject
    ON public.owner_delivery_tokens (subject_id, generation);

CREATE TABLE IF NOT EXISTS public.owner_delivery_receipts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id      UUID NOT NULL,
    owning_schema   TEXT NOT NULL,
    presentation_id UUID REFERENCES public.owner_delivery_presentations (id),
    kind            TEXT NOT NULL CHECK (kind IN (
                        'opened', 'presented', 'sent', 'send_failed', 'send_unknown',
                        'acknowledged', 'decided', 'closed', 'refused', 'retracted',
                        'retract_failed', 'bridged')),
    generation      INTEGER NOT NULL CHECK (generation >= 1),
    detail          JSONB NOT NULL DEFAULT '{{}}'::jsonb CHECK (jsonb_typeof(detail) = 'object'),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (subject_id, owning_schema)
        REFERENCES public.owner_delivery_subjects (id, owning_schema)
);
CREATE INDEX IF NOT EXISTS ix_owner_delivery_receipts_subject
    ON public.owner_delivery_receipts (subject_id, created_at);

CREATE TABLE IF NOT EXISTS public.owner_delivery_source_map (
    owning_schema   TEXT NOT NULL,
    source_store    TEXT NOT NULL CHECK (source_store IN (
                        'approval_action', 'rfc0023_intent', 'rfc0023_presentation',
                        'rfc0023_attempt', 'messenger_handoff', 'attention')),
    source_id       TEXT NOT NULL CHECK (char_length(source_id) BETWEEN 1 AND 256),
    subject_id      UUID NOT NULL,
    presentation_id UUID REFERENCES public.owner_delivery_presentations (id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (owning_schema, source_store, source_id),
    FOREIGN KEY (subject_id, owning_schema)
        REFERENCES public.owner_delivery_subjects (id, owning_schema)
);
"""

_TRIGGERS = r"""
CREATE OR REPLACE FUNCTION public.owner_delivery_subject_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'owner-delivery subjects are durable' USING ERRCODE = '42501';
    END IF;
    IF ROW(NEW.id, NEW.kind, NEW.owning_schema, NEW.source_ref, NEW.goal_effect_id,
           NEW.owner_entity, NEW.content_digest, NEW.action_digest, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.kind, OLD.owning_schema, OLD.source_ref, OLD.goal_effect_id,
           OLD.owner_entity, OLD.content_digest, OLD.action_digest, OLD.created_at) THEN
        RAISE EXCEPTION 'owner-delivery subject identity is immutable' USING ERRCODE = '42501';
    END IF;
    IF OLD.state IN ('decided', 'cancelled', 'expired') AND
       ROW(NEW.state, NEW.generation, NEW.decision, NEW.decided_surface, NEW.resolved_at,
           NEW.urgency_class, NEW.holding_reason)
       IS DISTINCT FROM
       ROW(OLD.state, OLD.generation, OLD.decision, OLD.decided_surface, OLD.resolved_at,
           OLD.urgency_class, OLD.holding_reason) THEN
        RAISE EXCEPTION 'a resolved owner-delivery subject is frozen' USING ERRCODE = '42501';
    END IF;
    IF NEW.generation < OLD.generation THEN
        RAISE EXCEPTION 'owner-delivery generation never decreases' USING ERRCODE = '42501';
    END IF;
    IF NEW.state <> OLD.state AND NOT (
        (OLD.state = 'pending' AND NEW.state IN
            ('delivered', 'acknowledged', 'decided', 'cancelled', 'expired', 'unknown'))
        OR (OLD.state IN ('delivered', 'unknown') AND NEW.state IN
            ('delivered', 'acknowledged', 'decided', 'cancelled', 'expired'))
        OR (OLD.state = 'acknowledged' AND NEW.state IN ('decided', 'cancelled', 'expired'))
    ) THEN
        RAISE EXCEPTION 'owner-delivery subject cannot move from % to %', OLD.state, NEW.state
            USING ERRCODE = '42501';
    END IF;
    IF OLD.decided_surface IS NOT NULL AND NEW.decided_surface IS DISTINCT FROM OLD.decided_surface
       AND OLD.state = NEW.state THEN
        RAISE EXCEPTION 'owner-delivery winning surface is set once' USING ERRCODE = '42501';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.owner_delivery_open_fence() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE current_generation INTEGER; current_state TEXT;
BEGIN
    SELECT generation, state INTO current_generation, current_state
      FROM public.owner_delivery_subjects WHERE id = NEW.subject_id;
    IF current_state IS NULL OR current_state IN ('decided', 'cancelled', 'expired')
       OR NEW.generation <> current_generation THEN
        RAISE EXCEPTION 'owner-delivery rows are minted only at an open subject''s generation'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.owner_delivery_presentation_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'owner-delivery presentations are durable' USING ERRCODE = '42501';
    END IF;
    IF ROW(NEW.id, NEW.subject_id, NEW.owning_schema, NEW.generation, NEW.surface,
           NEW.route_ref, NEW.retry_key, NEW.legacy_ref, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.subject_id, OLD.owning_schema, OLD.generation, OLD.surface,
           OLD.route_ref, OLD.retry_key, OLD.legacy_ref, OLD.created_at) THEN
        RAISE EXCEPTION 'owner-delivery presentation identity is immutable'
            USING ERRCODE = '42501';
    END IF;
    IF OLD.provider_ref IS NOT NULL AND NEW.provider_ref IS DISTINCT FROM OLD.provider_ref THEN
        RAISE EXCEPTION 'owner-delivery provider reference is set once' USING ERRCODE = '42501';
    END IF;
    IF NEW.state <> OLD.state AND NOT (
        (OLD.state = 'pending' AND NEW.state IN ('sent', 'failed', 'unknown', 'retracted'))
        OR (OLD.state = 'unknown' AND NEW.state IN ('sent', 'failed', 'retracted'))
        OR (OLD.state = 'sent' AND NEW.state = 'retracted')
        -- A provider-proven non-delivery may be presented again; its tokens are revoked.
        OR (OLD.state = 'failed' AND NEW.state IN ('pending', 'retracted'))
    ) THEN
        RAISE EXCEPTION 'owner-delivery presentation cannot move from % to %',
            OLD.state, NEW.state USING ERRCODE = '42501';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.owner_delivery_token_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'owner-delivery token verifiers are durable' USING ERRCODE = '42501';
    END IF;
    IF ROW(NEW.verifier, NEW.subject_id, NEW.owning_schema, NEW.presentation_id, NEW.action,
           NEW.action_digest, NEW.owner_entity, NEW.surface, NEW.generation, NEW.expires_at,
           NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.verifier, OLD.subject_id, OLD.owning_schema, OLD.presentation_id, OLD.action,
           OLD.action_digest, OLD.owner_entity, OLD.surface, OLD.generation, OLD.expires_at,
           OLD.created_at) THEN
        RAISE EXCEPTION 'owner-delivery token bindings are immutable' USING ERRCODE = '42501';
    END IF;
    IF OLD.consumed_at IS NOT NULL AND
       ROW(NEW.consumed_at, NEW.outcome) IS DISTINCT FROM ROW(OLD.consumed_at, OLD.outcome) THEN
        RAISE EXCEPTION 'an owner-delivery token is consumed once' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.owner_delivery_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;

DROP TRIGGER IF EXISTS owner_delivery_subject_guard ON public.owner_delivery_subjects;
CREATE TRIGGER owner_delivery_subject_guard BEFORE UPDATE OR DELETE
    ON public.owner_delivery_subjects
    FOR EACH ROW EXECUTE FUNCTION public.owner_delivery_subject_guard();

DROP TRIGGER IF EXISTS owner_delivery_presentation_fence ON public.owner_delivery_presentations;
CREATE TRIGGER owner_delivery_presentation_fence BEFORE INSERT
    ON public.owner_delivery_presentations
    FOR EACH ROW EXECUTE FUNCTION public.owner_delivery_open_fence();
DROP TRIGGER IF EXISTS owner_delivery_presentation_guard ON public.owner_delivery_presentations;
CREATE TRIGGER owner_delivery_presentation_guard BEFORE UPDATE OR DELETE
    ON public.owner_delivery_presentations
    FOR EACH ROW EXECUTE FUNCTION public.owner_delivery_presentation_guard();

DROP TRIGGER IF EXISTS owner_delivery_token_fence ON public.owner_delivery_tokens;
CREATE TRIGGER owner_delivery_token_fence BEFORE INSERT ON public.owner_delivery_tokens
    FOR EACH ROW EXECUTE FUNCTION public.owner_delivery_open_fence();
DROP TRIGGER IF EXISTS owner_delivery_token_guard ON public.owner_delivery_tokens;
CREATE TRIGGER owner_delivery_token_guard BEFORE UPDATE OR DELETE
    ON public.owner_delivery_tokens
    FOR EACH ROW EXECUTE FUNCTION public.owner_delivery_token_guard();

DROP TRIGGER IF EXISTS owner_delivery_receipts_append_only ON public.owner_delivery_receipts;
CREATE TRIGGER owner_delivery_receipts_append_only BEFORE UPDATE OR DELETE
    ON public.owner_delivery_receipts
    FOR EACH ROW EXECUTE FUNCTION public.owner_delivery_append_only();
DROP TRIGGER IF EXISTS owner_delivery_source_map_append_only ON public.owner_delivery_source_map;
CREATE TRIGGER owner_delivery_source_map_append_only BEFORE UPDATE OR DELETE
    ON public.owner_delivery_source_map
    FOR EACH ROW EXECUTE FUNCTION public.owner_delivery_append_only();
"""


def _grants_and_policies() -> None:
    # init-db.sql's default privileges hand every runtime role DML on new public tables;
    # the row policy (own schema only) is the fence, and connector_writer gets nothing.
    tables = ", ".join(f"public.{t}" for t in _TABLES)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {tables} FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        DECLARE role_name TEXT;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'connector_writer') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE {tables} FROM connector_writer';
            END IF;
            FOR role_name IN SELECT rolname FROM pg_roles
                WHERE rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\'
            LOOP
                EXECUTE format('GRANT SELECT ON TABLE public.owner_delivery_rollout TO %I',
                               role_name);
                EXECUTE format(
                    'GRANT SELECT, INSERT, UPDATE ON TABLE public.owner_delivery_subjects, '
                    'public.owner_delivery_presentations, public.owner_delivery_tokens '
                    'TO %I', role_name);
                EXECUTE format(
                    'GRANT SELECT, INSERT ON TABLE public.owner_delivery_receipts, '
                    'public.owner_delivery_source_map TO %I', role_name);
            END LOOP;
        EXCEPTION WHEN insufficient_privilege THEN NULL;
        END $$
        """
    )
    for table in _TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY")
    for table in _OWNED:
        op.execute(f"DROP POLICY IF EXISTS {table}_own_schema ON public.{table}")
        op.execute(
            f"""
            CREATE POLICY {table}_own_schema ON public.{table}
                USING (current_user = 'butler_' || owning_schema || '_rw')
                WITH CHECK (current_user = 'butler_' || owning_schema || '_rw')
            """
        )
    op.execute("DROP POLICY IF EXISTS owner_delivery_rollout_read ON public.owner_delivery_rollout")
    op.execute(
        "CREATE POLICY owner_delivery_rollout_read ON public.owner_delivery_rollout "
        "FOR SELECT USING (true)"
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
                    THEN substring(version_num FROM 6)::integer >= 317
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
    _grants_and_policies()


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.owner_delivery_subjects') IS NOT NULL
               AND EXISTS (SELECT 1 FROM public.owner_delivery_subjects) THEN
                RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
            END IF;
        END
        $$
        """
    )
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
    for function in (
        "owner_delivery_subject_guard",
        "owner_delivery_open_fence",
        "owner_delivery_presentation_guard",
        "owner_delivery_token_guard",
        "owner_delivery_append_only",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS public.{function}()")
