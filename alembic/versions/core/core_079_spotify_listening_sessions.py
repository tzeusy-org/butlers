"""spotify_listening_sessions: durable Spotify session-summary evidence table.

Revision ID: core_079
Revises: core_078
Create Date: 2026-04-24 00:00:00.000000

Creates ``connectors.spotify_listening_sessions`` — the long-retention
evidence table that Chronicler's Spotify session-summary adapter reads.

Unlike ``public.ingestion_events`` (audit record, no raw payload) and
``connectors.filtered_events`` (monthly-partitioned, short retention),
this table:

  - Persists indefinitely (no partitioning / TTL by default).
  - Carries stable boundary timestamps (started_at, ended_at).
  - Carries a stable, deterministic idempotency key.
  - Carries source references needed for Chronicler provenance.
  - Is written by the Spotify connector via connector_writer role.
  - Is readable by ``butler_chronicler_rw`` for Tier-0 projection.

Schema design notes
-------------------
``idempotency_key`` is the canonical replay-safety handle.  It follows
the same format as the ingest-envelope's ``control.idempotency_key``:
  ``spotify:<endpoint_identity>:session:<session_start_ms>``

``ended_at`` is nullable to accommodate in-flight sessions that may be
force-closed on connector restart; the connector always supplies it but
callers should treat NULL as "session still open or abnormally closed".

``track_names`` is a JSONB array of ordered track names collected during
the session (mirrors ``ListeningSession.track_names``).

``raw_payload`` is a JSONB snapshot of the full ingest-envelope payload
for archival / forensic use.  It is intentionally nullable so that
connectors that can't cheaply reconstruct the payload can omit it.

RFC 0014 §D8 requires explicit grant inclusion here; see init-db.sql for
the corresponding butler_chronicler_rw grant.
"""

from __future__ import annotations

from alembic import op

revision = "core_079"
down_revision = "core_078"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_CHRONICLER_ROLE = "butler_chronicler_rw"
_SCHEMA = "connectors"
_TABLE = "spotify_listening_sessions"
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
    # 1. Create connectors.spotify_listening_sessions
    # -------------------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_FULL_TABLE} (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            idempotency_key     TEXT NOT NULL UNIQUE,
            endpoint_identity   TEXT NOT NULL,
            spotify_user_id     TEXT NOT NULL,
            started_at          TIMESTAMPTZ NOT NULL,
            ended_at            TIMESTAMPTZ,
            duration_seconds    INTEGER,
            track_count         INTEGER NOT NULL DEFAULT 0,
            track_names         JSONB NOT NULL DEFAULT '[]'::jsonb,
            context_uri         TEXT,
            context_name        TEXT,
            raw_payload         JSONB,
            recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── Indexes ──────────────────────────────────────────────────────────────
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_spotify_listening_sessions_endpoint_started
            ON {_FULL_TABLE} (endpoint_identity, started_at DESC)
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_spotify_listening_sessions_started_at
            ON {_FULL_TABLE} (started_at DESC)
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
