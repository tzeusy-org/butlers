"""Create the shared task-continuity ledger and its dispatch-seam columns.

Revision ID: core_229
Revises: core_228
Create Date: 2026-09-09 00:05:00.000000

bu-2jtfw.13: a recurring task that opts in (``scheduled_tasks.continuity =
true``) records what it concluded via the ``carry_forward`` core tool, and the
scheduler injects that record into the next run's dispatched prompt. This
mirrors the chronicler's bespoke day-close cache with a general primitive
without retiring that hook (see the bead's Non-goals).

``public.task_continuity`` is database-global (like
``core_210_expected_signals.py``), keyed by ``(butler_name, task_name)`` for
the single "live" row per task. ``(butler_name, task_name, session_id)`` is
the idempotence key so calling ``carry_forward`` twice in one session updates
the same row instead of appending a duplicate. The partial unique index over
``is_live`` rows is the hard backstop that guarantees exactly one live row per
task even under a true concurrent write from two sessions; the scheduler's
existing atomic occurrence claim (``core.scheduler.tick`` optimistic
``next_run_at`` UPDATE) is what normally prevents that race from arising at
all.

``sessions.continuation_of_session_id`` and ``scheduled_tasks.continuity`` are
per-schema columns (unqualified, fanning out via the existing core-chain
replay across every butler schema — see ``core_061_runtime_config.py``).
"""

from __future__ import annotations

from alembic import op

revision = "core_229"
down_revision = "core_228"
branch_labels = None
depends_on = None

_ALL_BUTLER_ROLES = (
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


def _grant_best_effort(role: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.task_continuity') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON TABLE public.task_continuity '
                        'TO "{role}"';
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
    op.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_229:task_continuity', 0))"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.task_continuity (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name     TEXT NOT NULL,
            task_name       TEXT NOT NULL,
            session_id      UUID NOT NULL,
            producer_role   TEXT NOT NULL DEFAULT current_user,
            carry_forward   TEXT NOT NULL,
            is_live         BOOLEAN NOT NULL DEFAULT TRUE,
            recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_task_continuity_session
            ON public.task_continuity (butler_name, task_name, session_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_task_continuity_live
            ON public.task_continuity (butler_name, task_name)
            WHERE is_live
        """
    )
    op.execute("ALTER TABLE public.task_continuity ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.task_continuity FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS task_continuity_read ON public.task_continuity")
    op.execute(
        "CREATE POLICY task_continuity_read ON public.task_continuity FOR SELECT USING (true)"
    )
    op.execute("DROP POLICY IF EXISTS task_continuity_insert_own ON public.task_continuity")
    op.execute(
        "CREATE POLICY task_continuity_insert_own ON public.task_continuity "
        "FOR INSERT WITH CHECK (producer_role = current_user)"
    )
    op.execute("DROP POLICY IF EXISTS task_continuity_update_own ON public.task_continuity")
    op.execute(
        "CREATE POLICY task_continuity_update_own ON public.task_continuity "
        "FOR UPDATE USING (producer_role = current_user) "
        "WITH CHECK (producer_role = current_user)"
    )
    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort(role)

    op.execute(
        """
        ALTER TABLE sessions
        ADD COLUMN IF NOT EXISTS continuation_of_session_id UUID NULL REFERENCES sessions(id)
        """
    )
    op.execute(
        """
        ALTER TABLE scheduled_tasks
        ADD COLUMN IF NOT EXISTS continuity boolean NOT NULL DEFAULT false
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE scheduled_tasks DROP COLUMN IF EXISTS continuity")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS continuation_of_session_id")
    op.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_229:task_continuity', 0))"
    )
    op.execute("DROP TABLE IF EXISTS public.task_continuity")
