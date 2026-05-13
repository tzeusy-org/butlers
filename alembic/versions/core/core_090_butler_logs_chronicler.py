"""Add butler_logs to chronicler schema.

Revision ID: core_090
Revises: core_089
Create Date: 2026-05-14 00:00:00.000000

``core_089`` created ``butler_logs`` in every butler schema present at the
time but missed ``chronicler``, which exists as a domain butler with its own
schema.  Without the table, chronicler cannot persist application logs and
the dashboard logs tab is permanently empty for it.

This migration adds the same table + indexes to the ``chronicler`` schema.
All DDL uses ``IF NOT EXISTS`` so the migration is safe to re-run.
"""

from __future__ import annotations

import logging

from alembic import op

revision = "core_090"
down_revision = "core_089"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_SCHEMA = "chronicler"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SCHEMA}.butler_logs (
            id         BIGSERIAL PRIMARY KEY,
            ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
            level      VARCHAR NOT NULL
                           CHECK (level IN ('DEBUG','INFO','WARN','ERROR')),
            msg        TEXT NOT NULL,
            source     VARCHAR,
            request_id UUID,
            metadata   JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS butler_logs_ts
        ON {_SCHEMA}.butler_logs (ts DESC)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS butler_logs_level
        ON {_SCHEMA}.butler_logs (level)
        """
    )
    logger.info("core_090: created butler_logs in schema %s", _SCHEMA)


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_SCHEMA}.butler_logs_level")
    op.execute(f"DROP INDEX IF EXISTS {_SCHEMA}.butler_logs_ts")
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.butler_logs")
    logger.info("core_090: dropped butler_logs from schema %s", _SCHEMA)
