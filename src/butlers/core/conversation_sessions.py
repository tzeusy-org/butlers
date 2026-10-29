"""Provider resume handles for dashboard conversations.

The spawner stores the provider-native session id minted for a conversation's turn on
``public.dashboard_conversations`` and resumes from it on the next turn. These helpers
live in core because the spawner is their only runtime caller; the dashboard API does
not use them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

# Provider resume handles are considered fresh for this long after their last
# refresh. Chosen as a generous same-day window: long enough that a user
# picking a conversation back up later the same day still gets warm
# prompt-cache continuity, short enough that a handle from days ago (likely
# evicted provider-side already) is never worth an attempted resume. Pure
# staleness check -- there is no separate eviction job (bu-ep4ks.8 [decision]).
_PROVIDER_SESSION_TTL_SECONDS: int = 24 * 60 * 60


async def conversation_get_provider_session(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    butler_name: str,
) -> dict[str, Any] | None:
    """Fetch the current provider resume handle for a conversation, if any.

    Returns ``None`` when the conversation has never had a provider session
    recorded (including when the conversation itself does not exist) --
    callers treat that identically to "cold start", never as an error.
    """
    row = await pool.fetchrow(
        """
        SELECT provider_session_id, provider_runtime_type, provider_session_updated_at
        FROM public.dashboard_conversations
        WHERE id = $1 AND butler_name = $2
        """,
        conversation_id,
        butler_name,
    )
    if row is None or row["provider_session_id"] is None:
        return None
    return dict(row)


async def conversation_set_provider_session(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    butler_name: str,
    provider_session_id: str,
    provider_runtime_type: str,
) -> None:
    """Record the provider-native session id minted for a conversation's turn.

    Overwrites any prior handle -- "one memory per thread" means only the
    most recent provider session is ever worth resuming from.
    """
    await pool.execute(
        """
        UPDATE public.dashboard_conversations
        SET provider_session_id = $3,
            provider_runtime_type = $4,
            provider_session_updated_at = now()
        WHERE id = $1 AND butler_name = $2
        """,
        conversation_id,
        butler_name,
        provider_session_id,
        provider_runtime_type,
    )


async def conversation_clear_provider_session(
    pool: asyncpg.Pool,
    conversation_id: UUID,
    *,
    butler_name: str,
) -> None:
    """Evict a conversation's provider resume handle (fall-back-to-cold).

    Callers invoke this when a resume attempt fails (the provider rejects
    the handle as expired or unknown) so the *next* turn cold-starts cleanly
    instead of repeatedly retrying a dead handle.
    """
    await pool.execute(
        """
        UPDATE public.dashboard_conversations
        SET provider_session_id = NULL,
            provider_runtime_type = NULL,
            provider_session_updated_at = NULL
        WHERE id = $1 AND butler_name = $2
        """,
        conversation_id,
        butler_name,
    )


def resolve_resume_handle(
    provider_session: dict[str, Any] | None,
    *,
    runtime_type: str,
    ttl_seconds: int = _PROVIDER_SESSION_TTL_SECONDS,
    now: datetime | None = None,
) -> str | None:
    """Return a usable provider resume handle, or ``None`` if not applicable.

    Pure function (no DB access) so eviction/TTL logic is unit-testable
    without a pool. A handle is usable only when:

    - one is present at all,
    - it was minted by the *same* ``runtime_type`` the caller is about to
      invoke (resume tokens are provider-specific and never portable across
      adapters), and
    - its ``provider_session_updated_at`` is within ``ttl_seconds`` of `now`.

    Any other case (missing, runtime mismatch, expired) returns ``None`` --
    the caller's contract is to treat that as a transparent cold start, never
    an error.
    """
    if provider_session is None:
        return None
    handle = provider_session.get("provider_session_id")
    if not handle:
        return None
    if provider_session.get("provider_runtime_type") != runtime_type:
        return None
    updated_at = provider_session.get("provider_session_updated_at")
    if updated_at is None:
        return None
    effective_now = now if now is not None else datetime.now(UTC)
    if effective_now - updated_at > timedelta(seconds=ttl_seconds):
        return None
    return handle
