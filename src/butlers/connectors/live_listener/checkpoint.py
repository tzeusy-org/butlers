"""Checkpoint persistence for the live-listener voice connector.

Per-mic checkpoint state is persisted to ``switchboard.connector_registry``
via :mod:`butlers.connectors.cursor_store`. The checkpoint is a JSON object::

    {
        "last_utterance_ts": <unix_ms: int>,
        "session_id": <str | null>,
        "session_last_ts": <unix_ms: int | null>
    }

Checkpoint is keyed by ``(provider="live-listener", endpoint_identity=<mic endpoint>)``.

Semantics
---------
- The checkpoint is updated after each utterance that is successfully
  submitted to the Switchboard (accepted or duplicate).
- On restart the checkpoint is loaded to restore session continuity.
- The connector does NOT replay missed audio; the checkpoint is purely
  for session state recovery.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from butlers.connectors.cursor_store import load_cursor, save_cursor
from butlers.connectors.live_listener.envelope import endpoint_identity

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_CONNECTOR_TYPE = "live-listener"


class VoiceCheckpoint:
    """Per-mic checkpoint state container.

    Attributes correspond 1-to-1 with the persisted JSON fields.
    """

    __slots__ = ("last_utterance_ts", "session_id", "session_last_ts")

    def __init__(
        self,
        last_utterance_ts: int | None,
        session_id: str | None,
        session_last_ts: int | None,
    ) -> None:
        self.last_utterance_ts = last_utterance_ts
        """Unix ms of the most recently submitted utterance."""

        self.session_id = session_id
        """Active conversation session ID, or None."""

        self.session_last_ts = session_last_ts
        """Unix ms of the last utterance in the active session, or None."""

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialise checkpoint state to a JSON string for cursor_store."""
        data: dict[str, Any] = {
            "last_utterance_ts": self.last_utterance_ts,
            "session_id": self.session_id,
            "session_last_ts": self.session_last_ts,
        }
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str) -> VoiceCheckpoint:
        """Deserialise a JSON string produced by :meth:`to_json`.

        Args:
            raw: JSON string as stored in ``checkpoint_cursor``.

        Returns:
            A populated :class:`VoiceCheckpoint` instance.
        """
        data: dict[str, Any] = json.loads(raw)
        return cls(
            last_utterance_ts=data.get("last_utterance_ts"),
            session_id=data.get("session_id"),
            session_last_ts=data.get("session_last_ts"),
        )

    @classmethod
    def empty(cls) -> VoiceCheckpoint:
        """Return a checkpoint with all fields set to None (no prior state)."""
        return cls(last_utterance_ts=None, session_id=None, session_last_ts=None)


# ---------------------------------------------------------------------------
# DB-backed load / save helpers
# ---------------------------------------------------------------------------


async def load_voice_checkpoint(
    pool: asyncpg.Pool,
    device_name: str,
) -> VoiceCheckpoint:
    """Load per-mic checkpoint state from ``switchboard.connector_registry``.

    Returns :meth:`VoiceCheckpoint.empty` if no prior checkpoint exists or
    if DB access fails (fail-open: the connector starts fresh).

    Args:
        pool: asyncpg pool with access to the ``switchboard`` schema.
        device_name: Mic device name (used to derive endpoint identity).
    """
    ep_id = endpoint_identity(device_name)
    try:
        raw = await load_cursor(pool, _CONNECTOR_TYPE, ep_id)
    except Exception:
        logger.exception(
            "live-listener: failed to load checkpoint for mic=%s; starting fresh",
            device_name,
        )
        return VoiceCheckpoint.empty()

    if raw is None:
        logger.info(
            "live-listener: no checkpoint found for mic=%s; starting fresh",
            device_name,
        )
        return VoiceCheckpoint.empty()

    try:
        ckpt = VoiceCheckpoint.from_json(raw)
    except Exception:
        logger.exception(
            "live-listener: malformed checkpoint for mic=%s; starting fresh",
            device_name,
        )
        return VoiceCheckpoint.empty()

    logger.info(
        "live-listener: loaded checkpoint for mic=%s: last_ts=%s session_id=%s",
        device_name,
        ckpt.last_utterance_ts,
        ckpt.session_id,
    )
    return ckpt


async def save_voice_checkpoint(
    pool: asyncpg.Pool,
    device_name: str,
    last_utterance_ts: int,
    session_id: str | None,
    session_last_ts: int | None,
) -> None:
    """Persist per-mic checkpoint state to ``switchboard.connector_registry``.

    Called after each utterance is successfully submitted (accepted or
    duplicate) to advance the checkpoint forward.

    Args:
        pool: asyncpg pool with access to the ``switchboard`` schema.
        device_name: Mic device name (used to derive endpoint identity).
        last_utterance_ts: Unix ms of the submitted utterance.
        session_id: Active conversation session ID (or None).
        session_last_ts: Unix ms of the last utterance in the session (or None).
    """
    ep_id = endpoint_identity(device_name)
    ckpt = VoiceCheckpoint(
        last_utterance_ts=last_utterance_ts,
        session_id=session_id,
        session_last_ts=session_last_ts,
    )
    try:
        await save_cursor(pool, _CONNECTOR_TYPE, ep_id, ckpt.to_json())
        logger.debug(
            "live-listener: saved checkpoint for mic=%s: last_ts=%s session_id=%s",
            device_name,
            last_utterance_ts,
            session_id,
        )
    except Exception:
        logger.exception("live-listener: failed to save checkpoint for mic=%s", device_name)
