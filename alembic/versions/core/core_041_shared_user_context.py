"""shared_user_context: create shared.user_context table for situational context bus

Revision ID: core_041
Revises: core_040
Create Date: 2026-03-28 00:00:00.000000

Creates the ``shared.user_context`` table that backs the situational context bus
(``src/butlers/context_bus.py``).  Butlers read and write context signals
(traveling, sleeping, meeting, etc.) with TTL-based expiry, confidence scoring,
and per-signal write permissions enforced at the application layer.

Schema:

  shared.user_context
  -------------------
  id              UUID PK DEFAULT gen_random_uuid()
  signal_type     TEXT NOT NULL
  value           TEXT (nullable)
  set_by_butler   TEXT NOT NULL
  set_at          TIMESTAMPTZ NOT NULL DEFAULT now()
  expires_at      TIMESTAMPTZ NOT NULL
  confidence      REAL NOT NULL DEFAULT 1.0  CHECK (confidence BETWEEN 0.0 AND 1.0)
  metadata        JSONB (nullable)
  superseded_at   TIMESTAMPTZ (nullable)

Constraints / indexes:
  - UNIQUE (signal_type, set_by_butler)  — one active entry per butler per type
  - idx_user_context_active_signal_type — partial index on signal_type WHERE
    superseded_at IS NULL for fast active-signal queries
  - idx_user_context_expires_at         — for TTL sweep jobs

Grants:
  SELECT, INSERT, UPDATE, DELETE on shared.user_context to all known butler roles.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_041"
down_revision = "core_040"
branch_labels = None
depends_on = None

# All butler roles that need access to shared.user_context.
# Matches the set from core_023 / core_014 / core_015.
_ALL_BUTLER_ROLES = (
    "butler_switchboard_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_relationship_rw",
    "butler_messenger_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_home_rw",
    "butler_travel_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role, guarded by table and role existence."""
    safe_table_fqn = table_fqn.replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{safe_table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create the shared.user_context table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.user_context (
            id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            signal_type   TEXT        NOT NULL,
            value         TEXT,
            set_by_butler TEXT        NOT NULL,
            set_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at    TIMESTAMPTZ NOT NULL,
            confidence    REAL        NOT NULL DEFAULT 1.0
                          CHECK (confidence >= 0.0 AND confidence <= 1.0),
            metadata      JSONB,
            superseded_at TIMESTAMPTZ,
            CONSTRAINT uq_user_context_signal_butler
                UNIQUE (signal_type, set_by_butler)
        )
    """)

    # -------------------------------------------------------------------------
    # 2. Partial index for fast active-signal queries
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_context_active_signal_type
        ON shared.user_context (signal_type)
        WHERE superseded_at IS NULL
    """)

    # -------------------------------------------------------------------------
    # 3. Index on expires_at for TTL sweep jobs
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_context_expires_at
        ON shared.user_context (expires_at)
    """)

    # -------------------------------------------------------------------------
    # 4. Grant table privileges to all butler roles
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _grant_if_table_exists("shared.user_context", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS shared.user_context")
