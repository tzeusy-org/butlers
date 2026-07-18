"""Deterministic owner notification for approval-gate park events.

This module deliberately contains no model invocation and never calls the
proactive-insight broker.  It reserves one durable control-plane emission for
each parked action, renders fixed dossier text, and routes the resulting
``approval_request`` envelope through the normal Switchboard → Messenger path.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from butlers.core.approval_callbacks import (
    APPROVAL_CALLBACK_SECRET_KEY,
    mint_approval_callback_token,
)
from butlers.core.approvals_policy import (
    approval_push_deliver_at,
    get_approvals_policy_quiet_hours,
)
from butlers.core.temporal.delivery_db import insert_deferred_notification

logger = logging.getLogger(__name__)

_BURST_WINDOW = timedelta(minutes=10)
_DASHBOARD_PORT_DEFAULT = "41200"

ApprovalPushMode = Literal["single", "burst_digest", "collapsed", "duplicate"]
ApprovalPushOutcome = Literal["delivered", "deferred", "collapsed", "duplicate", "failed"]


@dataclass(frozen=True, slots=True)
class ApprovalPushRuntime:
    """Live daemon dependencies for a deterministic approval push.

    The small injected boundary keeps the approvals module independent of the
    Switchboard client's concrete type and makes every delivery decision easy to
    exercise without an LLM runtime.
    """

    dispatch: Callable[[dict[str, Any]], Awaitable[None]]
    resolve_owner_recipient: Callable[[], Awaitable[str | None]]
    credential_store: Any | None
    dashboard_base_url: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalPushReservation:
    """A durable reservation result for one action's park event."""

    mode: ApprovalPushMode
    park_count: int
    pending_count: int


def _dashboard_base_url(override: str | None = None) -> str:
    """Return the dashboard origin used by owner-facing approval links."""
    value = override or os.environ.get(
        "DASHBOARD_URL",
        f"http://localhost:{os.environ.get('DASHBOARD_PORT', _DASHBOARD_PORT_DEFAULT)}",
    )
    return value.rstrip("/")


def _as_aware_datetime(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"approval push action {field_name} must be timezone-aware datetime")
    return value


def _action_id(action: Mapping[str, Any]) -> uuid.UUID:
    raw = action.get("id")
    try:
        return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("approval push action id must be a UUID") from exc


def _dossier_value(value: Any) -> str:
    if value is None:
        return "unspecified"
    normalized = str(value).strip()
    return normalized or "unspecified"


def approval_dashboard_url(
    action_id: uuid.UUID | str,
    *,
    dashboard_base_url: str | None = None,
) -> str:
    """Return the canonical action-detail deep link."""
    return f"{_dashboard_base_url(dashboard_base_url)}/approvals/{action_id}"


def format_approval_request_message(
    action: Mapping[str, Any],
    *,
    dashboard_base_url: str | None = None,
) -> str:
    """Render a fixed, reviewable dossier summary without LLM involvement."""
    action_id = _action_id(action)
    expires_at = _as_aware_datetime(action.get("expires_at"), field_name="expires_at")
    action_url = approval_dashboard_url(action_id, dashboard_base_url=dashboard_base_url)
    return "\n".join(
        (
            "Approval needed",
            f"Tool: {_dossier_value(action.get('tool_name'))}",
            f"Why: {_dossier_value(action.get('why'))}",
            f"Blast radius: {_dossier_value(action.get('blast_radius'))}",
            f"Reversibility: {_dossier_value(action.get('reversibility'))}",
            f"Expires: {expires_at.astimezone(UTC).isoformat()}",
            f"Review: {action_url}",
        )
    )


def build_approval_request_envelope(
    *,
    action: Mapping[str, Any],
    origin_butler: str,
    owner_recipient: str,
    callback_secret: str,
    dashboard_base_url: str | None = None,
) -> dict[str, Any]:
    """Build one owner-only ``approval_request`` envelope for a parked action."""
    action_id = _action_id(action)
    requested_at = _as_aware_datetime(action.get("requested_at"), field_name="requested_at")
    action_url = approval_dashboard_url(action_id, dashboard_base_url=dashboard_base_url)
    return {
        "schema_version": "notify.v1",
        "origin_butler": origin_butler,
        "delivery": {
            "intent": "approval_request",
            "channel": "telegram",
            "message": format_approval_request_message(
                action, dashboard_base_url=dashboard_base_url
            ),
            "recipient": owner_recipient,
        },
        "actions": [
            {
                "verb": "approve",
                "callback_token": mint_approval_callback_token(
                    action_id=action_id,
                    verb="a",
                    requested_at=requested_at,
                    secret=callback_secret,
                ),
                "dashboard_url": action_url,
            },
            {
                "verb": "reject",
                "callback_token": mint_approval_callback_token(
                    action_id=action_id,
                    verb="r",
                    requested_at=requested_at,
                    secret=callback_secret,
                ),
                "dashboard_url": action_url,
            },
            {"verb": "open_dashboard", "dashboard_url": action_url},
        ],
    }


def build_approval_digest_envelope(
    *,
    pending_count: int,
    origin_butler: str,
    owner_recipient: str,
    dashboard_base_url: str | None = None,
) -> dict[str, Any]:
    """Build the one non-interactive digest used after a park burst starts."""
    dashboard_url = _dashboard_base_url(dashboard_base_url) + "/approvals"
    return {
        "schema_version": "notify.v1",
        "origin_butler": origin_butler,
        "delivery": {
            "intent": "approval_request",
            "channel": "telegram",
            "message": f"{pending_count} actions awaiting review.\nReview: {dashboard_url}",
            "recipient": owner_recipient,
        },
        "actions": [{"verb": "open_dashboard", "dashboard_url": dashboard_url}],
    }


def select_approval_push_mode(*, park_count: int, digest_already_emitted: bool) -> ApprovalPushMode:
    """Select a deterministic park-notification mode for the current burst."""
    if park_count <= 3:
        return "single"
    if digest_already_emitted:
        return "collapsed"
    return "burst_digest"


async def reserve_approval_push(
    pool: Any,
    *,
    action_id: uuid.UUID,
    now: datetime,
) -> ApprovalPushReservation:
    """Reserve exactly one emission mode under a per-schema transaction lock."""
    window_start = now - _BURST_WINDOW
    async with pool.acquire() as connection:
        async with connection.transaction():
            # The tables are butler-schema scoped. Serializing within the active
            # schema prevents concurrent fourth parks from each emitting a digest.
            await connection.execute("SELECT pg_advisory_xact_lock(hashtext(current_schema()))")
            inserted = await connection.fetchval(
                """
                INSERT INTO approval_push_emissions (action_id, emission_kind, created_at)
                VALUES ($1, 'single', $2)
                ON CONFLICT (action_id) DO NOTHING
                RETURNING action_id
                """,
                action_id,
                now,
            )
            if inserted is None:
                return ApprovalPushReservation(mode="duplicate", park_count=0, pending_count=0)

            park_count = int(
                await connection.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM approval_push_emissions
                    WHERE created_at >= $1
                    """,
                    window_start,
                )
                or 0
            )
            pending_count = int(
                await connection.fetchval(
                    "SELECT COUNT(*) FROM pending_actions WHERE status = 'pending'"
                )
                or 0
            )
            digest_already_emitted = bool(
                await connection.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM approval_push_emissions
                        WHERE emission_kind = 'burst_digest' AND created_at >= $1
                    )
                    """,
                    window_start,
                )
            )
            mode = select_approval_push_mode(
                park_count=park_count,
                digest_already_emitted=digest_already_emitted,
            )
            if mode != "single":
                await connection.execute(
                    "UPDATE approval_push_emissions SET emission_kind = $1 WHERE action_id = $2",
                    mode,
                    action_id,
                )
            return ApprovalPushReservation(
                mode=mode,
                park_count=park_count,
                pending_count=pending_count,
            )


async def _callback_secret(runtime: ApprovalPushRuntime) -> str | None:
    if runtime.credential_store is None:
        return None
    return await runtime.credential_store.resolve(APPROVAL_CALLBACK_SECRET_KEY, env_fallback=False)


async def emit_approval_push(
    *,
    pool: Any,
    action: Mapping[str, Any],
    origin_butler: str,
    runtime: ApprovalPushRuntime,
    now: datetime | None = None,
) -> ApprovalPushOutcome:
    """Reserve and submit the deterministic push for a newly parked action.

    This is fail-open with respect to the action itself: an unavailable delivery
    plane must never un-park, approve, extend, or otherwise mutate the pending
    action.  The durable reservation is intentionally retained on an egress
    failure so retries/edits do not create owner-notification storms.
    """
    effective_now = now or datetime.now(UTC)
    if effective_now.tzinfo is None or effective_now.utcoffset() is None:
        raise ValueError("emit_approval_push requires a timezone-aware now value")

    try:
        reservation = await reserve_approval_push(
            pool,
            action_id=_action_id(action),
            now=effective_now,
        )
        if reservation.mode == "duplicate":
            return "duplicate"
        if reservation.mode == "collapsed":
            return "collapsed"

        owner_recipient = await runtime.resolve_owner_recipient()
        if not owner_recipient:
            logger.warning(
                "approval push skipped because no owner Telegram recipient is configured "
                "(action=%s butler=%s)",
                _action_id(action),
                origin_butler,
            )
            return "failed"

        if reservation.mode == "burst_digest":
            envelope = build_approval_digest_envelope(
                pending_count=reservation.pending_count,
                origin_butler=origin_butler,
                owner_recipient=owner_recipient,
                dashboard_base_url=runtime.dashboard_base_url,
            )
        else:
            callback_secret = await _callback_secret(runtime)
            if not callback_secret:
                logger.warning(
                    "approval push skipped because %s is unavailable (action=%s butler=%s)",
                    APPROVAL_CALLBACK_SECRET_KEY,
                    _action_id(action),
                    origin_butler,
                )
                return "failed"
            envelope = build_approval_request_envelope(
                action=action,
                origin_butler=origin_butler,
                owner_recipient=owner_recipient,
                callback_secret=callback_secret,
                dashboard_base_url=runtime.dashboard_base_url,
            )

        policy = await get_approvals_policy_quiet_hours(pool)
        deliver_at = approval_push_deliver_at(policy, now=effective_now)
        if deliver_at is not None:
            await insert_deferred_notification(
                pool,
                butler_name=origin_butler,
                channel="telegram",
                message=envelope["delivery"]["message"],
                priority="high",
                envelope=envelope,
                deliver_at=deliver_at,
                deferred_at=effective_now,
            )
            logger.info(
                "Deferred approval push (action=%s butler=%s deliver_at=%s mode=%s)",
                _action_id(action),
                origin_butler,
                deliver_at.isoformat(),
                reservation.mode,
            )
            return "deferred"

        await runtime.dispatch(envelope)
        logger.info(
            "Submitted approval push (action=%s butler=%s mode=%s)",
            _action_id(action),
            origin_butler,
            reservation.mode,
        )
        return "delivered"
    except Exception:  # noqa: BLE001 - never alter a parked action for push failure
        logger.warning(
            "approval push failed after action park (action=%s butler=%s)",
            action.get("id"),
            origin_butler,
            exc_info=True,
        )
        return "failed"


__all__ = [
    "ApprovalPushMode",
    "ApprovalPushOutcome",
    "ApprovalPushReservation",
    "ApprovalPushRuntime",
    "approval_dashboard_url",
    "build_approval_digest_envelope",
    "build_approval_request_envelope",
    "emit_approval_push",
    "format_approval_request_message",
    "reserve_approval_push",
    "select_approval_push_mode",
]
