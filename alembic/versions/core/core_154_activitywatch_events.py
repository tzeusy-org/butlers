"""activitywatch_events: durable ActivityWatch desktop-activity evidence table.

Revision ID: core_154
Revises: core_153
Create Date: 2026-07-05 00:00:00.000000

Creates ``connectors.activitywatch_events`` — the long-retention evidence
table that Chronicler's ActivityWatch projection adapter reads (bu-whhll.6,
epic bu-whhll Tier 1 "gold standard": no connector observes any computer
today).

Unlike ``public.ingestion_events`` (audit record, no raw payload) and
``connectors.filtered_events`` (monthly-partitioned, short retention), this
table:

  - Persists indefinitely (no partitioning / TTL by default), mirroring
    ``connectors.owntracks_points`` (core_081).
  - Carries one row per ActivityWatch ``aw-watcher-window`` focus event
    (app + optional window title + duration), plus the derived app-class
    bucket and AFK status the connector computed at ingest time.
  - Carries a stable, deterministic idempotency key.
  - Is written by the ActivityWatch connector via ``connector_writer`` role.
  - Is readable by ``butler_chronicler_rw`` for Tier-0 projection.

Schema design notes
--------------------
``idempotency_key`` is the canonical replay-safety handle, format:
  ``activitywatch:<machine_id>:<bucket_id>:<ts_iso>``

``window_title`` is nullable and privacy=sensitive by *convention*: the
Chronicler projection adapter never surfaces this column's contents (see
``src/butlers/chronicler/adapters/activitywatch.py``) — only ``app_class``
(and, in full ingestion tier, ``app``) reach the ingest.v1 envelope /
chronicler point events. The raw title is retained here only for forensic
/ future-reclassification use.

``app_class`` is the connector-computed bucket (``ide``, ``terminal``,
``browser``, ``other``) — see ``classify_app()`` in the connector module.

``is_afk`` reflects the nearest ``aw-watcher-afk`` bucket status at the
event's start time; ``NULL`` when no AFK bucket was available on that
machine (afk watcher not installed).

RFC 0014 §D8 requires explicit grant inclusion here.
"""

from __future__ import annotations

from alembic import op

revision = "core_154"
down_revision = "core_153"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_CHRONICLER_ROLE = "butler_chronicler_rw"
_SCHEMA = "connectors"
_TABLE = "activitywatch_events"
_FULL_TABLE = f"{_SCHEMA}.{_TABLE}"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str, *, role_name: str | None = None) -> None:
    """Execute a DDL statement only when the prerequisite role exists.

    Silently skips if the role is missing (non-prod DB without all roles).
    """
    if role_name is not None:
        condition = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)})"
    else:
        condition = "TRUE"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                {statement};
            END IF;
        END;
        $$
        """
    )


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create connectors.activitywatch_events
    # -------------------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_FULL_TABLE} (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            idempotency_key     TEXT NOT NULL UNIQUE,
            machine_id          TEXT NOT NULL,
            endpoint_identity   TEXT NOT NULL,
            bucket_id           TEXT NOT NULL,
            ts                  TIMESTAMPTZ NOT NULL,
            duration_seconds    DOUBLE PRECISION NOT NULL,
            app                 TEXT NOT NULL,
            window_title        TEXT,
            app_class           TEXT NOT NULL,
            is_afk              BOOLEAN,
            raw_payload         JSONB,
            recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_activitywatch_events_app_class
                CHECK (app_class IN ('ide', 'terminal', 'browser', 'other')),
            CONSTRAINT chk_activitywatch_events_duration
                CHECK (duration_seconds >= 0)
        )
    """)

    # ── Indexes ──────────────────────────────────────────────────────────────
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_activitywatch_events_endpoint_ts
            ON {_FULL_TABLE} (endpoint_identity, ts DESC)
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_activitywatch_events_ts
            ON {_FULL_TABLE} (ts DESC)
    """)

    # -------------------------------------------------------------------------
    # 2. Grants
    # -------------------------------------------------------------------------

    # connector_writer: full DML on the table (write path).
    _execute_best_effort(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {_FULL_TABLE}"
        f" TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )

    # butler_chronicler_rw: read-only (projection path, RFC 0014 §D1).
    # The schema USAGE grant is already in place from init-db.sql; the
    # per-table grant is what RFC 0014 §D8 requires here.
    _execute_best_effort(
        f"GRANT SELECT ON TABLE {_FULL_TABLE} TO {_quote_ident(_CHRONICLER_ROLE)}",
        role_name=_CHRONICLER_ROLE,
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_FULL_TABLE} CASCADE")
