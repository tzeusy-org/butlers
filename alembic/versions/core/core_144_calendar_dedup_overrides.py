"""core_144 — calendar cross-source duplicate review: rules + keep-separate overrides.

Revision ID: core_144
Revises: core_143
Create Date: 2026-06-22 00:00:00.000000

Backs the calendar cross-source duplicate review surface (bead bu-tjo2m1, epic
bu-l3k0zg).  The workspace read-model silently collapses cross-source duplicate
events via a two-pass dedup (origin_ref identity, then title/start collapse).
These two ``public`` tables let the user *see* the collapsed clusters and steer
the collapse:

``public.calendar_dedup_rules`` — a single owner-scoped row (``id = TRUE``)
holding the active match strategy and the noisy-cluster reporting threshold.

``public.calendar_dedup_overrides`` — one row per cluster the user has chosen to
**keep separate** (i.e. NOT collapse); the dedup skips collapsing any cluster
whose ``cluster_key`` is present here.

Both tables live in ``public`` (not per-butler) because the dedup operates on the
cross-schema merge of the workspace read — the clusters and the rules that govern
them are workspace-global, not owned by any one butler schema.  CRUD is granted
to every butler runtime role (mirroring ``core_137`` /
``public.owner_scheduling_preferences``); the dashboard API reads/writes them
through whichever calendar-enabled butler pool it picks deterministically.

The migration is idempotent (``CREATE TABLE IF NOT EXISTS`` on explicit
``public.`` names) so the per-schema migration runner creates each table once and
no-ops on every subsequent schema.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_144"
down_revision = "core_143"
branch_labels = None
depends_on = None

_RULES_TABLE = "public.calendar_dedup_rules"
_OVERRIDES_TABLE = "public.calendar_dedup_overrides"

_ALL_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "butler_qa_rw",
    "butler_chronicler_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerates missing role/table."""
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
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_RULES_TABLE} (
            id              BOOLEAN PRIMARY KEY DEFAULT TRUE,
            match_strategy  TEXT NOT NULL DEFAULT 'balanced',
            noisy_threshold INTEGER NOT NULL DEFAULT 2,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_calendar_dedup_rules_singleton CHECK (id = TRUE),
            CONSTRAINT chk_calendar_dedup_rules_strategy
                CHECK (match_strategy IN ('exact', 'balanced', 'aggressive')),
            CONSTRAINT chk_calendar_dedup_rules_threshold
                CHECK (noisy_threshold >= 2)
        )
    """)

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_OVERRIDES_TABLE} (
            cluster_key TEXT PRIMARY KEY,
            match_pass  TEXT NOT NULL DEFAULT '',
            label       TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort(_RULES_TABLE, _TABLE_PRIVILEGES, role)
        _grant_best_effort(_OVERRIDES_TABLE, _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_OVERRIDES_TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {_RULES_TABLE}")
