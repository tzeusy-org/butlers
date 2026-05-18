"""Channel defaults API — per-channel default ingestion policy.

Provides:

- ``router`` — endpoints under ``/api/ingestion/channel-defaults``

Endpoints
---------
GET    /api/ingestion/channel-defaults/{channel}   — read (404 on missing)
PATCH  /api/ingestion/channel-defaults/{channel}   — upsert with schema validation
DELETE /api/ingestion/channel-defaults/{channel}   — HTTP 405 (no DELETE surface)

Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
      ingestion-ui-information-architecture/spec.md
      §"Channel defaults data model and REST API"
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.routers.audit import append as _audit_append

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingestion/channel-defaults", tags=["ingestion"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Per-channel schema validation
#
# Each channel's default_policy_json is an opaque document that is validated
# at PATCH time against a per-channel schema.  Unknown channels are rejected
# with HTTP 400 so that callers don't silently store un-validated blobs.
#
# Schema contract: a validator function receives the raw dict and returns
# None on success, or a string describing the validation error.
# ---------------------------------------------------------------------------

_REQUIRED_KEYS: dict[str, set[str]] = {
    "email": {"priority_action", "max_age_days"},
    "telegram": {"priority_action"},
    "telegram_bot": {"priority_action"},
    "telegram_user_client": {"priority_action"},
    "home-assistant": {"priority_action"},
    "home_assistant": {"priority_action"},
    "discord": {"priority_action"},
    "spotify": {"priority_action"},
    "owntracks": {"priority_action"},
    "whatsapp": {"priority_action"},
    "steam": {"priority_action"},
    "google_calendar": {"priority_action"},
    "google_drive": {"priority_action"},
    "google_health": {"priority_action"},
}

_VALID_PRIORITY_ACTIONS = frozenset(
    {"pass_through", "block", "skip", "metadata_only", "low_priority_queue"}
)


def _validate_policy_for_channel(channel: str, policy: dict[str, Any]) -> str | None:
    """Validate a policy document against the per-channel schema.

    Returns None on success, or a human-readable error string on failure.
    """
    required = _REQUIRED_KEYS.get(channel)
    if required is None:
        return f"Unknown channel {channel!r}. Known channels: {sorted(_REQUIRED_KEYS.keys())}"

    missing = required - set(policy.keys())
    if missing:
        return f"Missing required field(s) for channel {channel!r}: {sorted(missing)}"

    # Validate priority_action if present
    priority_action = policy.get("priority_action")
    if priority_action is not None and priority_action not in _VALID_PRIORITY_ACTIONS:
        return (
            f"Invalid priority_action {priority_action!r}. "
            f"Must be one of {sorted(_VALID_PRIORITY_ACTIONS)}"
        )

    # Channel-specific extra validation
    if channel == "email":
        max_age = policy.get("max_age_days")
        if max_age is not None and (not isinstance(max_age, int | float) or max_age <= 0):
            return "max_age_days must be a positive number"

    return None


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ChannelDefaultEntry(BaseModel):
    """A single channel_defaults row returned from the API."""

    channel: str
    default_policy_json: dict[str, Any]
    updated_at: datetime
    updated_by: str


class ChannelDefaultPatchRequest(BaseModel):
    """Request body for PATCH /api/ingestion/channel-defaults/{channel}."""

    default_policy_json: dict[str, Any]
    updated_by: str = "dashboard"


# ---------------------------------------------------------------------------
# GET /api/ingestion/channel-defaults/{channel}
# ---------------------------------------------------------------------------


@router.get("/{channel}", response_model=ChannelDefaultEntry)
async def get_channel_default(
    channel: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ChannelDefaultEntry:
    """Return the default policy document for a single channel.

    Returns HTTP 404 if no row exists for the channel.
    Returns HTTP 503 if the shared database pool is unavailable.
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    row = await pool.fetchrow(
        """
        SELECT channel, default_policy_json, updated_at, updated_by
        FROM public.channel_defaults
        WHERE channel = $1
        """,
        channel,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No channel defaults found for channel '{channel}'",
        )

    return ChannelDefaultEntry(
        channel=row["channel"],
        default_policy_json=row["default_policy_json"],
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
    )


# ---------------------------------------------------------------------------
# PATCH /api/ingestion/channel-defaults/{channel}
# ---------------------------------------------------------------------------


@router.patch("/{channel}", response_model=ChannelDefaultEntry)
async def upsert_channel_default(
    channel: str,
    body: ChannelDefaultPatchRequest,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ChannelDefaultEntry:
    """Upsert the default policy document for a channel.

    Validates the body against the per-channel schema — rejects with HTTP 400
    on validation failure.  Creates the row if missing; updates it otherwise.

    Emits an audit entry with action='ingestion.channel_default.update' on success.

    Returns HTTP 400 if validation fails or the channel is unknown.
    Returns HTTP 503 if the shared database pool is unavailable.
    """
    validation_error = _validate_policy_for_channel(channel, body.default_policy_json)
    if validation_error is not None:
        raise HTTPException(status_code=400, detail=validation_error)

    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=f"Shared database unavailable: {exc}") from exc

    now = datetime.now(tz=UTC)

    row = await pool.fetchrow(
        """
        INSERT INTO public.channel_defaults (channel, default_policy_json, updated_at, updated_by)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (channel) DO UPDATE
            SET default_policy_json = EXCLUDED.default_policy_json,
                updated_at          = EXCLUDED.updated_at,
                updated_by          = EXCLUDED.updated_by
        RETURNING channel, default_policy_json, updated_at, updated_by
        """,
        channel,
        body.default_policy_json,
        now,
        body.updated_by,
    )

    # Emit audit entry
    client_host = getattr(request.client, "host", None) if request.client else None
    try:
        await _audit_append(
            pool,
            actor=body.updated_by,
            action="ingestion.channel_default.update",
            target=channel,
            note=f"Updated channel defaults for channel '{channel}'",
            ip=client_host,
        )
    except Exception:
        logger.warning(
            "channel_defaults: failed to append audit_log entry for channel %s",
            channel,
            exc_info=True,
        )

    return ChannelDefaultEntry(
        channel=row["channel"],
        default_policy_json=row["default_policy_json"],
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
    )


# ---------------------------------------------------------------------------
# DELETE /api/ingestion/channel-defaults/{channel} — HTTP 405 (no DELETE surface)
# ---------------------------------------------------------------------------


@router.delete("/{channel}", status_code=405)
async def delete_channel_default_not_allowed(channel: str) -> Response:
    """No DELETE surface — channel defaults are never deleted, only overwritten.

    Per spec §"Channel defaults data model and REST API":
    'There SHALL be no DELETE endpoint exposed.'

    Returns HTTP 405 Method Not Allowed.
    """
    raise HTTPException(
        status_code=405,
        detail=(
            "Deleting channel defaults is not supported. "
            "Use PATCH to overwrite the policy document."
        ),
    )
