"""delegation wake: durable delegated-answer wake state on public.delegation_ledger.

Revision ID: core_181
Revises: core_180
Create Date: 2026-07-24 00:00:00.000000

bu-27dxl.5.2 — implements the durable representation defined by the merged
``activate-delegation-wake-loop`` OpenSpec change (bu-27dxl.5.1, PR #3514).
See ``src/butlers/core/delegation_ledger.py`` for the ledger writer/reader,
``src/butlers/core/delegation_wake.py`` for the wake state machine and
asker-local task reconciliation, and ``src/butlers/core_tools/_delegation.py``
for the ``delegate_answer``/``delegate_wake`` MCP tools.

Adds to ``public.delegation_ledger``:
  - ``answer_digest``   -- immutable SHA-256 hex digest of the first accepted
                           answer, committed atomically with the answer.
  - ``wake_key``         -- immutable
                           ``delegation-wake:v1:<ledger_id>:<answer_digest>``
                           identity for all callback/replay attempts.
  - ``wake_state``       -- one of ``not_applicable`` (default; no v1 answer
                           yet), ``callback_pending``, ``callback_failed``,
                           ``callback_routed``, ``task_created``,
                           ``task_conflict``.
  - ``wake_task_id`` / ``wake_task_name`` -- the asker-local
                           ``scheduled_tasks`` binding once known. No FK: the
                           row lives in the asking butler's own schema, not
                           ``public`` (RFC 0006) -- mirrors ``catalog_match_id``
                           having no FK to ``public.memory_catalog`` for the
                           same cross-lifecycle reason.
  - ``wake_updated_at``  -- bookkeeping timestamp for the most recent wake
                           transition.

Adds ``public.delegation_wake_attempts``, an append-only audit log of
callback-dispatch and wake-reconciliation attempts (evidence for the
"honest partial success" contract -- a callback failure must remain durably
auditable, never silently dropped). Mirrors the shape/grant pattern of
``public.model_dispatch_attempts`` (core_104).

Existing answered rows retain ``wake_state='not_applicable'`` with NULL
``answer_digest``/``wake_key`` -- legacy rows per the spec, discoverable but
never auto-woken or backfilled.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_181"
down_revision = "core_180"
branch_labels = None
depends_on = None

# Mirrors core_162's _ALL_RUNTIME_ROLES — every butler role that may
# participate in cross-butler delegation (ask, receive, answer, or wake).
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

_LEDGER_COLUMN_PRIVILEGES = "SELECT, INSERT, UPDATE"
_ATTEMPTS_TABLE_PRIVILEGES = "SELECT, INSERT"


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
        ALTER TABLE public.delegation_ledger
            ADD COLUMN IF NOT EXISTS answer_digest TEXT,
            ADD COLUMN IF NOT EXISTS wake_key TEXT,
            ADD COLUMN IF NOT EXISTS wake_state TEXT NOT NULL DEFAULT 'not_applicable',
            ADD COLUMN IF NOT EXISTS wake_task_id UUID,
            ADD COLUMN IF NOT EXISTS wake_task_name TEXT,
            ADD COLUMN IF NOT EXISTS wake_updated_at TIMESTAMPTZ
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'chk_delegation_ledger_wake_state'
            ) THEN
                ALTER TABLE public.delegation_ledger
                    ADD CONSTRAINT chk_delegation_ledger_wake_state
                    CHECK (wake_state IN (
                        'not_applicable', 'callback_pending', 'callback_failed',
                        'callback_routed', 'task_created', 'task_conflict'
                    ));
            END IF;
        END
        $$;
    """)

    # wake_key is the immutable replay identity — unique whenever set (a
    # given answer digest can only ever bind to one ledger row's wake).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_delegation_ledger_wake_key
        ON public.delegation_ledger (wake_key)
        WHERE wake_key IS NOT NULL
    """)

    # Dashboard/observability: "what's stuck in callback_failed/task_conflict".
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_delegation_ledger_wake_state
        ON public.delegation_ledger (wake_state, asked_at DESC)
        WHERE wake_state != 'not_applicable'
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS public.delegation_wake_attempts (
            id             BIGSERIAL PRIMARY KEY,
            ledger_id      UUID NOT NULL REFERENCES public.delegation_ledger(id) ON DELETE CASCADE,
            ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
            stage          TEXT NOT NULL,
            result         TEXT NOT NULL,
            retryable      BOOLEAN,
            error_class    TEXT,
            error_message  TEXT,
            actor_butler   TEXT
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_delegation_wake_attempts_ledger_ts
        ON public.delegation_wake_attempts (ledger_id, ts DESC)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.delegation_ledger", _LEDGER_COLUMN_PRIVILEGES, role)
        _grant_best_effort("public.delegation_wake_attempts", _ATTEMPTS_TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.delegation_wake_attempts")
    op.execute("DROP INDEX IF EXISTS public.idx_delegation_ledger_wake_state")
    op.execute("DROP INDEX IF EXISTS public.uq_delegation_ledger_wake_key")
    op.execute("""
        ALTER TABLE public.delegation_ledger
            DROP CONSTRAINT IF EXISTS chk_delegation_ledger_wake_state
    """)
    op.execute("""
        ALTER TABLE public.delegation_ledger
            DROP COLUMN IF EXISTS wake_updated_at,
            DROP COLUMN IF EXISTS wake_task_name,
            DROP COLUMN IF EXISTS wake_task_id,
            DROP COLUMN IF EXISTS wake_state,
            DROP COLUMN IF EXISTS wake_key,
            DROP COLUMN IF EXISTS answer_digest
    """)
