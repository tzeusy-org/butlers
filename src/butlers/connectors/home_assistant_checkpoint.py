"""Checkpoint persistence for the Home Assistant connector.

Per-instance checkpoint state is persisted to ``switchboard.connector_registry``
via :mod:`butlers.connectors.cursor_store`. The checkpoint is a JSON object::

    {
        "last_event_ts": <iso8601_str | null>,
        "last_entity_id": <str | null>,
        "transport": <"websocket" | "rest_fallback" | null>
    }

Checkpoint is keyed by ``(connector_type="home_assistant", endpoint_identity=<ha endpoint>)``.

Semantics
---------
- The checkpoint is updated after each event that is successfully submitted to
  the Switchboard (accepted or duplicate).
- On restart the checkpoint is loaded and a safety margin (``overlap_seconds``)
  is subtracted from ``last_event_ts`` to ensure events near the boundary are
  not missed due to clock skew or in-flight delivery gaps.
- Event deduplication skips events with ``time_fired`` at or before the
  adjusted checkpoint timestamp (``last_event_ts - overlap_seconds``).
  The Switchboard's own dedup layer handles any residual replays.
- If DB access fails during load, the connector starts fresh (fail-open).
  DB errors during save are logged and swallowed — the connector continues.

Design reference: openspec/changes/connector-home-assistant/design.md §D3
Tasks: openspec/changes/connector-home-assistant/tasks.md §7 (7.1–7.4)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from butlers.connectors.cursor_store import load_cursor, save_cursor

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_CONNECTOR_TYPE = "home_assistant"

TransportMode = Literal["websocket", "rest_fallback"]

# ---------------------------------------------------------------------------
# Checkpoint dataclass (task 7.1)
# ---------------------------------------------------------------------------


class HACheckpoint:
    """Per-instance checkpoint state for the Home Assistant connector.

    Attributes correspond 1-to-1 with the persisted JSON fields.
    """

    __slots__ = ("last_event_ts", "last_entity_id", "transport")

    def __init__(
        self,
        last_event_ts: datetime | None,
        last_entity_id: str | None,
        transport: TransportMode | None,
    ) -> None:
        self.last_event_ts = last_event_ts
        """ISO 8601 datetime of the most recently submitted HA event, or None."""

        self.last_entity_id = last_entity_id
        """HA entity_id of the most recently submitted event, or None."""

        self.transport = transport
        """Transport mode active when the checkpoint was last updated, or None."""

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialise checkpoint state to a JSON string for cursor_store."""
        data: dict[str, Any] = {
            "last_event_ts": (
                self.last_event_ts.isoformat() if self.last_event_ts is not None else None
            ),
            "last_entity_id": self.last_entity_id,
            "transport": self.transport,
        }
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str) -> HACheckpoint:
        """Deserialise a JSON string produced by :meth:`to_json`.

        Args:
            raw: JSON string as stored in ``checkpoint_cursor``.

        Returns:
            A populated :class:`HACheckpoint` instance.

        Raises:
            ValueError: If ``raw`` is not valid JSON or contains unexpected types.
        """
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"HA checkpoint is not valid JSON: {raw!r}") from exc

        if not isinstance(data, dict):
            raise ValueError(
                f"HA checkpoint JSON must be an object, got {type(data).__name__}: {raw!r}"
            )

        # Parse last_event_ts — stored as ISO 8601 string or null
        raw_ts = data.get("last_event_ts")
        last_event_ts: datetime | None = None
        if raw_ts is not None:
            if not isinstance(raw_ts, str):
                raise ValueError(
                    f"HA checkpoint 'last_event_ts' must be a string or null, "
                    f"got {type(raw_ts).__name__}: {raw_ts!r}"
                )
            try:
                last_event_ts = datetime.fromisoformat(raw_ts)
                # Ensure timezone-aware (assume UTC if naive)
                if last_event_ts.tzinfo is None:
                    last_event_ts = last_event_ts.replace(tzinfo=UTC)
            except ValueError as exc:
                raise ValueError(
                    f"HA checkpoint 'last_event_ts' is not a valid ISO 8601 datetime: {raw_ts!r}"
                ) from exc

        last_entity_id: str | None = data.get("last_entity_id")
        if last_entity_id is not None and not isinstance(last_entity_id, str):
            raise ValueError(
                f"HA checkpoint 'last_entity_id' must be a string or null, "
                f"got {type(last_entity_id).__name__}"
            )

        raw_transport = data.get("transport")
        transport: TransportMode | None = None
        if raw_transport is not None:
            if raw_transport not in ("websocket", "rest_fallback"):
                raise ValueError(
                    f"HA checkpoint 'transport' must be 'websocket', 'rest_fallback', or null, "
                    f"got {raw_transport!r}"
                )
            transport = raw_transport  # type: ignore[assignment]

        return cls(
            last_event_ts=last_event_ts,
            last_entity_id=last_entity_id,
            transport=transport,
        )

    @classmethod
    def empty(cls) -> HACheckpoint:
        """Return a checkpoint with all fields set to None (no prior state)."""
        return cls(last_event_ts=None, last_entity_id=None, transport=None)

    def advance(
        self,
        event_ts: datetime,
        entity_id: str,
        transport: TransportMode,
    ) -> None:
        """Update the checkpoint to the given event if it is strictly newer.

        Only advances the checkpoint if ``event_ts`` is strictly after
        ``last_event_ts``.  When timestamps are equal, the checkpoint is NOT
        advanced — the tie is broken by the Switchboard's dedup layer.

        Args:
            event_ts: The ``time_fired`` of the HA event that was successfully submitted.
            entity_id: The ``entity_id`` of the event.
            transport: The transport mode in use when the event was processed.
        """
        if self.last_event_ts is None or event_ts > self.last_event_ts:
            self.last_event_ts = event_ts
            self.last_entity_id = entity_id
            self.transport = transport


# ---------------------------------------------------------------------------
# Checkpoint loading with safety margin (task 7.2)
# ---------------------------------------------------------------------------


def adjusted_resume_ts(
    checkpoint: HACheckpoint,
    overlap_seconds: int,
) -> datetime | None:
    """Return the adjusted timestamp from which to resume event consumption.

    Subtracts ``overlap_seconds`` from the stored ``last_event_ts`` to account
    for clock skew and in-flight delivery gaps.  Events with ``time_fired``
    strictly after this value should be processed; events at or before it are
    treated as duplicates.

    Returns ``None`` when no prior checkpoint exists (connector starts from scratch).

    Args:
        checkpoint: The loaded checkpoint state.
        overlap_seconds: Safety margin in seconds to subtract from ``last_event_ts``.

    Returns:
        Adjusted ``datetime`` cutoff, or ``None`` if no prior checkpoint.
    """
    if checkpoint.last_event_ts is None:
        return None
    return checkpoint.last_event_ts - timedelta(seconds=overlap_seconds)


# ---------------------------------------------------------------------------
# Event deduplication (task 7.3)
# ---------------------------------------------------------------------------


def is_duplicate_event(
    resume_ts: datetime | None,
    event_ts: datetime,
) -> bool:
    """Return True if the event should be skipped as a duplicate.

    An event is a duplicate if its ``time_fired`` is at or before the adjusted
    resume timestamp computed by :func:`adjusted_resume_ts`.

    This is intentionally conservative: events exactly at the boundary are
    considered duplicates to avoid double-submission.  The Switchboard's own
    idempotency layer (advisory lock + dedup key) handles residual replays.

    Args:
        resume_ts: Adjusted resume cutoff from :func:`adjusted_resume_ts`.
            When ``None`` (no prior checkpoint), all events are new.
        event_ts: The ``time_fired`` of the incoming HA event.

    Returns:
        ``True`` if the event should be skipped; ``False`` if it should be processed.
    """
    if resume_ts is None:
        return False
    return event_ts <= resume_ts


# ---------------------------------------------------------------------------
# DB-backed load / save helpers (task 7.4)
# ---------------------------------------------------------------------------


async def load_ha_checkpoint(
    pool: asyncpg.Pool,
    endpoint_identity: str,
    overlap_seconds: int = 30,
) -> tuple[HACheckpoint, datetime | None]:
    """Load per-instance HA checkpoint state from ``switchboard.connector_registry``.

    Returns the raw checkpoint and the adjusted resume timestamp ready for
    deduplication.  Fail-open: any DB or parse error returns an empty checkpoint
    so the connector starts from scratch without crashing.

    Args:
        pool: asyncpg pool with access to the ``switchboard`` schema.
        endpoint_identity: Endpoint identity for this connector instance,
            e.g. ``"home_assistant:ha.local:8123"``.
        overlap_seconds: Safety margin subtracted from ``last_event_ts`` on
            resume (default 30, matches ``HA_CHECKPOINT_OVERLAP_S``).

    Returns:
        ``(checkpoint, resume_ts)`` where ``resume_ts`` is the adjusted cutoff
        for deduplication (``None`` when no prior checkpoint).
    """
    try:
        raw = await load_cursor(pool, _CONNECTOR_TYPE, endpoint_identity)
    except Exception:
        logger.exception(
            "ha-connector: failed to load checkpoint for endpoint=%s; starting fresh",
            endpoint_identity,
        )
        empty = HACheckpoint.empty()
        return empty, adjusted_resume_ts(empty, overlap_seconds)

    if raw is None:
        logger.info(
            "ha-connector: no checkpoint found for endpoint=%s; starting fresh",
            endpoint_identity,
        )
        empty = HACheckpoint.empty()
        return empty, adjusted_resume_ts(empty, overlap_seconds)

    try:
        checkpoint = HACheckpoint.from_json(raw)
    except ValueError:
        logger.exception(
            "ha-connector: malformed checkpoint for endpoint=%s; starting fresh",
            endpoint_identity,
        )
        empty = HACheckpoint.empty()
        return empty, adjusted_resume_ts(empty, overlap_seconds)

    resume_ts = adjusted_resume_ts(checkpoint, overlap_seconds)
    logger.info(
        "ha-connector: loaded checkpoint for endpoint=%s: "
        "last_event_ts=%s last_entity_id=%s transport=%s resume_ts=%s",
        endpoint_identity,
        checkpoint.last_event_ts,
        checkpoint.last_entity_id,
        checkpoint.transport,
        resume_ts,
    )
    return checkpoint, resume_ts


async def save_ha_checkpoint(
    pool: asyncpg.Pool,
    endpoint_identity: str,
    event_ts: datetime,
    entity_id: str,
    transport: TransportMode,
) -> None:
    """Persist per-instance HA checkpoint state to ``switchboard.connector_registry``.

    Called after each HA event is successfully submitted to the Switchboard
    (accepted or duplicate) to advance the checkpoint forward.

    DB errors are logged and swallowed — the connector continues operating
    without a persistent checkpoint rather than crashing.

    Args:
        pool: asyncpg pool with access to the ``switchboard`` schema.
        endpoint_identity: Endpoint identity for this connector instance.
        event_ts: The ``time_fired`` datetime of the submitted HA event.
            Must be timezone-aware (UTC).
        entity_id: The ``entity_id`` of the submitted event.
        transport: Transport mode active when the event was processed.
    """
    checkpoint = HACheckpoint(
        last_event_ts=event_ts,
        last_entity_id=entity_id,
        transport=transport,
    )
    try:
        await save_cursor(pool, _CONNECTOR_TYPE, endpoint_identity, checkpoint.to_json())
        logger.debug(
            "ha-connector: saved checkpoint for endpoint=%s: "
            "last_event_ts=%s entity_id=%s transport=%s",
            endpoint_identity,
            event_ts.isoformat(),
            entity_id,
            transport,
        )
    except Exception:
        logger.exception(
            "ha-connector: failed to save checkpoint for endpoint=%s", endpoint_identity
        )
