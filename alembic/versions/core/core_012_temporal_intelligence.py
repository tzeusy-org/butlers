"""temporal_intelligence: delivery_preferences and deferred_notifications tables

Revision ID: core_012
Revises: core_011
Create Date: 2026-03-27 00:00:00.000000

Creates per-butler tables for the temporal intelligence feature
(spec: openspec/changes/temporal-intelligence/specs/time-aware-delivery/spec.md):

  {schema}.delivery_preferences  — per-butler quiet hours and batch delivery config.
  {schema}.deferred_notifications — notifications deferred during quiet hours.

Design notes:
  - delivery_preferences is keyed on butler_name (UNIQUE) for upsert semantics.
  - deferred_notifications uses a UUID PK; status transitions are one-directional
    (pending -> delivered | expired | cancelled).
  - All DDL is guarded with IF (NOT) EXISTS for idempotency.
  - Each butler's tables live in its own schema with schema-isolated ACL.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_012"
down_revision = "core_011"
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
        # 1. {schema}.delivery_preferences
        # =====================================================================
        op.execute(f"""
            CREATE TABLE IF NOT EXISTS {schema}.delivery_preferences (
                id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                butler_name         TEXT NOT NULL UNIQUE,
                quiet_hours_start   TIME NOT NULL DEFAULT '22:00',
                quiet_hours_end     TIME NOT NULL DEFAULT '07:00',
                timezone            TEXT NOT NULL DEFAULT 'UTC',
                batch_low_priority  BOOLEAN NOT NULL DEFAULT true,
                batch_delivery_time TIME NOT NULL DEFAULT '07:00',
                override_channels   JSONB,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        _grant_if_exists(schema, "delivery_preferences", "SELECT, INSERT, UPDATE, DELETE", role)

        # =====================================================================
        # 2. {schema}.deferred_notifications
        # =====================================================================
        op.execute(f"""
            CREATE TABLE IF NOT EXISTS {schema}.deferred_notifications (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                butler_name  TEXT NOT NULL,
                channel      TEXT NOT NULL,
                message      TEXT NOT NULL,
                priority     TEXT NOT NULL DEFAULT 'medium',
                envelope     JSONB NOT NULL,
                deferred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                deliver_at   TIMESTAMPTZ NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                delivered_at TIMESTAMPTZ,
                CONSTRAINT chk_deferred_notifications_status
                    CHECK (status IN ('pending', 'delivered', 'expired', 'cancelled')),
                CONSTRAINT chk_deferred_notifications_priority
                    CHECK (priority IN ('high', 'medium', 'low'))
            )
        """)

        # Index for the flush pass: pending notifications ordered by deliver_at
        op.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_deferred_notifications_pending_deliver_at
            ON {schema}.deferred_notifications (deliver_at ASC)
            WHERE status = 'pending'
        """)

        _grant_if_exists(schema, "deferred_notifications", "SELECT, INSERT, UPDATE, DELETE", role)


def downgrade() -> None:
    for schema in _BUTLER_SCHEMAS:
        op.execute(
            f"DROP TABLE IF EXISTS {schema}.deferred_notifications"
        )
        op.execute(
            f"DROP TABLE IF EXISTS {schema}.delivery_preferences"
        )
