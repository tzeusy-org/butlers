"""deployments: durable per-boot ledger of what code+schema state is running.

Revision ID: core_163
Revises: core_162
Create Date: 2026-07-11 00:00:00.000000

bu-9r3hd.2 (epic bu-9r3hd "Deploy spine", slice 2/5) — per the 2026-07-10
JARVIS pursuit dossier (``docs/redesigns/2026-07-10-jarvis-pursuit.md``
§Ranked moves #7): "merged != deployed drift is structural at fleet
velocity, and nothing can even know it." Seven merged revisions
(core_155..161) sat dark in prod with no record of when any deploy actually
took effect.

Creates ``public.deployments``, one row per ``butlers up`` process boot: the
git commit it was built from, a representative Alembic migration head, and a
coarse success/failure result. This is a ledger, not the drift detector
itself — the hourly alembic-head vs per-schema DB-revision vs deployed-SHA
comparison surfaced as a red `/system` clause is bu-9r3hd.1's job; this table
is what that sentinel (and the dashboard) reads to answer "what is currently
running".

Table design:
  - Written once per boot by ``butlers.cli._start_all`` (see
    ``src/butlers/core/deployments.py``) — not per-butler-daemon, since all
    butlers share one process/container in this deploy topology and a
    per-butler write would produce N duplicate rows per actual deploy.
  - ``migration_head`` is read from a single representative schema's
    ``alembic_version`` table (the first-started daemon, conventionally
    ``switchboard`` — see ``_PRIORITY_BUTLERS`` in ``cli.py``) rather than
    reconciling every schema's head. Different schemas can legitimately carry
    different heads (butler-specific migration chains), so this field is an
    observability snapshot, not a cross-schema drift proof.
  - ``result`` is `'success'` when every configured butler daemon started,
    `'failed'` otherwise. No `'in_progress'` state in this slice — the
    one-command `butlers deploy` verb (bu-9r3hd.3) that builds, migrates, and
    verifies `/health` before recording will add that phase later.
  - Mirrors the shape and grant pattern of ``public.delegation_ledger``
    (core_162): one row per event, granted to every butler runtime role
    since any daemon may end up recording the boot (whichever role wins the
    priority-ordered start race).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_163"
down_revision = "core_162"
branch_labels = None
depends_on = None

# Mirrors core_162's _ALL_RUNTIME_ROLES — every butler role whose daemon
# process could be the one recording the boot (see _PRIORITY_BUTLERS
# ordering in cli.py; in practice it's whichever daemon starts first).
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

_TABLE_PRIVILEGES = "SELECT, INSERT"


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
        CREATE TABLE IF NOT EXISTS public.deployments (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            git_sha           TEXT NOT NULL,
            migration_head    TEXT,
            started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at       TIMESTAMPTZ,
            result            TEXT NOT NULL,
            CONSTRAINT chk_deployments_result
                CHECK (result IN ('success', 'failed'))
        )
    """)

    # Dashboard read: current deployment (most recent row) + recent history.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_deployments_started_at
        ON public.deployments (started_at DESC)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.deployments", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.deployments CASCADE")
