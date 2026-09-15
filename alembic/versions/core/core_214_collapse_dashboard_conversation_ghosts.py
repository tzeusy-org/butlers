"""Collapse dashboard-channel ghost conversation anchors onto their parent.

Revision ID: core_214
Revises: core_213
Create Date: 2026-09-05 00:00:00.000000

Context (bu-0ynlk.5)
---------------------
``src/butlers/core_tools/_routing.py`` used to call
``conversation_get_or_create_by_thread`` for every inbound thread that
carries a ``source_thread_identity`` -- including dashboard-channel turns,
whose ``source_thread_identity`` is actually the id of the
``dashboard_conversations`` row the owner is already looking at (see
``build_dashboard_envelope``'s ``external_thread_id``), not a channel key to
upsert against. That treated the parent's own id as a *new* thread identity,
forking a second, invisible "ghost" anchor row
(``source_channel = 'dashboard'``, ``source_thread_identity`` = the parent's
id as text) that the Spawner attached a provider resume handle to and that
``conversation_reply`` wrote assistant replies into -- while the dashboard
SSE poller only ever watched the parent row, so those replies were
persisted but never seen.

This migration is a one-time data repair: for every ghost anchor, its
messages are re-pointed onto the parent (recovering any stranded replies),
the parent keeps whichever of (parent, ghost) has the more recently updated
``provider_session_*`` handle, and the now-empty ghost row is deleted. The
code-path fix (routing resolves the existing conversation by id for the
dashboard channel instead of upserting) ships alongside this migration.

Idempotent: a re-run finds no rows matching the ghost shape (real anchors
have ``source_thread_identity IS NULL``) and no-ops.

Irreversible: ``downgrade`` cannot resurrect a deleted ghost row or split
folded messages back off of the parent -- this is a data cleanup, not a
schema change. Ghost/parent ids are logged via ``RAISE NOTICE`` before
delete for forensic recovery if ever needed.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_214"
down_revision = "core_213"
branch_labels = None
depends_on = None

_COLLAPSE_SQL = r"""
DO $$
DECLARE
    _ghost RECORD;
    _folded_ids uuid[] := '{}';
BEGIN
    IF to_regclass('public.dashboard_conversations') IS NULL THEN
        RETURN;
    END IF;

    FOR _ghost IN
        SELECT g.id AS ghost_id, p.id AS parent_id
        FROM public.dashboard_conversations g
        JOIN public.dashboard_conversations p
            ON p.id::text = g.source_thread_identity
           AND p.source_thread_identity IS NULL
           AND p.id <> g.id
        WHERE g.source_channel = 'dashboard'
          AND g.source_thread_identity IS NOT NULL
    LOOP
        -- Recover any replies stranded on the ghost onto the parent thread.
        UPDATE public.dashboard_messages
        SET conversation_id = _ghost.parent_id
        WHERE conversation_id = _ghost.ghost_id;

        -- Keep whichever of (parent, ghost) minted the more recent provider
        -- resume handle -- "one memory per thread" (core_185).
        UPDATE public.dashboard_conversations parent
        SET provider_session_id = ghost.provider_session_id,
            provider_runtime_type = ghost.provider_runtime_type,
            provider_session_updated_at = ghost.provider_session_updated_at
        FROM public.dashboard_conversations ghost
        WHERE parent.id = _ghost.parent_id
          AND ghost.id = _ghost.ghost_id
          AND ghost.provider_session_updated_at IS NOT NULL
          AND (
              parent.provider_session_updated_at IS NULL
              OR ghost.provider_session_updated_at > parent.provider_session_updated_at
          );

        -- Reconcile message_count now that the ghost's messages folded in.
        UPDATE public.dashboard_conversations
        SET message_count = (
                SELECT count(*) FROM public.dashboard_messages
                WHERE conversation_id = _ghost.parent_id
            ),
            updated_at = now()
        WHERE id = _ghost.parent_id;

        _folded_ids := array_append(_folded_ids, _ghost.ghost_id);
    END LOOP;

    IF array_length(_folded_ids, 1) > 0 THEN
        RAISE NOTICE 'core_214: folding % dashboard ghost conversation anchor(s): %',
            array_length(_folded_ids, 1), _folded_ids;
        DELETE FROM public.dashboard_conversations WHERE id = ANY(_folded_ids);
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute(_COLLAPSE_SQL)


def downgrade() -> None:
    # Irreversible data cleanup -- deleted ghost rows and the messages folded
    # off of them cannot be reconstructed. See module docstring.
    pass
