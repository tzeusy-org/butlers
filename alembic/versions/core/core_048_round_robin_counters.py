"""round_robin_counters: add model_round_robin_counters table.

Revision ID: core_048
Revises: core_047
Create Date: 2026-04-02 00:00:00.000000

Adds ``public.model_round_robin_counters`` to support round-robin model
selection among same-priority catalog entries.  Each ``(butler_name,
complexity_tier)`` pair maintains an independent counter that is atomically
incremented on every ``resolve_model()`` call.
"""

from __future__ import annotations

import logging

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_048"
down_revision = "core_047"
branch_labels = None
depends_on = None

log = logging.getLogger(__name__)

_ALL_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)

_TABLE_FQN = "public.model_round_robin_counters"
_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerates missing role/table."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
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
        CREATE TABLE IF NOT EXISTS public.model_round_robin_counters (
            butler_name      TEXT NOT NULL,
            complexity_tier  TEXT NOT NULL,
            counter          BIGINT NOT NULL DEFAULT 0,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (butler_name, complexity_tier),
            CONSTRAINT chk_rr_complexity_tier
                CHECK (complexity_tier IN (
                    'trivial', 'medium', 'high', 'extra_high',
                    'discretion', 'self_healing'
                ))
        )
    """)

    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort(_TABLE_FQN, _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.model_round_robin_counters")
