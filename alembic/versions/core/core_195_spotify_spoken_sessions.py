"""Create bounded, connector-owned Spotify spoken-session evidence.

Revision ID: core_195
Revises: core_194
Create Date: 2026-08-09 00:00:00.000000

The capture-only surface deliberately separates podcast episodes and audiobook
chapters from music ``spotify_listening_sessions``. It contains normalized
typed fields plus bounded metadata only; transcripts, descriptions, and raw
Spotify responses are not retained here.
"""

from __future__ import annotations

from alembic import op

revision = "core_195"
down_revision = "core_194"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_CHRONICLER_ROLE = "butler_chronicler_rw"
_TABLE = "connectors.spotify_spoken_sessions"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _grant_if_role_exists(statement: str, role_name: str) -> None:
    """Apply an ACL only when the optional runtime role exists."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)}) THEN
                {statement};
            END IF;
        END;
        $$;
        """
    )


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            idempotency_key     TEXT NOT NULL UNIQUE,
            endpoint_identity   TEXT NOT NULL,
            spotify_user_id     TEXT NOT NULL,
            content_kind        TEXT NOT NULL,
            episode_id          TEXT NOT NULL,
            episode_name        TEXT NOT NULL,
            episode_uri         TEXT,
            parent_id           TEXT,
            parent_name         TEXT,
            parent_uri          TEXT,
            started_at          TIMESTAMPTZ NOT NULL,
            ended_at            TIMESTAMPTZ NOT NULL,
            duration_seconds    INTEGER NOT NULL,
            metadata            JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_spotify_spoken_sessions_content_kind
                CHECK (content_kind IN ('podcast', 'audiobook', 'unknown_episode')),
            CONSTRAINT chk_spotify_spoken_sessions_duration
                CHECK (duration_seconds >= 0),
            CONSTRAINT chk_spotify_spoken_sessions_metadata_bounded
                CHECK (jsonb_typeof(metadata) = 'object' AND octet_length(metadata::text) <= 2048)
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_spotify_spoken_sessions_endpoint_started
            ON {_TABLE} (endpoint_identity, started_at DESC)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_spotify_spoken_sessions_recorded
            ON {_TABLE} (recorded_at DESC)
        """
    )
    _grant_if_role_exists(
        (
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
            f"{_TABLE} TO {_quote_ident(_CONNECTOR_ROLE)}"
        ),
        _CONNECTOR_ROLE,
    )
    _grant_if_role_exists(
        f"GRANT SELECT ON TABLE {_TABLE} TO {_quote_ident(_CHRONICLER_ROLE)}",
        _CHRONICLER_ROLE,
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_TABLE} CASCADE")
