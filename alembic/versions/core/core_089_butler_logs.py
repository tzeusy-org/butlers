"""butler_logs: structured per-butler log table.

Revision ID: core_089
Revises: core_088
Create Date: 2026-05-11 00:00:00.000000

Creates ``butler_logs`` in each butler schema to persist structured daemon
log lines.  Each butler writes only to its own schema; no ``butler`` column
is needed because isolation is schema-based (see CLAUDE.md § Database Isolation).

Table shape
-----------
- ``id``         — BIGSERIAL surrogate key.
- ``ts``         — Log timestamp (defaults to ``now()``).
- ``level``      — Severity: DEBUG | INFO | WARN | ERROR.
- ``msg``        — Log message body.
- ``source``     — Optional logger / subsystem name.
- ``request_id`` — Optional UUID tying the line to a session request.
- ``metadata``   — Optional JSONB bag for structured fields.
- ``created_at`` — Row insertion timestamp (matches ``ts`` for awaited writes;
                   may differ slightly when written via fire-and-forget tasks).

Indexes
-------
- ``butler_logs_ts``    — on (ts DESC): primary time-range access pattern.
- ``butler_logs_level`` — on (level): secondary filter for error surfacing.

Retention
---------
No automatic vacuum or partition is applied in this migration.
Retention is handled out-of-band (cron job, pg_partman, or operator tooling).

Idempotency
-----------
All DDL uses ``IF NOT EXISTS``, so the migration is safe to run more than once.

Downgrade
---------
Drops the table and indexes from each butler schema.
"""

from __future__ import annotations

import logging

from alembic import op

revision = "core_089"
down_revision = "core_088"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

# Mirrors the per-butler schema list from core_001 / core_050.
_BUTLER_SCHEMAS: tuple[str, ...] = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "qa",
    "relationship",
    "switchboard",
    "travel",
)


def upgrade() -> None:
    for schema in _BUTLER_SCHEMAS:
        # Guard against schemas that were not provisioned by core_001 (e.g.
        # ``qa`` was added to the butler roster after the foundation migration
        # shipped). Idempotent — no-op when the schema already exists.
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        op.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.butler_logs (
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
            ON {schema}.butler_logs (ts DESC)
            """
        )
        op.execute(
            f"""
            CREATE INDEX IF NOT EXISTS butler_logs_level
            ON {schema}.butler_logs (level)
            """
        )
        logger.info("core_089: created butler_logs in schema %s", schema)


def downgrade() -> None:
    for schema in reversed(_BUTLER_SCHEMAS):
        op.execute(f"DROP INDEX IF EXISTS {schema}.butler_logs_level")
        op.execute(f"DROP INDEX IF EXISTS {schema}.butler_logs_ts")
        op.execute(f"DROP TABLE IF EXISTS {schema}.butler_logs")
        logger.info("core_089: dropped butler_logs from schema %s", schema)
