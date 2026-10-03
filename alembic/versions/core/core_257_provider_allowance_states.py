"""Provider allowance windows: account-scoped exhaustion with a reset horizon.

Revision ID: core_257
Revises: core_256
Create Date: 2026-10-03 00:00:00.000000

bu-q7vx1q.13.  A provider plan usage-limit rejection used to look like a model
fault: every catalog entry on the dead account had to fail on its own before the
breaker excluded it, and failover could pick another entry on the same account.

- ``public.model_catalog.allowance_account`` (nullable TEXT): which provider
  account an entry draws allowance from.  NULL means "the runtime default"
  (``runtime_type``), so no catalog row needs a backfill.
- ``public.provider_allowance_states``: one row per account key.  ``state`` is
  ``available`` / ``exhausted`` / ``unknown``; ``reset_at`` and ``reset_source``
  (``parsed`` / ``default_window`` / ``unknown``) say when exhaustion lifts and how
  we know.  ``last_rejection_attempt_id`` points at the attempt row that caused it.
  The table is evidence: rollback of the routing change leaves it in place.

Every statement is idempotent because the core chain is replayed once per butler
schema against these database-global objects.
"""

from __future__ import annotations

from alembic import op

revision = "core_257"
down_revision = "core_256"
branch_labels = None
depends_on = None

_LOCK = "core_257_provider_allowance_states"
_TABLE = "public.provider_allowance_states"

_RUNTIME_ROLES = (
    "butler_calendar_rw",
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
    "connector_writer",
)


def _grant_best_effort(role: str) -> None:
    """Grant SELECT/INSERT/UPDATE to ``role``; tolerate DBs missing the role."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON TABLE {_TABLE} TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtext('{_LOCK}'))")
    op.execute("ALTER TABLE public.model_catalog ADD COLUMN IF NOT EXISTS allowance_account TEXT")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            account_key               TEXT        PRIMARY KEY,
            state                     TEXT        NOT NULL
                CHECK (state IN ('available', 'exhausted', 'unknown')),
            reset_at                  TIMESTAMPTZ,
            reset_source              TEXT        NOT NULL DEFAULT 'unknown'
                CHECK (reset_source IN ('parsed', 'default_window', 'unknown')),
            first_seen_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_rejection_attempt_id BIGINT,
            updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    for role in _RUNTIME_ROLES:
        _grant_best_effort(role)


def downgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtext('{_LOCK}'))")
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
    op.execute("ALTER TABLE public.model_catalog DROP COLUMN IF EXISTS allowance_account")
