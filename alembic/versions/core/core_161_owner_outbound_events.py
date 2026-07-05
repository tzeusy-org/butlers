"""owner_outbound_events: durable owner-outbound-message point-event evidence table.

Revision ID: core_161
Revises: core_160
Create Date: 2026-07-05 00:00:00.000000

Creates ``connectors.owner_outbound_events`` — the evidence table the new
Chronicler ``owner_outbound.messages`` projection adapter reads (bu-whhll.8,
epic bu-whhll Tier 1 "workday sensors": the owner's own outbound messages on
``telegram_user_client``/``whatsapp_user_client`` are today discarded as a
signal even though both connectors already observe them).

RECHAIN NOTE (per bu-whhll.8 dispatch guidance): the core migration chain was
contended at dispatch time — ``core_159`` (PR #2943) and ``core_160``
(PR #2944) were both still open/unmerged. This revision was originally
numbered ``core_161`` to reserve the next free slot with ``down_revision``
pointed at ``core_157`` (the chain tip at branch time) so the migration was
self-consistent and testable on its own branch. Both ``core_159`` and
``core_160`` have since merged, and this revision has been rebased and
rechained onto ``core_160`` (see AGENTS.md "Parallel migration revision
collision").

Design notes (mirrors ``core_154_activitywatch_events.py``, the closest prior
art for a connector-owned Tier-1 evidence table):

  - Persists indefinitely (no partitioning/TTL), like
    ``connectors.activitywatch_events`` / ``connectors.owntracks_points``.
  - METADATA ONLY, by design and by schema shape: this table has no content
    column and no counterpart-identity column. It carries only
    ``channel``, ``endpoint_identity`` (the owner's own connector identity,
    not the person they messaged), ``occurred_at`` (message timestamp), and
    a hashed ``idempotency_key`` (see below). No message text, no chat/thread
    id, no counterpart JID/user-id is ever written here.
  - ``idempotency_key`` is a one-way SHA-256 digest of
    ``f"{provider}:{chat_or_jid}:{message_id}"`` computed by the connector.
    Chat/message identifiers are hashed (never stored in cleartext) so the
    dedup handle cannot be reversed into "who the owner was messaging" even
    though it is derived from that information — the bead's privacy
    requirement is "no counterpart identity", so the raw identifier itself
    must never land in the database, cleartext or otherwise recoverable.
  - Written by the connector via ``connector_writer`` role (same as every
    other ``connectors.*`` evidence table).
  - Read by ``butler_chronicler_rw`` for Tier-0 projection into
    ``chronicler.point_events`` (owner_outbound_message point events,
    layer=evidence — never counted as lived time on their own).

RFC 0014 §D8 requires explicit grant inclusion here.
"""

from __future__ import annotations

from alembic import op

revision = "core_161"
down_revision = "core_160"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_CHRONICLER_ROLE = "butler_chronicler_rw"
_SCHEMA = "connectors"
_TABLE = "owner_outbound_events"
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
    # 1. Create connectors.owner_outbound_events
    # -------------------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_FULL_TABLE} (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            idempotency_key     TEXT NOT NULL UNIQUE,
            channel             TEXT NOT NULL,
            endpoint_identity   TEXT NOT NULL,
            occurred_at         TIMESTAMPTZ NOT NULL,
            recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_owner_outbound_events_channel
                CHECK (channel IN ('telegram_user_client', 'whatsapp_user_client'))
        )
    """)

    # ── Indexes ──────────────────────────────────────────────────────────────
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_owner_outbound_events_endpoint_occurred
            ON {_FULL_TABLE} (endpoint_identity, occurred_at DESC)
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_owner_outbound_events_occurred
            ON {_FULL_TABLE} (occurred_at DESC)
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
