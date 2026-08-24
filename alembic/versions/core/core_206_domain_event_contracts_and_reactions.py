"""domain events: publisher-owned contracts + append-only reaction receipts.

Revision ID: core_206
Revises: core_205
Create Date: 2026-08-22 00:00:00.000000

bu-6jv4m.8 (JARVIS pursuit run 08, ranked move #8). Two tables that split
the one question the bus could answer from the one it could not.

``public.domain_event_contracts`` is a *projection*, not a source of truth.
The contract for an event type is declared in the publisher's own git
directory (``roster/<butler>/domain_events.toml``) and loaded by
``butlers.core.domain_event_contracts``; each daemon materializes its own
declarations here at startup so the dashboard and other butlers can read
what a namespace promises without reading another butler's repo directory.
Admission control reads the git declaration, never this table -- a stale or
truncated projection must never widen what may be published.

``public.domain_event_reactions`` is the append-only outcome ledger.
``public.domain_event_deliveries`` records transport ("a wake task was
scheduled"); this records what the subscriber actually did -- ``acted``,
``ignored``, ``deferred``, ``failed``, or ``unreported``. The partial unique
index makes "a wake closes exactly once" a database invariant, so a late
correlation sweep can never overwrite a receipt a session already filed.
Nothing infers ``acted``: the sweep may only write ``unreported``.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_206"
down_revision = "core_205"
branch_labels = None
depends_on = None

# Mirrors core_186's _ALL_RUNTIME_ROLES — every butler role that may publish,
# subscribe, or close out a fan-out dispatch.
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
        CREATE TABLE IF NOT EXISTS public.domain_event_contracts (
            event_type            TEXT PRIMARY KEY,
            publisher             TEXT NOT NULL,
            schema_version        INTEGER NOT NULL,
            summary               TEXT NOT NULL,
            retention_policy      TEXT NOT NULL,
            reaction_expectation  TEXT NOT NULL,
            reaction_contract     TEXT NOT NULL,
            permitted_subscribers JSONB NOT NULL DEFAULT '[]'::jsonb,
            required_fields       JSONB NOT NULL DEFAULT '[]'::jsonb,
            optional_fields       JSONB NOT NULL DEFAULT '[]'::jsonb,
            materialized_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_domain_event_contracts_publisher
        ON public.domain_event_contracts (publisher)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS public.domain_event_reactions (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_id           UUID NOT NULL REFERENCES public.domain_events(id),
            subscriber_butler  TEXT NOT NULL,
            status             TEXT NOT NULL,
            session_id         TEXT,
            task_name          TEXT,
            note               TEXT,
            evidence           JSONB NOT NULL DEFAULT '[]'::jsonb,
            recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_domain_event_reactions_status
                CHECK (status IN (
                    'scheduled', 'running', 'acted', 'ignored',
                    'deferred', 'failed', 'unreported'
                ))
        )
    """)
    # A wake closes exactly once. Enforced here rather than in application
    # code so a racing correlation sweep cannot append a second, contradictory
    # outcome beside a receipt a session already filed.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_domain_event_reactions_terminal
        ON public.domain_event_reactions (event_id, subscriber_butler)
        WHERE status IN ('acted', 'ignored', 'deferred', 'failed', 'unreported')
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_domain_event_reactions_pair_recorded_at
        ON public.domain_event_reactions (event_id, subscriber_butler, recorded_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_domain_event_reactions_subscriber_status
        ON public.domain_event_reactions (subscriber_butler, status)
    """)

    for table_fqn in (
        "public.domain_event_contracts",
        "public.domain_event_reactions",
    ):
        for role in _ALL_RUNTIME_ROLES:
            _grant_best_effort(table_fqn, _TABLE_PRIVILEGES, role)

    # The projection is publisher-owned and rewritten from git at daemon
    # startup, so DELETE is granted too — a retired declaration must be able
    # to disappear from the dashboard.
    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.domain_event_contracts", "DELETE", role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.domain_event_reactions CASCADE")
    op.execute("DROP TABLE IF EXISTS public.domain_event_contracts CASCADE")
