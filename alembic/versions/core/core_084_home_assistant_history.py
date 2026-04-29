"""home_assistant_history: durable Home Assistant state-change evidence table.

Revision ID: core_084
Revises: core_083
Create Date: 2026-04-30 00:00:00.000000

Creates ``connectors.home_assistant_history`` — the long-retention evidence
table that Chronicler's HomeAssistantHistoryAdapter reads when projecting
presence episodes from Home Assistant state-change events.

Unlike ``public.ingestion_events`` (audit record, no raw payload) and
``connectors.filtered_events`` (monthly-partitioned, short retention),
this table:

  - Persists indefinitely (no partitioning / TTL by default).
  - Carries the full HA state-change tuple (entity_id, state, attributes).
  - Is written by the Home Assistant connector via connector_writer role.
  - Is readable by ``butler_chronicler_rw`` for Tier-0 projection.

Schema design notes
-------------------
``entity_id`` is the Home Assistant entity identifier, e.g. ``person.tzeusy``.

``state`` is the new state value after the transition, e.g. ``home`` or
``not_home``.  Nullable to accommodate entities whose state may be ``unknown``
or whose connector omits the field on error.

``attributes`` is a JSONB snapshot of the HA event attributes payload.
Nullable — connectors that cannot reconstruct attributes may omit it.

``recorded_at`` is the monotonically-written server-side timestamp used as
the Chronicler watermark.  Indexed together with ``id`` to support the
tuple-comparison watermark cursor used in _fetch_rows:
  ``WHERE (recorded_at, id) > ($1, $2)``

RFC 0014 §D8 requires explicit grant inclusion here.
"""

from __future__ import annotations

from alembic import op

revision = "core_084"
down_revision = "core_083"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_CHRONICLER_ROLE = "butler_chronicler_rw"
_SCHEMA = "connectors"
_TABLE = "home_assistant_history"
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
    # 1. Create connectors.home_assistant_history
    # -------------------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_FULL_TABLE} (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_id       TEXT NOT NULL,
            state           TEXT,
            attributes      JSONB,
            recorded_at     TIMESTAMPTZ NOT NULL
        )
    """)

    # ── Indexes ──────────────────────────────────────────────────────────────
    # Composite index supports the tuple-comparison watermark cursor and
    # chronicles-style batch queries ordered by (recorded_at ASC, id ASC).
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_home_assistant_history_entity_recorded_at
            ON {_FULL_TABLE} (entity_id, recorded_at DESC)
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_home_assistant_history_recorded_at_id
            ON {_FULL_TABLE} (recorded_at ASC, id ASC)
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
