"""Add durable, message-scoped cancellation control for dashboard turns.

Revision ID: core_193
Revises: core_192
Create Date: 2026-07-28 00:00:00.000000

The dashboard chat Stop action crosses the API, Switchboard classification, a
target butler's durable route inbox, and the target Spawner.  A per-process
handle or a sticky conversation target cannot make that cross-process handoff
safe.  This migration introduces a small, private control protocol keyed by
the immutable ``public.dashboard_messages.id`` of one user turn.

The protocol's linearisation points are intentionally database transitions:

* The API opens an addressable turn before SSE; the generator claims ingress
  once at the outbound Switchboard boundary, so reconnects cannot duplicate
  work and an early Stop can win before dispatch.
* A target claims a route and inserts its inbox row in one caller-owned
  transaction.
* Each spawned session is registered before work, then atomically claims the
  right to invoke a runtime immediately before invocation.
* Stop records immutable intent.  A pre-invoke claim after that intent fails;
  an already-invoking session remains visible for an explicit MCP cancellation.

The tables are private.  Runtimes receive EXECUTE only on the narrow SECURITY
DEFINER functions needed for their transitions, never broad table DML.
"""

from __future__ import annotations

from alembic import op

revision = "core_193"
down_revision = "core_192"
branch_labels = None
depends_on = None


_TARGET_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_travel_rw",
)
_SWITCHBOARD_ROLE = "butler_switchboard_rw"
_RUNTIME_ROLES = (*_TARGET_RUNTIME_ROLES, _SWITCHBOARD_ROLE)
_CONNECTOR_WRITER_ROLE = "connector_writer"
_TABLE_REVOKE_ROLES = (*_RUNTIME_ROLES, _CONNECTOR_WRITER_ROLE)


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str, *, role: str | None = None) -> None:
    """Execute ACL DDL where the target role exists and is manageable."""
    condition = "TRUE"
    if role is not None:
        condition = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                EXECUTE {_quote_literal(statement)};
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


_TURN_RESULT = """
RETURNS TABLE(
    outcome text,
    message_id uuid,
    conversation_id uuid,
    request_id uuid,
    target_butler text,
    target_kind text,
    route_inbox_id uuid,
    cancel_requested_at timestamptz,
    cancel_confirmed_at timestamptz,
    terminal_state text,
    terminal_at timestamptz
)
"""

_RETURN_MISSING = """
RETURN QUERY SELECT
    'missing'::text,
    NULL::uuid,
    NULL::uuid,
    NULL::uuid,
    NULL::text,
    NULL::text,
    NULL::uuid,
    NULL::timestamptz,
    NULL::timestamptz,
    NULL::text,
    NULL::timestamptz;
RETURN;
"""

_RETURN_TURN = """
RETURN QUERY SELECT
    p_outcome,
    v_turn.message_id,
    v_turn.conversation_id,
    v_turn.request_id,
    v_turn.target_butler,
    v_turn.target_kind,
    v_turn.route_inbox_id,
    v_turn.cancel_requested_at,
    v_turn.cancel_confirmed_at,
    v_turn.terminal_state,
    v_turn.terminal_at;
RETURN;
"""


def _create_schema() -> None:
    # ``route_inbox`` is schema-local (one table per target butler), unlike the
    # public dashboard control tables below.  A recovered route must first win
    # this lease before it can re-run a runtime; otherwise a healthy long turn
    # and a startup scanner can invoke the same dashboard message concurrently.
    op.execute(
        """
        ALTER TABLE route_inbox
            ADD COLUMN IF NOT EXISTS processing_claim_id UUID,
            ADD COLUMN IF NOT EXISTS processing_claimed_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_route_inbox_processing_claim
            ON route_inbox (lifecycle_state, processing_claimed_at)
            WHERE lifecycle_state = 'processing'
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.dashboard_conversation_turns (
            message_id UUID PRIMARY KEY
                REFERENCES public.dashboard_messages(id) ON DELETE CASCADE,
            conversation_id UUID NOT NULL
                REFERENCES public.dashboard_conversations(id) ON DELETE CASCADE,
            ingress_state TEXT NOT NULL DEFAULT 'pending'
                CHECK (
                    ingress_state IN (
                        'pending', 'submitting', 'accepted',
                        'retryable_error', 'rejected'
                    )
                ),
            ingress_attempts INTEGER NOT NULL DEFAULT 0
                CHECK (ingress_attempts >= 0),
            ingress_claimed_at TIMESTAMPTZ,
            ingress_error TEXT,
            request_id UUID,
            target_butler TEXT,
            target_kind TEXT
                CHECK (
                    target_kind IS NULL
                    OR target_kind IN ('route', 'bug_report', 'dead_letter')
                ),
            target_claimed_at TIMESTAMPTZ,
            route_inbox_id UUID,
            route_enqueued_at TIMESTAMPTZ,
            external_action_claimed_at TIMESTAMPTZ,
            cancel_requested_at TIMESTAMPTZ,
            cancel_confirmed_at TIMESTAMPTZ,
            pending_terminal_state TEXT
                CHECK (
                    pending_terminal_state IS NULL
                    OR pending_terminal_state IN ('completed', 'failed')
                ),
            pending_terminal_at TIMESTAMPTZ,
            terminal_state TEXT
                CHECK (
                    terminal_state IS NULL
                    OR terminal_state IN ('completed', 'failed', 'cancelled', 'ambiguous')
                ),
            terminal_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (target_kind IS NULL OR target_butler IS NOT NULL),
            CHECK (route_inbox_id IS NULL OR target_kind = 'route')
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_dashboard_conversation_turns_open_by_conversation
            ON public.dashboard_conversation_turns (conversation_id, created_at DESC)
            WHERE terminal_at IS NULL
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.dashboard_conversation_turn_sessions (
            message_id UUID NOT NULL
                REFERENCES public.dashboard_conversation_turns(message_id)
                ON DELETE CASCADE,
            session_id UUID NOT NULL UNIQUE,
            request_id UUID NOT NULL,
            butler_name TEXT NOT NULL,
            phase TEXT NOT NULL CHECK (phase IN ('classification', 'route')),
            registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            invoke_claimed_at TIMESTAMPTZ,
            invoke_active BOOLEAN NOT NULL DEFAULT FALSE,
            cancel_acknowledged_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            success BOOLEAN,
            PRIMARY KEY (message_id, session_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_dashboard_turn_sessions_live
            ON public.dashboard_conversation_turn_sessions (message_id, registered_at)
            WHERE completed_at IS NULL
        """
    )


def _create_functions() -> None:
    # SECURITY DEFINER hides ``current_user`` behind the function owner. The
    # runtime's active ``SET ROLE`` remains available through the ``role`` GUC,
    # so every cross-schema mutation below binds its caller to the target or
    # session it is changing instead of trusting a caller-supplied butler name.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.dashboard_turn_require_role(
            p_expected_role text
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        BEGIN
            IF current_setting('role', true) IS DISTINCT FROM p_expected_role THEN
                RAISE EXCEPTION 'dashboard turn control is not authorized for this runtime role'
                    USING ERRCODE = '42501';
            END IF;
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_open(
            p_message_id uuid,
            p_conversation_id uuid
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            p_outcome text;
        BEGIN
            INSERT INTO public.dashboard_conversation_turns (
                message_id,
                conversation_id
            )
            SELECT p_message_id, p_conversation_id
            WHERE EXISTS (
                SELECT 1
                FROM public.dashboard_messages
                WHERE id = p_message_id
                  AND conversation_id = p_conversation_id
                  AND role = 'user'
            )
            ON CONFLICT (message_id) DO NOTHING
            RETURNING * INTO v_turn;

            IF FOUND THEN
                -- Opening the durable row is deliberately not the external
                -- ingress boundary. The SSE generator claims that boundary
                -- immediately before calling Switchboard, so a Stop received
                -- before generator execution can terminally block dispatch.
                p_outcome := 'ready';
                {_RETURN_TURN}
            END IF;

            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF v_turn.conversation_id <> p_conversation_id THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            IF v_turn.terminal_at IS NOT NULL THEN
                p_outcome := CASE
                    WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                    WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                    ELSE 'finished'
                END;
                {_RETURN_TURN}
            END IF;
            IF v_turn.cancel_requested_at IS NOT NULL THEN
                -- A cancellation request may still be settling across an
                -- already-crossed ingress or runtime boundary. Only the
                -- terminal state above may produce SESSION_CANCELLED.
                p_outcome := 'cancelling';
                {_RETURN_TURN}
            END IF;
            IF v_turn.ingress_state = 'accepted' THEN
                p_outcome := 'accepted';
                {_RETURN_TURN}
            END IF;
            IF v_turn.ingress_state = 'submitting' THEN
                p_outcome := 'pending';
                {_RETURN_TURN}
            END IF;
            p_outcome := 'ready';
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_claim_ingress(
            p_message_id uuid
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            p_outcome text;
        BEGIN
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF v_turn.terminal_at IS NOT NULL THEN
                p_outcome := CASE
                    WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                    WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                    ELSE 'finished'
                END;
                {_RETURN_TURN}
            END IF;
            IF v_turn.cancel_requested_at IS NOT NULL THEN
                p_outcome := 'cancelling';
                {_RETURN_TURN}
            END IF;
            IF v_turn.ingress_state = 'accepted' THEN
                p_outcome := 'accepted';
                {_RETURN_TURN}
            END IF;
            IF v_turn.ingress_state = 'submitting'
               AND v_turn.ingress_claimed_at > now() - interval '60 seconds' THEN
                p_outcome := 'pending';
                {_RETURN_TURN}
            END IF;
            UPDATE public.dashboard_conversation_turns
            SET ingress_state = 'submitting',
                ingress_attempts = ingress_attempts + 1,
                ingress_claimed_at = now(),
                ingress_error = NULL,
                updated_at = now()
            WHERE message_id = p_message_id
            RETURNING * INTO v_turn;
            p_outcome := 'dispatch';
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_bind_ingress(
            p_message_id uuid,
            p_request_id uuid
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            v_has_prior_invocation boolean;
            p_outcome text;
        BEGIN
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF v_turn.terminal_at IS NOT NULL THEN
                p_outcome := CASE
                    WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                    WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                    ELSE 'finished'
                END;
                {_RETURN_TURN}
            END IF;
            IF v_turn.request_id IS NOT NULL AND v_turn.request_id <> p_request_id THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            SELECT EXISTS (
                SELECT 1
                FROM public.dashboard_conversation_turn_sessions
                WHERE message_id = p_message_id
                  AND invoke_claimed_at IS NOT NULL
            ) INTO v_has_prior_invocation;
            UPDATE public.dashboard_conversation_turns
            SET request_id = p_request_id,
                ingress_state = 'accepted',
                ingress_error = NULL,
                cancel_confirmed_at = CASE
                    WHEN cancel_requested_at IS NOT NULL AND NOT v_has_prior_invocation
                        THEN coalesce(cancel_confirmed_at, now())
                    ELSE cancel_confirmed_at
                END,
                terminal_state = CASE
                    WHEN cancel_requested_at IS NOT NULL AND NOT v_has_prior_invocation
                        THEN 'cancelled'
                    ELSE terminal_state
                END,
                terminal_at = CASE
                    WHEN cancel_requested_at IS NOT NULL AND NOT v_has_prior_invocation
                        THEN now()
                    ELSE terminal_at
                END,
                updated_at = now()
            WHERE message_id = p_message_id
            RETURNING * INTO v_turn;
            p_outcome := CASE
                WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                WHEN v_turn.terminal_at IS NOT NULL THEN 'finished'
                WHEN v_turn.cancel_requested_at IS NOT NULL THEN 'cancelling'
                ELSE 'accepted'
            END;
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_record_ingress_failure(
            p_message_id uuid,
            p_state text,
            p_detail text
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            v_has_prior_invocation boolean;
            p_outcome text;
        BEGIN
            IF p_state NOT IN ('retryable_error', 'rejected') THEN
                RAISE EXCEPTION 'invalid dashboard turn ingress state: %', p_state
                    USING ERRCODE = '22023';
            END IF;
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF v_turn.terminal_at IS NOT NULL THEN
                p_outcome := CASE
                    WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                    WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                    ELSE 'finished'
                END;
                {_RETURN_TURN}
            END IF;
            SELECT EXISTS (
                SELECT 1
                FROM public.dashboard_conversation_turn_sessions
                WHERE message_id = p_message_id
                  AND invoke_claimed_at IS NOT NULL
            ) INTO v_has_prior_invocation;
            IF v_turn.cancel_requested_at IS NOT NULL AND NOT v_has_prior_invocation THEN
                UPDATE public.dashboard_conversation_turns
                SET cancel_confirmed_at = coalesce(cancel_confirmed_at, now()),
                    terminal_state = 'cancelled',
                    terminal_at = now(),
                    updated_at = now()
                WHERE message_id = p_message_id
                RETURNING * INTO v_turn;
                p_outcome := 'cancelled';
                {_RETURN_TURN}
            END IF;
            UPDATE public.dashboard_conversation_turns
            SET ingress_state = p_state,
                ingress_error = left(coalesce(p_detail, ''), 4096),
                terminal_state = CASE
                    WHEN p_state = 'rejected' THEN 'failed'
                    ELSE terminal_state
                END,
                terminal_at = CASE
                    WHEN p_state = 'rejected' THEN now()
                    ELSE terminal_at
                END,
                updated_at = now()
            WHERE message_id = p_message_id
            RETURNING * INTO v_turn;
            p_outcome := CASE
                WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                WHEN v_turn.terminal_at IS NOT NULL THEN 'finished'
                WHEN v_turn.cancel_requested_at IS NOT NULL THEN 'cancelling'
                ELSE 'active'
            END;
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_claim_target(
            p_message_id uuid,
            p_request_id uuid,
            p_target_butler text
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            p_outcome text;
        BEGIN
            PERFORM public.dashboard_turn_require_role('butler_' || p_target_butler || '_rw');
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF v_turn.cancel_requested_at IS NOT NULL THEN
                p_outcome := 'cancelled';
                {_RETURN_TURN}
            END IF;
            IF v_turn.terminal_at IS NOT NULL THEN
                p_outcome := 'finished';
                {_RETURN_TURN}
            END IF;
            IF p_target_butler IS NULL OR btrim(p_target_butler) = '' THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            IF v_turn.request_id IS NOT NULL AND v_turn.request_id <> p_request_id THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            IF v_turn.target_kind = 'route' THEN
                IF v_turn.target_butler IS DISTINCT FROM p_target_butler THEN
                    p_outcome := 'conflict';
                    {_RETURN_TURN}
                END IF;
                IF v_turn.route_inbox_id IS NOT NULL THEN
                    p_outcome := 'enqueued';
                    {_RETURN_TURN}
                END IF;
                p_outcome := 'active';
                {_RETURN_TURN}
            END IF;
            IF v_turn.target_kind IS NOT NULL THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            UPDATE public.dashboard_conversation_turns
            SET request_id = coalesce(request_id, p_request_id),
                ingress_state = 'accepted',
                target_butler = p_target_butler,
                target_kind = 'route',
                target_claimed_at = now(),
                updated_at = now()
            WHERE message_id = p_message_id
            RETURNING * INTO v_turn;
            p_outcome := 'active';
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_mark_route_enqueued(
            p_message_id uuid,
            p_route_inbox_id uuid
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            p_outcome text;
        BEGIN
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF v_turn.cancel_requested_at IS NOT NULL THEN
                p_outcome := 'cancelled';
                {_RETURN_TURN}
            END IF;
            IF v_turn.terminal_at IS NOT NULL THEN
                p_outcome := 'finished';
                {_RETURN_TURN}
            END IF;
            IF v_turn.target_kind <> 'route' OR v_turn.target_butler IS NULL THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            PERFORM public.dashboard_turn_require_role(
                'butler_' || v_turn.target_butler || '_rw'
            );
            IF v_turn.route_inbox_id IS NOT NULL THEN
                IF v_turn.route_inbox_id = p_route_inbox_id THEN
                    p_outcome := 'enqueued';
                ELSE
                    p_outcome := 'conflict';
                END IF;
                {_RETURN_TURN}
            END IF;
            UPDATE public.dashboard_conversation_turns
            SET route_inbox_id = p_route_inbox_id,
                route_enqueued_at = now(),
                updated_at = now()
            WHERE message_id = p_message_id
            RETURNING * INTO v_turn;
            p_outcome := 'active';
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_register_session(
            p_message_id uuid,
            p_session_id uuid,
            p_request_id uuid,
            p_butler_name text,
            p_phase text
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            v_existing_message_id uuid;
            p_outcome text;
        BEGIN
            IF p_phase = 'classification' THEN
                PERFORM public.dashboard_turn_require_role('butler_switchboard_rw');
            ELSE
                PERFORM public.dashboard_turn_require_role(
                    'butler_' || p_butler_name || '_rw'
                );
            END IF;
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF p_phase NOT IN ('classification', 'route') THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            IF v_turn.request_id IS NOT NULL AND v_turn.request_id <> p_request_id THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            IF p_phase = 'classification' AND p_butler_name <> 'switchboard' THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            IF p_phase = 'route' AND (
                v_turn.target_kind <> 'route'
                OR v_turn.target_butler IS DISTINCT FROM p_butler_name
                OR v_turn.route_inbox_id IS NULL
            ) THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            SELECT message_id INTO v_existing_message_id
            FROM public.dashboard_conversation_turn_sessions
            WHERE session_id = p_session_id;
            IF FOUND AND v_existing_message_id <> p_message_id THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            IF v_turn.terminal_state = 'ambiguous' THEN
                -- Recovery has already established that a predecessor may
                -- still have acted. Do not let a delayed handler register a
                -- replacement session under that immutable message id.
                p_outcome := 'ambiguous';
                {_RETURN_TURN}
            END IF;
            IF v_turn.cancel_requested_at IS NULL AND v_turn.terminal_at IS NOT NULL THEN
                p_outcome := 'finished';
                {_RETURN_TURN}
            END IF;
            UPDATE public.dashboard_conversation_turns
            SET request_id = coalesce(request_id, p_request_id),
                ingress_state = 'accepted',
                updated_at = now()
            WHERE message_id = p_message_id
            RETURNING * INTO v_turn;
            INSERT INTO public.dashboard_conversation_turn_sessions (
                message_id,
                session_id,
                request_id,
                butler_name,
                phase
            ) VALUES (
                p_message_id,
                p_session_id,
                p_request_id,
                p_butler_name,
                p_phase
            )
            ON CONFLICT (message_id, session_id) DO NOTHING;
            p_outcome := CASE
                WHEN v_turn.cancel_requested_at IS NOT NULL THEN 'cancelled'
                ELSE 'active'
            END;
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_reconcile_route_recovery(
            p_message_id uuid,
            p_request_id uuid,
            p_route_inbox_id uuid
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            p_outcome text;
        BEGIN
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF v_turn.target_kind <> 'route'
               OR v_turn.target_butler IS NULL
               OR v_turn.request_id IS DISTINCT FROM p_request_id
               OR v_turn.route_inbox_id IS DISTINCT FROM p_route_inbox_id THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            PERFORM public.dashboard_turn_require_role(
                'butler_' || v_turn.target_butler || '_rw'
            );
            IF v_turn.terminal_at IS NOT NULL THEN
                p_outcome := CASE
                    WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                    WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                    ELSE 'finished'
                END;
                {_RETURN_TURN}
            END IF;

            -- A stale route-inbox lease is evidence that the predecessor
            -- cannot be contacted through the queue, not proof that its
            -- runtime died.  A database/network partition can leave that
            -- runtime live after a recovery worker takes the lease. Preserve
            -- every predecessor session for Stop enumeration and surface the
            -- turn as ambiguous instead of starting a duplicate runtime.
            UPDATE public.dashboard_conversation_turns
            SET terminal_state = 'ambiguous',
                terminal_at = now(),
                updated_at = now()
            WHERE message_id = p_message_id
            RETURNING * INTO v_turn;

            p_outcome := 'ambiguous';
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_claim_invoke(
            p_message_id uuid,
            p_session_id uuid
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            v_butler_name text;
            p_outcome text;
        BEGIN
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            SELECT butler_name INTO v_butler_name
            FROM public.dashboard_conversation_turn_sessions
            WHERE message_id = p_message_id
              AND session_id = p_session_id
              AND completed_at IS NULL;
            IF NOT FOUND THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            PERFORM public.dashboard_turn_require_role('butler_' || v_butler_name || '_rw');
            IF v_turn.cancel_requested_at IS NOT NULL THEN
                p_outcome := 'cancelled';
                {_RETURN_TURN}
            END IF;
            IF v_turn.terminal_at IS NOT NULL THEN
                p_outcome := CASE
                    WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                    ELSE 'finished'
                END;
                {_RETURN_TURN}
            END IF;
            UPDATE public.dashboard_conversation_turn_sessions
            SET invoke_claimed_at = coalesce(invoke_claimed_at, now()),
                invoke_active = TRUE
            WHERE message_id = p_message_id
              AND session_id = p_session_id;
            p_outcome := 'active';
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_release_invoke(
            p_message_id uuid,
            p_session_id uuid
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            v_butler_name text;
            p_outcome text;
        BEGIN
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            SELECT butler_name INTO v_butler_name
            FROM public.dashboard_conversation_turn_sessions
            WHERE message_id = p_message_id
              AND session_id = p_session_id
              AND completed_at IS NULL;
            IF NOT FOUND THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            PERFORM public.dashboard_turn_require_role('butler_' || v_butler_name || '_rw');
            UPDATE public.dashboard_conversation_turn_sessions
            SET invoke_active = FALSE
            WHERE message_id = p_message_id
              AND session_id = p_session_id;
            p_outcome := CASE
                WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                WHEN v_turn.terminal_at IS NOT NULL THEN 'finished'
                WHEN v_turn.cancel_requested_at IS NOT NULL THEN 'cancelling'
                ELSE 'active'
            END;
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_complete_session(
            p_message_id uuid,
            p_session_id uuid,
            p_success boolean
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            v_phase text;
            v_butler_name text;
            v_invoke_claimed_at timestamptz;
            v_cancel_acknowledged_at timestamptz;
            v_has_other_open_session boolean;
            v_pending_terminal_state text;
            p_outcome text;
        BEGIN
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            SELECT phase, butler_name, invoke_claimed_at, cancel_acknowledged_at
            INTO v_phase, v_butler_name, v_invoke_claimed_at, v_cancel_acknowledged_at
            FROM public.dashboard_conversation_turn_sessions
            WHERE message_id = p_message_id
              AND session_id = p_session_id;
            IF NOT FOUND THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            PERFORM public.dashboard_turn_require_role('butler_' || v_butler_name || '_rw');
            UPDATE public.dashboard_conversation_turn_sessions
            SET completed_at = coalesce(completed_at, now()),
                invoke_active = FALSE,
                success = coalesce(success, p_success)
            WHERE message_id = p_message_id
              AND session_id = p_session_id;
            SELECT EXISTS (
                SELECT 1
                FROM public.dashboard_conversation_turn_sessions
                WHERE message_id = p_message_id
                  AND completed_at IS NULL
            ) INTO v_has_other_open_session;
            SELECT pending_terminal_state INTO v_pending_terminal_state
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id;

            -- Cancellation intent is not cancellation confirmation.  A runtime
            -- that was already invoking can still finish if its MCP cancel call
            -- was unreachable, so retain its actual success/failure here.
            IF v_turn.terminal_at IS NULL
                AND v_turn.cancel_requested_at IS NOT NULL
                AND NOT v_has_other_open_session
                AND (
                    v_invoke_claimed_at IS NULL
                    OR v_cancel_acknowledged_at IS NOT NULL
                ) THEN
                -- Either the Stop arrived before this session crossed invoke,
                -- or this exact invoking session durably acknowledged owner
                -- cancellation before its final result was written.
                UPDATE public.dashboard_conversation_turns
                SET cancel_confirmed_at = coalesce(cancel_confirmed_at, now()),
                    terminal_state = 'cancelled',
                    terminal_at = now(),
                    updated_at = now()
                WHERE message_id = p_message_id
                RETURNING * INTO v_turn;
            ELSIF v_turn.terminal_at IS NULL
                AND v_phase = 'route'
                AND v_has_other_open_session THEN
                -- The target completed before an overlapping classifier or
                -- recovery attempt. Hold its concrete outcome until the final
                -- open session closes; no peer may be hidden by a terminal row.
                UPDATE public.dashboard_conversation_turns
                SET pending_terminal_state = CASE
                        WHEN p_success THEN 'completed'
                        ELSE 'failed'
                    END,
                    pending_terminal_at = now(),
                    updated_at = now()
                WHERE message_id = p_message_id
                RETURNING * INTO v_turn;
            ELSIF v_turn.terminal_at IS NULL
                AND NOT v_has_other_open_session
                AND v_pending_terminal_state IS NOT NULL THEN
                UPDATE public.dashboard_conversation_turns
                SET terminal_state = v_pending_terminal_state,
                    terminal_at = now(),
                    pending_terminal_state = NULL,
                    pending_terminal_at = NULL,
                    updated_at = now()
                WHERE message_id = p_message_id
                RETURNING * INTO v_turn;
            ELSIF v_turn.terminal_at IS NULL
                AND NOT v_has_other_open_session
                AND v_phase = 'route' THEN
                UPDATE public.dashboard_conversation_turns
                SET terminal_state = CASE WHEN p_success THEN 'completed' ELSE 'failed' END,
                    terminal_at = now(),
                    updated_at = now()
                WHERE message_id = p_message_id
                RETURNING * INTO v_turn;
            ELSIF v_turn.terminal_at IS NULL
                AND NOT v_has_other_open_session
                AND v_phase = 'classification'
                AND v_turn.cancel_requested_at IS NOT NULL
                AND v_turn.target_kind IS NULL THEN
                -- Stop landed after a classifier invocation ended but before
                -- it could create any target action. That invocation was not
                -- killed (request_cancel reports ``settling`` in this race),
                -- so record its actual non-delivery as finished/failed rather
                -- than fabricating a confirmed cancellation or leaving an
                -- unresolvable open turn forever.
                UPDATE public.dashboard_conversation_turns
                SET terminal_state = 'failed',
                    terminal_at = now(),
                    updated_at = now()
                WHERE message_id = p_message_id
                RETURNING * INTO v_turn;
            END IF;
            p_outcome := CASE
                WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                WHEN v_turn.terminal_at IS NOT NULL THEN 'finished'
                WHEN v_turn.cancel_requested_at IS NOT NULL THEN 'cancelling'
                ELSE 'active'
            END;
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_claim_external_action(
            p_message_id uuid,
            p_request_id uuid,
            p_kind text
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            v_target_butler text;
            p_outcome text;
        BEGIN
            PERFORM public.dashboard_turn_require_role('butler_switchboard_rw');
            IF p_kind NOT IN ('bug_report', 'dead_letter') THEN
                RAISE EXCEPTION 'invalid dashboard terminal action: %', p_kind
                    USING ERRCODE = '22023';
            END IF;
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF v_turn.cancel_requested_at IS NOT NULL THEN
                p_outcome := 'cancelled';
                {_RETURN_TURN}
            END IF;
            IF v_turn.terminal_at IS NOT NULL THEN
                p_outcome := 'finished';
                {_RETURN_TURN}
            END IF;
            IF v_turn.request_id IS NOT NULL AND v_turn.request_id <> p_request_id THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            IF v_turn.target_kind IS NOT NULL THEN
                IF v_turn.target_kind = p_kind
                   AND v_turn.external_action_claimed_at IS NOT NULL THEN
                    -- The side effect was reserved but not durably completed.
                    -- A crashed worker must never make a later caller report it
                    -- as filed; a dedicated reconciliation/outbox owns recovery.
                    p_outcome := 'external_action_in_progress';
                ELSE
                    p_outcome := 'conflict';
                END IF;
                {_RETURN_TURN}
            END IF;
            v_target_butler := CASE
                WHEN p_kind = 'bug_report' THEN 'qa'
                ELSE 'switchboard'
            END;
            UPDATE public.dashboard_conversation_turns
            SET request_id = coalesce(request_id, p_request_id),
                ingress_state = 'accepted',
                target_butler = v_target_butler,
                target_kind = p_kind,
                target_claimed_at = now(),
                external_action_claimed_at = now(),
                updated_at = now()
            WHERE message_id = p_message_id
            RETURNING * INTO v_turn;
            p_outcome := 'claimed';
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_mark_terminal(
            p_message_id uuid,
            p_state text
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            v_has_open_session boolean;
            p_outcome text;
        BEGIN
            -- Cancellation has a separate acknowledgement protocol.  A target
            -- runtime must not be able to bypass the other live sessions by
            -- directly writing a terminal cancelled state.
            IF p_state NOT IN ('completed', 'failed') THEN
                RAISE EXCEPTION 'invalid dashboard turn terminal state: %', p_state
                    USING ERRCODE = '22023';
            END IF;
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF v_turn.target_kind = 'route' AND v_turn.target_butler IS NOT NULL THEN
                PERFORM public.dashboard_turn_require_role(
                    'butler_' || v_turn.target_butler || '_rw'
                );
            ELSE
                -- Classification and terminal-action lanes are Switchboard-owned.
                PERFORM public.dashboard_turn_require_role('butler_switchboard_rw');
            END IF;
            IF v_turn.terminal_at IS NOT NULL THEN
                p_outcome := CASE
                    WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                    WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                    ELSE 'finished'
                END;
                {_RETURN_TURN}
            END IF;
            SELECT EXISTS (
                SELECT 1
                FROM public.dashboard_conversation_turn_sessions
                WHERE message_id = p_message_id
                  AND completed_at IS NULL
            ) INTO v_has_open_session;
            IF p_state <> 'cancelled' AND v_has_open_session THEN
                UPDATE public.dashboard_conversation_turns
                SET pending_terminal_state = p_state,
                    pending_terminal_at = now(),
                    updated_at = now()
                WHERE message_id = p_message_id
                RETURNING * INTO v_turn;
                p_outcome := 'pending';
                {_RETURN_TURN}
            END IF;
            UPDATE public.dashboard_conversation_turns
            SET terminal_state = CASE
                    WHEN cancel_requested_at IS NOT NULL THEN 'cancelled'
                    ELSE p_state
                END,
                terminal_at = now(),
                updated_at = now()
            WHERE message_id = p_message_id
            RETURNING * INTO v_turn;
            p_outcome := CASE
                WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                ELSE 'finished'
            END;
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_request_cancel(
            p_message_id uuid
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            v_has_active_invocation boolean;
            v_has_prior_invocation boolean;
            v_turn_is_ambiguous boolean;
            p_outcome text;
        BEGIN
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF v_turn.terminal_at IS NOT NULL
               AND v_turn.terminal_state IS DISTINCT FROM 'ambiguous' THEN
                p_outcome := CASE
                    WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                    ELSE 'finished'
                END;
                {_RETURN_TURN}
            END IF;
            v_turn_is_ambiguous := v_turn.terminal_state = 'ambiguous';
            IF v_turn.target_kind IN ('bug_report', 'dead_letter')
               AND v_turn.external_action_claimed_at IS NOT NULL THEN
                -- Do not write cancellation intent over an externally-visible
                -- action reservation. Its outcome is not yet known, so Stop
                -- must remain explicitly unconfirmed.
                p_outcome := 'external_action_in_progress';
                {_RETURN_TURN}
            END IF;
            UPDATE public.dashboard_conversation_turns
            SET cancel_requested_at = coalesce(cancel_requested_at, now()),
                updated_at = now()
            WHERE message_id = p_message_id
            RETURNING * INTO v_turn;
            SELECT EXISTS (
                SELECT 1
                FROM public.dashboard_conversation_turn_sessions
                WHERE message_id = p_message_id
                  AND completed_at IS NULL
                  AND invoke_active IS TRUE
            ) INTO v_has_active_invocation;
            SELECT EXISTS (
                SELECT 1
                FROM public.dashboard_conversation_turn_sessions
                WHERE message_id = p_message_id
                  AND invoke_claimed_at IS NOT NULL
            ) INTO v_has_prior_invocation;
            IF v_turn_is_ambiguous THEN
                -- Keep the no-replay terminal fact, but still address every
                -- exact active predecessor through the normal Stop API.
                p_outcome := CASE
                    WHEN v_has_active_invocation THEN 'cancelling'
                    ELSE 'ambiguous'
                END;
                {_RETURN_TURN}
            END IF;
            IF NOT v_has_active_invocation
                AND NOT v_has_prior_invocation
                AND v_turn.ingress_state = 'submitting' THEN
                -- The generator has claimed its one outbound Switchboard
                -- submission. A Stop now prevents every later runtime
                -- boundary, but cannot truthfully claim that this already
                -- crossing external call was stopped.
                p_outcome := 'settling';
            ELSIF NOT v_has_active_invocation AND NOT v_has_prior_invocation THEN
                UPDATE public.dashboard_conversation_turns
                SET cancel_confirmed_at = coalesce(cancel_confirmed_at, now()),
                    terminal_state = 'cancelled',
                    terminal_at = now(),
                    updated_at = now()
                WHERE message_id = p_message_id
                RETURNING * INTO v_turn;
                p_outcome := 'cancelled';
            ELSIF NOT v_has_active_invocation THEN
                -- An invocation has already ended but the logical session has
                -- not yet recorded its actual result.  Stop now gates all
                -- future attempts, but must not be mislabeled as a kill.
                p_outcome := 'settling';
            ELSE
                p_outcome := 'cancelling';
            END IF;
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_acknowledge_cancel(
            p_message_id uuid,
            p_session_id uuid
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            v_butler_name text;
            p_outcome text;
        BEGIN
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF v_turn.cancel_requested_at IS NULL THEN
                p_outcome := CASE
                    WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                    WHEN v_turn.terminal_at IS NOT NULL THEN 'finished'
                    ELSE 'conflict'
                END;
                {_RETURN_TURN}
            END IF;
            SELECT butler_name INTO v_butler_name
            FROM public.dashboard_conversation_turn_sessions
            WHERE message_id = p_message_id
              AND session_id = p_session_id
              AND invoke_claimed_at IS NOT NULL
              AND completed_at IS NULL;
            IF NOT FOUND THEN
                p_outcome := 'conflict';
                {_RETURN_TURN}
            END IF;
            PERFORM public.dashboard_turn_require_role('butler_' || v_butler_name || '_rw');
            IF v_turn.terminal_at IS NOT NULL
               AND v_turn.terminal_state IS DISTINCT FROM 'ambiguous' THEN
                p_outcome := CASE
                    WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                    ELSE 'finished'
                END;
                {_RETURN_TURN}
            END IF;
            UPDATE public.dashboard_conversation_turn_sessions
            SET cancel_acknowledged_at = coalesce(cancel_acknowledged_at, now())
            WHERE message_id = p_message_id
              AND session_id = p_session_id;
            p_outcome := CASE
                WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                ELSE 'cancelling'
            END;
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_confirm_cancel(
            p_message_id uuid
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            v_unconfirmed_invocation boolean;
            p_outcome text;
        BEGIN
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id
            FOR UPDATE;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            IF v_turn.cancel_requested_at IS NULL THEN
                p_outcome := CASE
                    WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                    WHEN v_turn.terminal_at IS NOT NULL THEN 'finished'
                    ELSE 'conflict'
                END;
                {_RETURN_TURN}
            END IF;
            IF v_turn.terminal_at IS NOT NULL THEN
                p_outcome := CASE
                    WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                    WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                    ELSE 'finished'
                END;
                {_RETURN_TURN}
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.dashboard_conversation_turn_sessions
                WHERE message_id = p_message_id
                  AND completed_at IS NULL
                  AND invoke_active IS TRUE
            ) THEN
                p_outcome := 'cancelling';
                {_RETURN_TURN}
            END IF;
            -- A runtime can release its live invoke lease before its outer
            -- session records the real outcome. Do not turn that narrow
            -- release window into a false owner-cancel confirmation: every
            -- still-open invocation must persist its own acknowledgement.
            SELECT EXISTS (
                SELECT 1
                FROM public.dashboard_conversation_turn_sessions
                WHERE message_id = p_message_id
                  AND invoke_claimed_at IS NOT NULL
                  AND completed_at IS NULL
                  AND cancel_acknowledged_at IS NULL
            ) INTO v_unconfirmed_invocation;
            IF v_unconfirmed_invocation THEN
                p_outcome := 'settling';
                {_RETURN_TURN}
            END IF;
            UPDATE public.dashboard_conversation_turns
            SET cancel_confirmed_at = coalesce(cancel_confirmed_at, now()),
                terminal_state = 'cancelled',
                terminal_at = coalesce(terminal_at, now()),
                updated_at = now()
            WHERE message_id = p_message_id
            RETURNING * INTO v_turn;
            p_outcome := 'cancelled';
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.dashboard_turn_live_sessions(
            p_message_id uuid
        )
        RETURNS TABLE(
            message_id uuid,
            session_id uuid,
            butler_name text,
            phase text,
            registered_at timestamptz,
            invoke_claimed_at timestamptz,
            invoke_active boolean
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
            SELECT
                s.message_id,
                s.session_id,
                s.butler_name,
                s.phase,
                s.registered_at,
                s.invoke_claimed_at,
                s.invoke_active
            FROM public.dashboard_conversation_turn_sessions AS s
            WHERE s.message_id = p_message_id
              AND s.completed_at IS NULL
            ORDER BY s.registered_at ASC
        $fn$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.dashboard_turn_dispatch_status(
            p_message_id uuid
        )
        {_TURN_RESULT}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $fn$
        #variable_conflict use_column
        DECLARE
            v_turn public.dashboard_conversation_turns%ROWTYPE;
            p_outcome text;
        BEGIN
            SELECT * INTO v_turn
            FROM public.dashboard_conversation_turns
            WHERE message_id = p_message_id;
            IF NOT FOUND THEN
                {_RETURN_MISSING}
            END IF;
            p_outcome := CASE
                WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'
                WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'
                WHEN v_turn.terminal_at IS NOT NULL THEN 'finished'
                WHEN v_turn.cancel_requested_at IS NOT NULL THEN 'cancelling'
                WHEN v_turn.target_kind IN ('bug_report', 'dead_letter')
                     AND v_turn.external_action_claimed_at IS NOT NULL
                    THEN 'external_action_in_progress'
                ELSE 'active'
            END;
            {_RETURN_TURN}
        END;
        $fn$;
        """
    )


def _apply_acl() -> None:
    for table in (
        "public.dashboard_conversation_turn_sessions",
        "public.dashboard_conversation_turns",
    ):
        _execute_best_effort(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
        for role in _TABLE_REVOKE_ROLES:
            statement = f"REVOKE ALL ON TABLE {table} FROM {_quote_ident(role)}"
            _execute_best_effort(statement, role=role)

    signatures = (
        "dashboard_turn_require_role(text)",
        "dashboard_turn_open(uuid, uuid)",
        "dashboard_turn_claim_ingress(uuid)",
        "dashboard_turn_bind_ingress(uuid, uuid)",
        "dashboard_turn_record_ingress_failure(uuid, text, text)",
        "dashboard_turn_claim_target(uuid, uuid, text)",
        "dashboard_turn_mark_route_enqueued(uuid, uuid)",
        "dashboard_turn_register_session(uuid, uuid, uuid, text, text)",
        "dashboard_turn_reconcile_route_recovery(uuid, uuid, uuid)",
        "dashboard_turn_claim_invoke(uuid, uuid)",
        "dashboard_turn_release_invoke(uuid, uuid)",
        "dashboard_turn_complete_session(uuid, uuid, boolean)",
        "dashboard_turn_claim_external_action(uuid, uuid, text)",
        "dashboard_turn_mark_terminal(uuid, text)",
        "dashboard_turn_request_cancel(uuid)",
        "dashboard_turn_acknowledge_cancel(uuid, uuid)",
        "dashboard_turn_confirm_cancel(uuid)",
        "dashboard_turn_live_sessions(uuid)",
        "dashboard_turn_dispatch_status(uuid)",
    )
    for signature in signatures:
        _execute_best_effort(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC")

    switchboard_functions = (
        "dashboard_turn_register_session(uuid, uuid, uuid, text, text)",
        "dashboard_turn_claim_invoke(uuid, uuid)",
        "dashboard_turn_release_invoke(uuid, uuid)",
        "dashboard_turn_complete_session(uuid, uuid, boolean)",
        "dashboard_turn_acknowledge_cancel(uuid, uuid)",
        "dashboard_turn_claim_external_action(uuid, uuid, text)",
        "dashboard_turn_mark_terminal(uuid, text)",
        "dashboard_turn_dispatch_status(uuid)",
    )
    for signature in switchboard_functions:
        statement = (
            f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_quote_ident(_SWITCHBOARD_ROLE)}"
        )
        _execute_best_effort(statement, role=_SWITCHBOARD_ROLE)

    target_functions = (
        "dashboard_turn_claim_target(uuid, uuid, text)",
        "dashboard_turn_mark_route_enqueued(uuid, uuid)",
        "dashboard_turn_mark_terminal(uuid, text)",
        "dashboard_turn_register_session(uuid, uuid, uuid, text, text)",
        "dashboard_turn_reconcile_route_recovery(uuid, uuid, uuid)",
        "dashboard_turn_claim_invoke(uuid, uuid)",
        "dashboard_turn_release_invoke(uuid, uuid)",
        "dashboard_turn_complete_session(uuid, uuid, boolean)",
        "dashboard_turn_acknowledge_cancel(uuid, uuid)",
    )
    for role in _TARGET_RUNTIME_ROLES:
        for signature in target_functions:
            statement = f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_quote_ident(role)}"
            _execute_best_effort(statement, role=role)


def upgrade() -> None:
    _create_schema()
    _create_functions()
    _apply_acl()


def downgrade() -> None:
    for signature in (
        "dashboard_turn_dispatch_status(uuid)",
        "dashboard_turn_live_sessions(uuid)",
        "dashboard_turn_confirm_cancel(uuid)",
        "dashboard_turn_acknowledge_cancel(uuid, uuid)",
        "dashboard_turn_request_cancel(uuid)",
        "dashboard_turn_mark_terminal(uuid, text)",
        "dashboard_turn_claim_external_action(uuid, uuid, text)",
        "dashboard_turn_complete_session(uuid, uuid, boolean)",
        "dashboard_turn_release_invoke(uuid, uuid)",
        "dashboard_turn_claim_invoke(uuid, uuid)",
        "dashboard_turn_register_session(uuid, uuid, uuid, text, text)",
        "dashboard_turn_reconcile_route_recovery(uuid, uuid, uuid)",
        "dashboard_turn_mark_route_enqueued(uuid, uuid)",
        "dashboard_turn_claim_target(uuid, uuid, text)",
        "dashboard_turn_record_ingress_failure(uuid, text, text)",
        "dashboard_turn_bind_ingress(uuid, uuid)",
        "dashboard_turn_claim_ingress(uuid)",
        "dashboard_turn_open(uuid, uuid)",
        "dashboard_turn_require_role(text)",
    ):
        _execute_best_effort(f"DROP FUNCTION IF EXISTS public.{signature}")
    op.execute("DROP INDEX IF EXISTS public.idx_dashboard_turn_sessions_live")
    op.execute("DROP TABLE IF EXISTS public.dashboard_conversation_turn_sessions")
    op.execute("DROP INDEX IF EXISTS public.idx_dashboard_conversation_turns_open_by_conversation")
    op.execute("DROP TABLE IF EXISTS public.dashboard_conversation_turns")
    op.execute("DROP INDEX IF EXISTS idx_route_inbox_processing_claim")
    op.execute("ALTER TABLE IF EXISTS route_inbox DROP COLUMN IF EXISTS processing_claimed_at")
    op.execute("ALTER TABLE IF EXISTS route_inbox DROP COLUMN IF EXISTS processing_claim_id")
