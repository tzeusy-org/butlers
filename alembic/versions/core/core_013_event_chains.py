"""event_chains: create per-butler event chain configuration table.

Revision ID: core_013
Revises: core_012
Create Date: 2026-03-28 00:00:00.000000

Creates the ``event_chains`` table in each butler's own schema (accessed
via the active search_path, exactly like ``sessions`` and ``state``).

Event chains define automated action sequences triggered by events such as
calendar event end, deadline expiry, or threshold alerts.

Schema:

  event_chains
  ------------
  id                UUID PK DEFAULT gen_random_uuid()
  name              TEXT NOT NULL   -- UNIQUE per butler
  trigger_type      TEXT NOT NULL   -- CHECK (calendar_event_end | deadline_passed |
                                  --        deadline_threshold)
  trigger_reference TEXT            -- event_id or task_id referenced by trigger
  actions           JSONB NOT NULL  -- ordered array of action dicts
  status            TEXT NOT NULL DEFAULT 'active'  CHECK (active | fired | disabled)
  butler_name       TEXT NOT NULL
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()

Indexes:
  idx_event_chains_butler_name     — filter by butler (primary access pattern)
  idx_event_chains_trigger_type    — tick() chain detection pass
  idx_event_chains_status          — filter active chains efficiently

Design notes:
  - name is UNIQUE per butler (enforced by a partial UNIQUE index on (name, butler_name)).
  - trigger_reference is optional; when NULL the chain fires for all events of that type.
  - actions JSONB is an ordered array: [{action_type, delay_minutes, ...}, ...].
  - status transitions: active → fired | disabled (one-way; re-enable via UPDATE).
  - All DDL is guarded with IF (NOT) EXISTS for idempotency.
  - Each butler's tables live in its own schema with schema-isolated ACL.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_013"
down_revision = "core_012"
branch_labels = None
depends_on = None

_BUTLER_SCHEMAS = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "relationship",
    "switchboard",
    "travel",
)

_RUNTIME_ROLES = {schema: f"butler_{schema}_rw" for schema in _BUTLER_SCHEMAS}


def _grant_if_exists(schema: str, table: str, privilege: str, role: str) -> None:
    """GRANT privilege ON schema.table TO role only when both exist."""
    safe_fqn = f"{schema}.{table}".replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{safe_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {schema}.{table} TO "{role}"';
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
    for schema in _BUTLER_SCHEMAS:
        role = _RUNTIME_ROLES[schema]

        # =====================================================================
        # 1. {schema}.event_chains
        # =====================================================================
        op.execute(f"""
            CREATE TABLE IF NOT EXISTS {schema}.event_chains (
                id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                name              TEXT        NOT NULL,
                trigger_type      TEXT        NOT NULL,
                trigger_reference TEXT,
                actions           JSONB       NOT NULL,
                status            TEXT        NOT NULL DEFAULT 'active',
                butler_name       TEXT        NOT NULL,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT chk_event_chains_trigger_type
                    CHECK (trigger_type IN (
                        'calendar_event_end',
                        'deadline_passed',
                        'deadline_threshold'
                    )),
                CONSTRAINT chk_event_chains_status
                    CHECK (status IN ('active', 'fired', 'disabled'))
            )
        """)

        # UNIQUE name per butler (enforced via a unique index on the pair)
        op.execute(f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_event_chains_name_butler
            ON {schema}.event_chains (name, butler_name)
        """)

        # Index for tick() chain detection (filter by trigger_type + status)
        op.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_event_chains_trigger_type
            ON {schema}.event_chains (trigger_type)
            WHERE status = 'active'
        """)

        # Index for butler-scoped queries
        op.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_event_chains_butler_name
            ON {schema}.event_chains (butler_name)
        """)

        _grant_if_exists(schema, "event_chains", "SELECT, INSERT, UPDATE, DELETE", role)


def downgrade() -> None:
    for schema in _BUTLER_SCHEMAS:
        op.execute(f"DROP TABLE IF EXISTS {schema}.event_chains")
