"""domain_events: standing cross-butler pub/sub bus (JARVIS run-07 move #10).

Revision ID: core_186
Revises: core_185
Create Date: 2026-07-26 00:00:00.000000

bu-ep4ks.10 (2026-07-25 JARVIS pursuit dossier, ranked move #10). See
``src/butlers/core/domain_events.py`` for the append-log reader/writer,
``src/butlers/core/domain_event_wake.py`` for the subscriber-local task
reconciliation, and ``src/butlers/core_tools/_domain_events.py`` for the
``publish_event``/``subscribe_to_event``/``unsubscribe_from_event``/
``list_my_subscriptions``/``receive_domain_event`` MCP tools.

Design
------
Cross-butler interaction so far is one-shot pull (``delegate_ask``/answer,
bu-gxmfx) or a frozen 11-signal read (``public.user_context``, the
context-bus). Neither lets a butler say "wake me when another butler's
domain does X". This adds a durable publish/subscribe log, reusing the
delegated-answer wake plumbing (``schedule_create`` + deterministic-name
task reconciliation, bu-27dxl.5.2) rather than a new side channel:

  - ``public.domain_events``: append-only log of everything published.
    ``event_type`` is an open, namespaced vocabulary (``"<butler>.<event>"``,
    e.g. ``"travel.trip_booked"``) enforced only by convention/light format
    check in the writer -- deliberately NOT a fixed enum, unlike
    ``context_bus.ContextSignal`` (the exact limitation this move's WHY
    calls out: "fixed vocabulary, hardcoded writers").
  - ``public.butler_subscriptions``: standing ``(subscriber_butler,
    event_type)`` registrations. A butler subscribes/unsubscribes itself;
    there is no cross-butler subscription management.
  - ``public.domain_event_deliveries``: the atomic per-subscriber fan-out
    claim/outcome ledger. ``UNIQUE (event_id, subscriber_butler)`` is the
    idempotence guarantee -- a fan-out retry for the same event/subscriber
    pair can only ever claim or observe the one row, never double-dispatch
    once it reaches ``delivered``.

Seeds the one concrete pair this move wires end-to-end: Finance subscribes
to ``travel.trip_booked`` (pre-budget on a newly booked trip).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_186"
down_revision = "core_185"
branch_labels = None
depends_on = None

# Mirrors core_162's _ALL_RUNTIME_ROLES — every butler role that may publish,
# subscribe, or receive a fan-out dispatch.
_ALL_RUNTIME_ROLES = (
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
    "butler_switchboard_rw",
    "butler_travel_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE"


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
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


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.domain_events (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_type     TEXT NOT NULL,
            source_butler  TEXT NOT NULL,
            payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
            occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_domain_events_type_occurred_at
        ON public.domain_events (event_type, occurred_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_domain_events_occurred_at
        ON public.domain_events (occurred_at DESC)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS public.butler_subscriptions (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            subscriber_butler  TEXT NOT NULL,
            event_type         TEXT NOT NULL,
            active             BOOLEAN NOT NULL DEFAULT true,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_butler_subscriptions_subscriber_event
                UNIQUE (subscriber_butler, event_type)
        )
    """)
    # Fan-out read path: "who is subscribed to this event_type right now."
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_butler_subscriptions_event_type_active
        ON public.butler_subscriptions (event_type)
        WHERE active
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS public.domain_event_deliveries (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_id           UUID NOT NULL REFERENCES public.domain_events(id),
            subscriber_butler  TEXT NOT NULL,
            status             TEXT NOT NULL DEFAULT 'pending',
            task_id            UUID,
            task_name          TEXT,
            error_message      TEXT,
            delivered_at       TIMESTAMPTZ,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_domain_event_deliveries_event_subscriber
                UNIQUE (event_id, subscriber_butler),
            CONSTRAINT chk_domain_event_deliveries_status
                CHECK (status IN ('pending', 'delivered', 'conflict', 'failed'))
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_domain_event_deliveries_subscriber_status
        ON public.domain_event_deliveries (subscriber_butler, status)
    """)

    for table_fqn in (
        "public.domain_events",
        "public.butler_subscriptions",
        "public.domain_event_deliveries",
    ):
        for role in _ALL_RUNTIME_ROLES:
            _grant_best_effort(table_fqn, _TABLE_PRIVILEGES, role)

    # The one concrete pair this move wires end-to-end (bu-ep4ks.10 slice 1):
    # Finance stands a subscription to Travel's trip-booked event so a newly
    # booked trip wakes Finance to consider a pre-budget action.
    op.execute("""
        INSERT INTO public.butler_subscriptions (subscriber_butler, event_type, active)
        VALUES ('finance', 'travel.trip_booked', true)
        ON CONFLICT (subscriber_butler, event_type) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.domain_event_deliveries CASCADE")
    op.execute("DROP TABLE IF EXISTS public.butler_subscriptions CASCADE")
    op.execute("DROP TABLE IF EXISTS public.domain_events CASCADE")
