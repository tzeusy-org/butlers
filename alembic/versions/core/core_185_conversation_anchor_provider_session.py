"""Channel-agnostic conversation anchor + provider resume ledger (bu-ep4ks.8).

Revision ID: core_185
Revises: core_184
Create Date: 2026-07-26 00:00:00.000000

Numbering note: chain head at authoring time was core_183. A parallel worker
(bu-ep4ks.6, PR #3581) held core_184 in flight and merged to main first; this
revision was rechained onto core_184 (down_revision updated from core_183)
before merging, per the repo's duplicate-revision-collision lore (core_160 /
core_168 sagas).

Motivation
----------
``public.dashboard_conversations`` (core_006) is dashboard-only: rows are
created exclusively by the dashboard chat API, anchored on a client-generated
``id``. Every other inbound channel (Telegram, email) already normalizes a
stable ``source_thread_identity`` at ingest (see
``request_context ->> 'source_thread_identity'`` on ``message_inbox`` and its
consumers in ``src/butlers/modules/pipeline.py``), but has no durable
conversation anchor row of its own — each channel re-derives ad hoc context
windows from raw ``message_inbox`` history instead of a persisted lineage
record it can attach a provider resume handle to.

This migration generalizes the table into a channel-agnostic anchor:

  source_channel               TEXT NOT NULL DEFAULT 'dashboard'
      Origin channel for the conversation ('dashboard', 'telegram', 'email',
      ...). Defaults to 'dashboard' so every pre-existing row (all
      dashboard-created) backfills correctly with zero data migration.

  source_thread_identity       TEXT NULL
      The channel-normalized thread identity (mirrors
      ``message_inbox.request_context ->> 'source_thread_identity'``).
      NULL for existing dashboard rows -- those are already anchored 1:1 on
      ``id`` and never need thread-identity lookup. Non-dashboard ingress
      populates this so ``conversation_get_or_create_by_thread`` (added in
      ``src/butlers/api/conversations.py``) can upsert one durable anchor
      per (butler_name, source_channel, source_thread_identity).

  provider_session_id          TEXT NULL
  provider_runtime_type        TEXT NULL
  provider_session_updated_at  TIMESTAMPTZ NULL
      Provider-side session/resume-handle ledger ("one memory per thread"):
      the most recent runtime-native session id a butler's runtime adapter
      minted for this conversation (e.g. the Claude CLI's stream-json
      session_id), which runtime adapter minted it, and when it was last
      refreshed. ``provider_runtime_type`` gates reuse -- a handle is only
      resumable by the adapter that minted it, and it expires by
      staleness (see ``resolve_resume_handle`` TTL check), never by a
      separate eviction job.

Indexes
-------
A partial unique index on (butler_name, source_channel,
source_thread_identity) WHERE source_thread_identity IS NOT NULL lets
concurrent-safe ``INSERT ... ON CONFLICT DO NOTHING`` upserts anchor exactly
one conversation row per thread across channels, without affecting existing
dashboard rows (which keep source_thread_identity NULL and are therefore
excluded from the partial index).

Backward-compatible, additive-only: existing rows backfill with
source_channel='dashboard' and NULL for every other new column; no downstream
FK or constraint depends on these columns.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_185"
down_revision = "core_184"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.dashboard_conversations
            ADD COLUMN IF NOT EXISTS source_channel TEXT NOT NULL DEFAULT 'dashboard',
            ADD COLUMN IF NOT EXISTS source_thread_identity TEXT NULL,
            ADD COLUMN IF NOT EXISTS provider_session_id TEXT NULL,
            ADD COLUMN IF NOT EXISTS provider_runtime_type TEXT NULL,
            ADD COLUMN IF NOT EXISTS provider_session_updated_at TIMESTAMPTZ NULL
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_dashboard_conversations_thread_anchor
            ON public.dashboard_conversations (butler_name, source_channel, source_thread_identity)
            WHERE source_thread_identity IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS public.uq_dashboard_conversations_thread_anchor",
    )
    op.execute(
        """
        ALTER TABLE public.dashboard_conversations
            DROP COLUMN IF EXISTS provider_session_updated_at,
            DROP COLUMN IF EXISTS provider_runtime_type,
            DROP COLUMN IF EXISTS provider_session_id,
            DROP COLUMN IF EXISTS source_thread_identity,
            DROP COLUMN IF EXISTS source_channel
        """
    )
