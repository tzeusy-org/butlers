"""spotify_track_plays: per-track progress/skip evidence for the taste ledger.

Revision ID: core_230
Revises: core_229
Create Date: 2026-09-10 00:00:00.000000

Creates ``connectors.spotify_track_plays`` (bu-2jtfw.10, "the taste ledger").
``connectors.spotify_listening_sessions`` (core_079) aggregates a session's
track *names* with no stable per-track id, so it cannot answer "did the owner
finish or skip this track". This table is keyed on the track's stable URI and
carries per-poll progress, closing with a derived ``completion_ratio`` /
``skipped`` when the track changes.

Also grants ``butler_lifestyle_rw`` read access to the pre-existing
``spotify_listening_sessions`` table (core_079 only granted
``butler_chronicler_rw``): the lifestyle taste-ledger resolver needs to read
session evidence directly, mirroring the same read-only pattern.

Schema design notes
--------------------
Idempotence key is ``(endpoint_identity, track_uri, first_seen_ms)`` — not a
single ``idempotency_key`` column — because a play is identified by *when it
started*, not by a value the connector can format ahead of knowing that. The
connector upserts ``max_progress_ms``/``last_seen_ms`` via ``GREATEST`` on
every poll of the same play, then resolves ``completion_ratio``/``skipped``
once when the track changes (``closed_at`` becomes non-null). Gap-filled plays
(from ``/me/player/recently-played``, which carries no ``progress_ms``) are
inserted already closed with ``observation_precision = 'play_only'`` and a
NULL ``completion_ratio``/``skipped`` — an honest "we know it played, not how
much" rather than a fabricated zero or a discarded signal.
"""

from __future__ import annotations

from alembic import op

revision = "core_230"
down_revision = "core_229"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_CHRONICLER_ROLE = "butler_chronicler_rw"
_LIFESTYLE_ROLE = "butler_lifestyle_rw"
_TABLE = "connectors.spotify_track_plays"
_SESSIONS_TABLE = "connectors.spotify_listening_sessions"


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
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            endpoint_identity       TEXT NOT NULL,
            spotify_user_id         TEXT NOT NULL,
            track_uri               TEXT NOT NULL,
            track_name              TEXT,
            first_seen_ms           BIGINT NOT NULL,
            last_seen_ms            BIGINT NOT NULL,
            duration_ms             INTEGER,
            max_progress_ms         INTEGER,
            completion_ratio        DOUBLE PRECISION,
            skipped                 BOOLEAN,
            observation_precision   TEXT NOT NULL DEFAULT 'progress_tracked',
            recently_played_at_ms   BIGINT,
            closed_at               TIMESTAMPTZ,
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_spotify_track_plays_precision
                CHECK (observation_precision IN ('progress_tracked', 'play_only')),
            CONSTRAINT chk_spotify_track_plays_completion_ratio
                CHECK (
                    completion_ratio IS NULL
                    OR (completion_ratio >= 0 AND completion_ratio <= 1)
                )
        )
    """)
    op.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_spotify_track_plays_identity
            ON {_TABLE} (endpoint_identity, track_uri, first_seen_ms)
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_spotify_track_plays_endpoint_recorded
            ON {_TABLE} (endpoint_identity, recorded_at DESC)
    """)
    op.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_spotify_track_plays_open_endpoint
            ON {_TABLE} (endpoint_identity) WHERE closed_at IS NULL
    """)
    op.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_spotify_track_plays_recent_item
            ON {_TABLE} (endpoint_identity, track_uri, recently_played_at_ms)
            WHERE recently_played_at_ms IS NOT NULL
    """)

    _grant_if_role_exists(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {_TABLE}"
        f" TO {_quote_ident(_CONNECTOR_ROLE)}",
        _CONNECTOR_ROLE,
    )
    _grant_if_role_exists(
        f"GRANT SELECT ON TABLE {_TABLE} TO {_quote_ident(_CHRONICLER_ROLE)}",
        _CHRONICLER_ROLE,
    )
    _grant_if_role_exists(
        f"GRANT SELECT ON TABLE {_TABLE} TO {_quote_ident(_LIFESTYLE_ROLE)}",
        _LIFESTYLE_ROLE,
    )

    # The taste-ledger resolver (lifestyle butler) also needs to read the
    # pre-existing session-summary evidence table; core_079 only granted
    # butler_chronicler_rw.
    _grant_if_role_exists(
        f"GRANT SELECT ON TABLE {_SESSIONS_TABLE} TO {_quote_ident(_LIFESTYLE_ROLE)}",
        _LIFESTYLE_ROLE,
    )


def downgrade() -> None:
    _grant_if_role_exists(
        f"REVOKE SELECT ON TABLE {_SESSIONS_TABLE} FROM {_quote_ident(_LIFESTYLE_ROLE)}",
        _LIFESTYLE_ROLE,
    )
    op.execute(f"DROP TABLE IF EXISTS {_TABLE} CASCADE")
