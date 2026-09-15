"""Typed cross-butler cost claims with role-separated write authority.

Revision ID: core_239
Revises: core_238
Create Date: 2026-09-15 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "core_239"
down_revision = "core_238"
branch_labels = None
depends_on = None

_ALL_BUTLER_ROLES = (
    "butler_chronicler_rw",
    "butler_concierge_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)


def _grant_if_role_exists(role: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON public.cost_claims TO "{role}"';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON public.cost_claim_resolutions TO "{role}"';
                EXECUTE 'GRANT SELECT, INSERT ON public.cost_claim_events TO "{role}"';
                EXECUTE 'REVOKE DELETE ON public.cost_claims, public.cost_claim_resolutions, '
                        || 'public.cost_claim_events FROM "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege OR undefined_object OR undefined_table THEN NULL;
        END $$
        """
    )


def upgrade() -> None:
    op.execute("SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_239:cost_claims', 0))")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.cost_claims (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            claim_key TEXT NOT NULL CHECK (btrim(claim_key) <> ''),
            asserted_by TEXT NOT NULL CHECK (asserted_by ~ '^[a-z][a-z0-9_]*$'),
            asserted_by_role TEXT NOT NULL DEFAULT current_user,
            kind TEXT NOT NULL CHECK (kind IN (
                'receivable', 'payable', 'shared_expense', 'committed_cost', 'expected_refund'
            )),
            direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
            amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
            currency CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            counterparty_entity_id UUID REFERENCES public.entities(id) ON DELETE SET NULL,
            counterparty_label TEXT,
            expected_on DATE,
            description TEXT NOT NULL CHECK (btrim(description) <> ''),
            evidence_kind TEXT,
            evidence_ref TEXT,
            asserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            superseded_at TIMESTAMPTZ,
            retracted_at TIMESTAMPTZ,
            retraction_reason TEXT,
            CHECK (asserted_by_role = 'butler_' || asserted_by || '_rw'),
            CHECK (counterparty_entity_id IS NOT NULL OR btrim(counterparty_label) <> ''),
            CHECK ((retracted_at IS NULL) = (retraction_reason IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_cost_claims_live_key
        ON public.cost_claims (asserted_by, claim_key)
        WHERE superseded_at IS NULL AND retracted_at IS NULL
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.cost_claim_resolutions (
            claim_id UUID PRIMARY KEY REFERENCES public.cost_claims(id) ON DELETE CASCADE,
            state TEXT NOT NULL DEFAULT 'unreconciled' CHECK (state IN (
                'unreconciled', 'matched', 'ambiguous', 'partially_settled', 'settled',
                'unverifiable', 'disputed', 'written_off'
            )),
            matched_amount NUMERIC(14, 2),
            matched_currency CHAR(3),
            match_refs JSONB NOT NULL DEFAULT '[]'::jsonb
                CHECK (jsonb_typeof(match_refs) = 'array'),
            unmatched_reason TEXT,
            unverifiable_reason TEXT,
            evidence_horizon_at TIMESTAMPTZ,
            decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            decided_by TEXT NOT NULL DEFAULT current_user,
            CHECK ((state = 'unverifiable') = (unverifiable_reason IS NOT NULL)),
            CHECK (matched_amount IS NULL OR matched_amount >= 0),
            CHECK (matched_currency IS NULL OR matched_currency ~ '^[A-Z]{3}$')
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.cost_claim_events (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            claim_id UUID NOT NULL REFERENCES public.cost_claims(id) ON DELETE CASCADE,
            actor_role TEXT NOT NULL,
            action TEXT NOT NULL,
            old_state JSONB,
            new_state JSONB,
            idempotency_key TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (claim_id, actor_role, action, idempotency_key)
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.cost_claim_immutable_assertion() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.claim_key, NEW.asserted_by, NEW.asserted_by_role, NEW.kind,
                NEW.direction, NEW.amount, NEW.currency, NEW.counterparty_entity_id,
                NEW.counterparty_label, NEW.expected_on, NEW.description,
                NEW.evidence_kind, NEW.evidence_ref, NEW.asserted_at)
               IS DISTINCT FROM
               (OLD.claim_key, OLD.asserted_by, OLD.asserted_by_role, OLD.kind,
                OLD.direction, OLD.amount, OLD.currency, OLD.counterparty_entity_id,
                OLD.counterparty_label, OLD.expected_on, OLD.description,
                OLD.evidence_kind, OLD.evidence_ref, OLD.asserted_at)
            THEN
                RAISE EXCEPTION 'cost claim assertions are immutable; supersede the row';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.cost_claim_audit_claim() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF current_user = (
                   SELECT pg_get_userbyid(c.relowner)
                   FROM pg_class AS c
                   WHERE c.oid = TG_RELID
               )
               AND current_setting('butlers.cost_claim_restore', true) = 'on'
            THEN
                RETURN NEW;
            END IF;
            INSERT INTO public.cost_claim_events
                (claim_id, actor_role, action, old_state, new_state, idempotency_key)
            VALUES (
                NEW.id, current_user,
                CASE WHEN TG_OP = 'INSERT' THEN 'asserted'
                     WHEN NEW.retracted_at IS DISTINCT FROM OLD.retracted_at THEN 'retracted'
                     WHEN NEW.superseded_at IS DISTINCT FROM OLD.superseded_at THEN 'superseded'
                     ELSE 'amended' END,
                CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) ELSE NULL END,
                to_jsonb(NEW), gen_random_uuid()::text
            );
            RETURN NEW;
        END $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_cost_claim_immutable ON public.cost_claims")
    op.execute(
        "CREATE TRIGGER trg_cost_claim_immutable BEFORE UPDATE ON public.cost_claims "
        "FOR EACH ROW EXECUTE FUNCTION public.cost_claim_immutable_assertion()"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.cost_claim_audit_resolution() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF current_user = (
                   SELECT pg_get_userbyid(c.relowner)
                   FROM pg_class AS c
                   WHERE c.oid = TG_RELID
               )
               AND current_setting('butlers.cost_claim_restore', true) = 'on'
            THEN
                RETURN NEW;
            END IF;
            INSERT INTO public.cost_claim_events
                (claim_id, actor_role, action, old_state, new_state, idempotency_key)
            VALUES (
                NEW.claim_id, current_user, 'resolved',
                CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) ELSE NULL END,
                to_jsonb(NEW), gen_random_uuid()::text
            );
            RETURN NEW;
        END $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_cost_claim_audit ON public.cost_claims")
    op.execute(
        "CREATE TRIGGER trg_cost_claim_audit AFTER INSERT OR UPDATE ON public.cost_claims "
        "FOR EACH ROW EXECUTE FUNCTION public.cost_claim_audit_claim()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_cost_claim_resolution_audit ON public.cost_claim_resolutions"
    )
    op.execute(
        "CREATE TRIGGER trg_cost_claim_resolution_audit AFTER INSERT OR UPDATE "
        "ON public.cost_claim_resolutions FOR EACH ROW "
        "EXECUTE FUNCTION public.cost_claim_audit_resolution()"
    )
    for table in ("cost_claims", "cost_claim_resolutions", "cost_claim_events"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_read ON public.{table}")
        op.execute(
            f"CREATE POLICY {table}_read ON public.{table} FOR SELECT TO PUBLIC USING (true)"
        )
    op.execute("DROP POLICY IF EXISTS cost_claims_insert_own ON public.cost_claims")
    op.execute(
        "CREATE POLICY cost_claims_insert_own ON public.cost_claims FOR INSERT "
        "WITH CHECK (asserted_by_role = current_user "
        "AND asserted_by_role = 'butler_' || asserted_by || '_rw')"
    )
    op.execute("DROP POLICY IF EXISTS cost_claims_update_own ON public.cost_claims")
    op.execute(
        "CREATE POLICY cost_claims_update_own ON public.cost_claims FOR UPDATE "
        "USING (asserted_by_role = current_user) WITH CHECK (asserted_by_role = current_user)"
    )
    op.execute(
        "DROP POLICY IF EXISTS cost_claim_resolutions_finance ON public.cost_claim_resolutions"
    )
    op.execute(
        "DROP POLICY IF EXISTS cost_claim_resolutions_finance_insert "
        "ON public.cost_claim_resolutions"
    )
    op.execute(
        "DROP POLICY IF EXISTS cost_claim_resolutions_finance_update "
        "ON public.cost_claim_resolutions"
    )
    op.execute(
        "CREATE POLICY cost_claim_resolutions_finance_insert "
        "ON public.cost_claim_resolutions FOR INSERT "
        "WITH CHECK (current_user = 'butler_finance_rw')"
    )
    op.execute(
        "CREATE POLICY cost_claim_resolutions_finance_update "
        "ON public.cost_claim_resolutions FOR UPDATE "
        "USING (current_user = 'butler_finance_rw') "
        "WITH CHECK (current_user = 'butler_finance_rw')"
    )
    op.execute("DROP POLICY IF EXISTS cost_claim_events_trigger_insert ON public.cost_claim_events")
    op.execute(
        "CREATE POLICY cost_claim_events_trigger_insert ON public.cost_claim_events FOR INSERT "
        "WITH CHECK (actor_role = current_user AND pg_trigger_depth() > 0)"
    )
    op.execute(
        """
        DO $$
        DECLARE
            v_owner name;
        BEGIN
            SELECT pg_get_userbyid(c.relowner)
              INTO v_owner
              FROM pg_class AS c
             WHERE c.oid = 'public.cost_claims'::regclass;

            IF v_owner IS NULL THEN
                RAISE EXCEPTION 'cost_claims table owner is unavailable';
            END IF;

            DROP POLICY IF EXISTS cost_claims_restore_owner ON public.cost_claims;
            DROP POLICY IF EXISTS cost_claim_resolutions_restore_owner
                ON public.cost_claim_resolutions;
            DROP POLICY IF EXISTS cost_claim_events_restore_owner ON public.cost_claim_events;

            EXECUTE format(
                'CREATE POLICY cost_claims_restore_owner ON public.cost_claims '
                'FOR INSERT WITH CHECK (current_user = %L)',
                v_owner
            );
            EXECUTE format(
                'CREATE POLICY cost_claim_resolutions_restore_owner '
                'ON public.cost_claim_resolutions FOR INSERT '
                'WITH CHECK (current_user = %L)',
                v_owner
            );
            EXECUTE format(
                'CREATE POLICY cost_claim_events_restore_owner ON public.cost_claim_events '
                'FOR INSERT WITH CHECK (current_user = %L)',
                v_owner
            );
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.cost_claim_restore_row(
            p_relation TEXT,
            p_payload JSONB
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        SET row_security = on
        AS $$
        BEGIN
            PERFORM set_config('butlers.cost_claim_restore', 'on', true);

            CASE p_relation
                WHEN 'cost_claims' THEN
                    INSERT INTO public.cost_claims
                    SELECT (jsonb_populate_record(NULL::public.cost_claims, p_payload)).*;
                WHEN 'cost_claim_resolutions' THEN
                    INSERT INTO public.cost_claim_resolutions
                    SELECT (jsonb_populate_record(
                        NULL::public.cost_claim_resolutions, p_payload
                    )).*;
                WHEN 'cost_claim_events' THEN
                    INSERT INTO public.cost_claim_events OVERRIDING SYSTEM VALUE
                    SELECT (jsonb_populate_record(NULL::public.cost_claim_events, p_payload)).*;
                    PERFORM setval(
                        pg_get_serial_sequence('public.cost_claim_events', 'id'),
                        GREATEST((SELECT max(id) FROM public.cost_claim_events), 1),
                        EXISTS (SELECT 1 FROM public.cost_claim_events)
                    );
                ELSE
                    RAISE EXCEPTION 'unsupported cost-claim restore relation';
            END CASE;
        END
        $$
        """
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION public.cost_claim_restore_row(TEXT, JSONB) FROM PUBLIC"
    )
    for role in _ALL_BUTLER_ROLES:
        _grant_if_role_exists(role)


def downgrade() -> None:
    op.execute("SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_239:cost_claims', 0))")
    op.execute("DROP FUNCTION IF EXISTS public.cost_claim_audit_resolution() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS public.cost_claim_audit_claim() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS public.cost_claim_immutable_assertion() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS public.cost_claim_restore_row(TEXT, JSONB)")
    op.execute("DROP TABLE IF EXISTS public.cost_claim_events")
    op.execute("DROP TABLE IF EXISTS public.cost_claim_resolutions")
    op.execute("DROP TABLE IF EXISTS public.cost_claims")
