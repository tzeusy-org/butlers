"""delegation_ledger: cross-butler question/answer ledger (pursuit-0704 follow-on).

Revision ID: core_162
Revises: core_161
Create Date: 2026-07-05 00:00:00.000000

bu-gxmfx — sequenced after the memory_catalog default-on flip (bu-qvnce.15,
PR #2919, merged) per the 2026-07-04 JARVIS pursuit dossier
(``docs/redesigns/2026-07-04-jarvis-pursuit.md``, "Dropped" ledger: "Cross-
butler delegation ask/answer ledger"). See ``src/butlers/core/delegation_ledger.py``
for the writer/reader and ``src/butlers/core_tools/_delegation.py`` for the
``delegate_ask``/``delegate_receive``/``delegate_answer`` MCP tools.

Creates ``public.delegation_ledger``, the single durable record of every
cross-butler delegated question: who asked, what catalog-attributed domain
it was routed to, and (eventually) the answer. Mirrors the shape and grant
pattern of ``public.attention_ledger`` (core_160) — one row per question,
mutated exactly once (pending -> {routed, unroutable, failed} -> answered).

Table design:
  - No FK to ``public.memory_catalog`` on ``catalog_match_id`` — catalog rows
    can be GC'd/superseded independently of the ledger's provenance record,
    and the ledger must still read back after that happens (ON DELETE
    SET NULL semantics without needing an FK trip).
  - Status lifecycle (see ``delegation_ledger.VALID_STATUSES``):
    ``pending`` (row reserved, dispatch in flight) -> ``routed`` (target's
    ``delegate_receive`` acknowledged) | ``unroutable`` (no catalog domain
    match, or resolved target was the asking butler itself) | ``failed``
    (dispatch via Switchboard ``route()`` errored) -> ``answered`` (target
    posted its answer via ``delegate_answer``).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_162"
down_revision = "core_161"
branch_labels = None
depends_on = None

# Mirrors core_160's _ALL_RUNTIME_ROLES — every butler role that may
# participate in cross-butler delegation (ask, receive, or answer).
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
        CREATE TABLE IF NOT EXISTS public.delegation_ledger (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            asked_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            asking_butler     TEXT NOT NULL,
            question          TEXT NOT NULL,
            target_butler     TEXT,
            catalog_match_id  UUID,
            catalog_score     DOUBLE PRECISION,
            status            TEXT NOT NULL DEFAULT 'pending',
            reason            TEXT,
            answer            TEXT,
            answered_at       TIMESTAMPTZ,
            answering_butler  TEXT,
            metadata          JSONB,
            CONSTRAINT chk_delegation_ledger_status
                CHECK (status IN ('pending', 'routed', 'unroutable', 'failed', 'answered'))
        )
    """)

    # Dashboard/ledger-summary read: recent-first listing.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_delegation_ledger_asked_at
        ON public.delegation_ledger (asked_at DESC)
    """)

    # Status-filtered listing (e.g. "show me everything still pending/failed").
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_delegation_ledger_status_asked_at
        ON public.delegation_ledger (status, asked_at DESC)
    """)

    # Per-butler "what have I asked" / "what have I been asked" views.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_delegation_ledger_asking_butler
        ON public.delegation_ledger (asking_butler, asked_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_delegation_ledger_target_butler
        ON public.delegation_ledger (target_butler, asked_at DESC)
        WHERE target_butler IS NOT NULL
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.delegation_ledger", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.delegation_ledger CASCADE")
