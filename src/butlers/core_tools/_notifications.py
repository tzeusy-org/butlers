"""Notifications core tools: remind and notify (group: notifications).

notify is only registered for non-STAFFER butlers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import Field

from butlers.config import ButlerType
from butlers.core.permissions import NOTIFY_PERMISSION, check_permission
from butlers.core.scheduler import schedule_create as _schedule_create
from butlers.core.telemetry import tool_span
from butlers.core.tool_call_capture import get_current_runtime_session_id
from butlers.core_tools._base import NotifyRequestContextInput, ToolContext

logger = logging.getLogger(__name__)

_NO_TELEGRAM_CHAT_CONFIGURED_ERROR = (
    "No bot <-> user telegram chat has been configured - please add a "
    "telegram_chat_id entity_info entry on the owner entity via the dashboard"
)

_REQUEST_CONTEXT_KEYS_HINT = (
    "Pass request_context as a JSON object (not a string) with keys "
    "request_id, source_channel, source_endpoint_identity, "
    "source_sender_identity (plus source_thread_identity for telegram "
    "reply/react)."
)


def _coerce_request_context(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize a request_context argument into a dict.

    Models (especially non-Claude runtimes) sometimes pass request_context as a
    JSON-encoded *string* rather than an object. The dict-only schema would
    otherwise reject the call at the MCP boundary with an opaque type error the
    model cannot recover from, silently dropping the reply. Accept the string
    here and parse it instead.

    Returns ``(context_dict_or_none, error_message_or_none)``. When the error
    element is non-None the caller should return it as an actionable
    ``{"status": "error", ...}`` so the model can correct the shape.
    """
    if value is None or isinstance(value, dict):
        return value, None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, None
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None, (
                "request_context must be an object/dict, but a string that is "
                f"not valid JSON was received. {_REQUEST_CONTEXT_KEYS_HINT}"
            )
        if isinstance(parsed, dict):
            return parsed, None
        return None, (
            "request_context must be an object/dict, but a JSON "
            f"{type(parsed).__name__} was received. {_REQUEST_CONTEXT_KEYS_HINT}"
        )
    return None, (
        f"request_context must be an object/dict, got {type(value).__name__}. "
        f"{_REQUEST_CONTEXT_KEYS_HINT}"
    )


def register_notification_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register notifications group tools: remind and notify."""
    daemon = ctx.daemon
    pool = ctx.pool
    butler_name = ctx.butler_name
    butler_type = ctx.butler_type

    @_core_tool("notifications")
    async def remind(
        message: Annotated[
            str,
            Field(description="The reminder message to deliver."),
        ],
        channel: Annotated[
            Literal["telegram", "email"],
            Field(description="Delivery channel for the reminder."),
        ],
        delay_minutes: Annotated[
            int | None,
            Field(
                description=(
                    "Minutes from now to deliver the reminder. "
                    "Only for reminders relative to the current moment "
                    "(e.g. 'remind me in 30 minutes'). "
                    "Do NOT use for event-based reminders — use remind_at instead. "
                    "Mutually exclusive with remind_at."
                )
            ),
        ] = None,
        remind_at: Annotated[
            datetime | None,
            Field(
                description=(
                    "Absolute UTC datetime to deliver the reminder. "
                    "PREFERRED for event-based reminders: compute the target time "
                    "from the event's start time (e.g. event at 2026-03-20T06:00Z "
                    "minus 1 hour = remind_at 2026-03-20T05:00Z). "
                    "Mutually exclusive with delay_minutes."
                )
            ),
        ] = None,
        request_context: Annotated[
            NotifyRequestContextInput | str | None,
            Field(
                description=(
                    "Optional request context passed through to notify(). "
                    "Pass a dict/object (a JSON-string is tolerated and parsed)."
                )
            ),
        ] = None,
    ) -> dict:
        """Set a one-shot reminder that delivers a message via notify().

        Exactly one of ``delay_minutes`` or ``remind_at`` must be provided.

        IMPORTANT: When setting a reminder for a known future event (interview,
        flight, meeting, etc.), ALWAYS use ``remind_at`` with an absolute UTC
        time computed from the event's start time. For example, to remind 1 hour
        before an event at 2026-03-20T14:00+08:00, use
        remind_at=2026-03-20T05:00:00+00:00. Never use ``delay_minutes`` for
        event-based reminders — it sets the reminder relative to *now*, not
        relative to the event.
        """
        # --- normalize request_context (tolerate a stringified JSON object) ---
        request_context, _rc_err = _coerce_request_context(request_context)
        if _rc_err is not None:
            return {"status": "error", "error": _rc_err}

        # --- validate inputs ---
        if delay_minutes is not None and remind_at is not None:
            return {
                "status": "error",
                "error": ("Provide exactly one of delay_minutes or remind_at, not both."),
            }
        if delay_minutes is None and remind_at is None:
            return {
                "status": "error",
                "error": ("Provide exactly one of delay_minutes or remind_at."),
            }
        if delay_minutes is not None and delay_minutes < 1:
            return {
                "status": "error",
                "error": "delay_minutes must be at least 1.",
            }

        # --- compute target time ---
        now = datetime.now(UTC)
        if delay_minutes is not None:
            target = now + timedelta(minutes=delay_minutes)
        else:
            if remind_at is None:
                return {"status": "error", "error": "Internal error: remind_at is None."}
            # Ensure remind_at is timezone-aware (assume UTC if naive)
            if remind_at.tzinfo is None:
                target = remind_at.replace(tzinfo=UTC)
            else:
                target = remind_at
            if target <= now:
                return {
                    "status": "error",
                    "error": "remind_at must be in the future.",
                }

        # --- build cron expression for the target minute ---
        cron = f"{target.minute} {target.hour} {target.day} {target.month} *"

        # --- build prompt that calls notify() ---
        notify_args: dict[str, Any] = {
            "channel": channel,
            "message": message,
            "intent": "send",
        }
        if request_context is not None:
            notify_args["request_context"] = request_context

        prompt = (
            f"Deliver this reminder by calling the notify tool with "
            f"the following arguments: {json.dumps(notify_args)}"
        )

        # --- schedule a one-shot task ---
        # No stagger_key: stagger is designed for recurring tasks to spread load
        # across butlers.  One-shot reminders must fire as close to the target
        # minute as possible — adding stagger can push next_run_at past the next
        # tick boundary and delay delivery by a full extra tick interval.
        until_at = target + timedelta(minutes=1)
        task_id = await _schedule_create(
            pool,
            f"remind-{target.strftime('%Y%m%dT%H%M')}-{str(uuid.uuid4())[:8]}",
            cron,
            prompt,
            until_at=until_at,
        )

        return {
            "id": str(task_id),
            "status": "scheduled",
            "remind_at": target.isoformat(),
            "channel": channel,
            "message": message,
        }

    # notify is non-STAFFER only
    if butler_type != ButlerType.STAFFER:

        @_core_tool("notifications")
        @tool_span("notify", butler_name=butler_name)
        async def notify(
            channel: Annotated[
                Literal["telegram", "email", "whatsapp"] | None,
                Field(
                    description=(
                        "Delivery channel. Allowed values: telegram | email | whatsapp. "
                        "Optional: when omitted together with an entity_id, the channel is "
                        "resolved from the entity's preferred channel (falling back to "
                        "telegram, then email). When omitted without an entity_id, delivery "
                        "defaults to telegram."
                    )
                ),
            ] = None,
            message: Annotated[
                str | None,
                Field(description="Message text. Required for send/reply intents."),
            ] = None,
            recipient: Annotated[
                str | None,
                Field(description="Optional explicit recipient identity (for example email)."),
            ] = None,
            subject: Annotated[
                str | None,
                Field(description="Optional subject line (email channel)."),
            ] = None,
            intent: Annotated[
                Literal["send", "reply", "react", "insight"],
                Field(
                    description=("Delivery intent. Allowed values: send | reply | react | insight.")
                ),
            ] = "send",
            emoji: Annotated[
                str | None,
                Field(description="Required when intent=react."),
            ] = None,
            request_context: Annotated[
                NotifyRequestContextInput | str | None,
                Field(
                    description=(
                        "Context lineage for reply/react targeting. Pass a "
                        "dict/object (a JSON-string is tolerated and parsed, but "
                        "an object is preferred). Required keys "
                        "for reply/react: request_id, source_channel, "
                        "source_endpoint_identity, source_sender_identity. For "
                        "telegram reply/react include source_thread_identity. "
                        "Do not pass placeholder strings such as "
                        '"<the REQUEST CONTEXT object...>".'
                    )
                ),
            ] = None,
            entity_id: Annotated[
                uuid.UUID | None,
                Field(
                    description=(
                        "Optional entity UUID (public.entities.id). When provided, the channel"
                        " identifier is resolved "
                        "from relationship.entity_facts (active triple preferred). If no matching "
                        "entity_facts triple exists, the notification is parked as a "
                        "pending_action and {status: pending_missing_identifier} is returned."
                    )
                ),
            ] = None,
            priority: Annotated[
                Literal["high", "medium", "low"],
                Field(
                    description=(
                        "Notification priority for quiet-hours enforcement. "
                        "Allowed values: high | medium | low. Default: medium. "
                        "high — always delivers immediately (bypasses quiet hours). "
                        "medium — deferred during quiet hours. "
                        "low — deferred during quiet hours."
                    )
                ),
            ] = "medium",
            msg_context: Annotated[
                Literal["personal", "work", "other"] | None,
                Field(
                    description=(
                        "Optional message context sphere. Allowed values: personal | work | other. "
                        "When provided with entity_id, recipient resolution prefers "
                        "contact_info entries tagged with matching context. "
                        "When the resolved address context conflicts with msg_context, "
                        "delivery is parked for approval. "
                        "Defaults to None (no context preference)."
                    )
                ),
            ] = None,
        ) -> dict:
            """Send a `notify.v1` envelope through Switchboard `deliver()`.

            Required fields:
            - `channel` (string enum): `telegram`, `email`, or `whatsapp`
            - `message` (string): required for `send`/`reply`, omitted for `react`

            Optional fields:
            - `recipient` (string): explicit recipient identity (e.g. email address or chat ID)
            - `entity_id` (UUID): resolve recipient from relationship.entity_facts (active
              triple preferred) keyed on this entity. If no matching triple exists the
              notification is parked as a pending_action and
              `{"status": "pending_missing_identifier"}` is returned.
            - `subject` (string)
            - `intent` (string enum): `send` | `reply` | `react` | `insight`
            - `emoji` (string): required when `intent="react"`
            - `request_context` (dict, NOT a JSON string): required for `reply`/`react` and must
              include `request_id`, `source_channel`, `source_endpoint_identity`,
              `source_sender_identity` plus `source_thread_identity` for
              telegram `reply`/`react`.
              Pass an object value, not a quoted placeholder string.

            Recipient resolution priority:
            1. `entity_id` provided → look up channel identifier from relationship.entity_facts
               keyed on the entity; msg_context is not used for ordering (entity_facts
               has no context column) but is still applied by the email guard for validation
            2. `recipient` string provided → use as-is
            3. Neither → resolve owner entity's channel identifier (default)

            Context mismatch: if `msg_context` is provided and the resolved address is
            tagged with a conflicting context (e.g. sending a "personal" message to a
            "work" email), delivery is parked for approval.

            Valid JSON example:
            {
              "channel": "telegram",
              "intent": "reply",
              "message": "Done. I logged it.",
              "request_context": {
                "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
                "source_channel": "telegram_bot",
                "source_endpoint_identity": "switchboard",
                "source_sender_identity": "health",
                "source_thread_identity": "12345"
              }
            }
            """
            # --- Normalize request_context (tolerate a stringified JSON object) ---
            # A model may pass request_context as a JSON string; coerce it to a
            # dict here so reply/react targeting still works instead of failing
            # at the schema boundary with an unrecoverable type error.
            request_context, _rc_err = _coerce_request_context(request_context)
            if _rc_err is not None:
                return {"status": "error", "error": _rc_err}

            # --- Permissions-matrix enforcement (public.permissions: notify) ---
            # The Settings → Permissions matrix governs whether this butler may
            # send owner-facing notifications. A cell flipped to granted=false
            # blocks notify() outright (an authorization decision). Mirrors the
            # spawn gate: consult the matrix at the decision point, return an
            # observable denial. check_permission fails open, so a DB error never
            # wedges delivery.
            _perm_pool = daemon.db.pool if daemon.db is not None else None
            _notify_perm = await check_permission(_perm_pool, butler_name, NOTIFY_PERMISSION)
            if not _notify_perm.allowed:
                _perm_msg = (
                    f"Permission denied: butler '{butler_name}' is not granted "
                    f"'{NOTIFY_PERMISSION}'"
                )
                if _notify_perm.reason:
                    _perm_msg += f" (reason: {_notify_perm.reason})"
                logger.warning(
                    "notify() blocked by permissions matrix for butler=%s: %s",
                    butler_name,
                    _perm_msg,
                )
                return {"status": "error", "error": _perm_msg}

            # --- Channel resolution (entity-keyed-preferred-channel, group 2) ---
            # `channel` is optional. A forced channel always wins. When the caller
            # leaves it unspecified, resolve the outbound channel:
            #   - entity-targeted → honour the entity's `prefers-channel`
            #     fact when deliverable, else fall back to telegram → email;
            #   - no entity_id → default to telegram (the historical owner-page
            #     channel), preserving prior behaviour for callers that relied on
            #     a channel always being present.
            # The forced channel is never overridden, so preference is consulted
            # only here, before any channel-dependent validation runs.
            if channel is None:
                resolved_channel: str | None = None
                if entity_id is not None:
                    _resolve_pool = daemon.db.pool if daemon.db is not None else None
                    if _resolve_pool is not None:
                        from butlers.identity import resolve_outbound_channel

                        resolved_channel = await resolve_outbound_channel(
                            _resolve_pool,
                            entity_id,
                            deliverable_channels={"telegram", "email"},
                        )
                channel = resolved_channel or "telegram"

            # Validate message is present (not required for react intent)
            if intent != "react" and message is None:
                logger.error(
                    "notify() called without required 'message' parameter: "
                    "channel=%r, intent=%r, emoji=%r, request_context=%r",
                    channel,
                    intent,
                    emoji,
                    request_context,
                )
                return {
                    "status": "error",
                    "error": (
                        "Missing required 'message' parameter. "
                        "notify() requires: channel, message, request_context."
                    ),
                }

            # Validate message is not empty/whitespace (not required for react intent)
            if intent != "react" and (not message or not message.strip()):
                return {
                    "status": "error",
                    "error": "Message must not be empty or whitespace-only.",
                }

            _SUPPORTED_CHANNELS = {"telegram", "email"}
            if channel not in _SUPPORTED_CHANNELS:
                return {
                    "status": "error",
                    "error": (
                        f"Unsupported channel '{channel}'. "
                        f"Supported channels: {', '.join(sorted(_SUPPORTED_CHANNELS))}"
                    ),
                }

            if intent not in {"send", "reply", "react", "insight"}:
                return {
                    "status": "error",
                    "error": "Unsupported notify intent. Supported intents: send, reply, react, insight",  # noqa: E501
                }

            # React intent validation
            if intent == "react":
                if not emoji:
                    return {
                        "status": "error",
                        "error": "React intent requires emoji parameter.",
                    }
                if channel not in {"telegram"}:
                    return {
                        "status": "error",
                        "error": (
                            f"React intent is not supported for channel '{channel}'. "
                            "Only telegram supports reactions."
                        ),
                    }
                if not request_context or not request_context.get("source_thread_identity"):
                    return {
                        "status": "error",
                        "error": (
                            "React intent requires request_context with source_thread_identity."
                        ),
                    }

            # Priority validation
            from butlers.core.temporal.delivery_db import _VALID_PRIORITIES as _VP

            if priority not in _VP:
                return {
                    "status": "error",
                    "error": (
                        f"Invalid priority {priority!r}. Allowed values: {', '.join(sorted(_VP))}"
                    ),
                }

            # Quiet-hours gate: check delivery preferences and defer if needed
            _notify_pool = daemon.db.pool if daemon.db is not None else None
            if _notify_pool is not None and intent in {"send", "insight"}:
                from datetime import UTC as _UTC
                from datetime import datetime as _datetime
                from zoneinfo import ZoneInfo as _ZoneInfo

                from butlers.core.temporal.delivery import (
                    compute_deliver_at,
                    should_defer_notification,
                )
                from butlers.core.temporal.delivery_db import (
                    get_delivery_preferences,
                    insert_deferred_notification,
                )

                try:
                    _prefs = await get_delivery_preferences(_notify_pool, butler_name)
                except Exception:
                    # Table may not exist yet or pool unavailable; deliver immediately
                    logger.exception(
                        "notify() failed to fetch delivery preferences; delivering immediately"
                    )
                    _prefs = None
                if _prefs is not None:
                    _tz_name = _prefs.get("timezone", "UTC")
                    try:
                        _tz = _ZoneInfo(_tz_name)
                    except Exception:
                        _tz = _ZoneInfo("UTC")
                    _now_utc = _datetime.now(_UTC)
                    _now_local = _now_utc.astimezone(_tz).time()

                    if should_defer_notification(
                        priority=priority,
                        current_time=_now_local,
                        prefs=_prefs,
                        channel=channel,
                    ):
                        # Build notify.v1 envelope to persist
                        _envelope: dict[str, Any] = {
                            "schema_version": "notify.v1",
                            "origin_butler": butler_name,
                            "delivery": {
                                "intent": intent,
                                "channel": channel,
                                "message": message or "",
                            },
                        }
                        if subject is not None:
                            _envelope["delivery"]["subject"] = subject
                        if recipient is not None:
                            _envelope["delivery"]["recipient"] = recipient
                        if request_context is not None:
                            _envelope["request_context"] = request_context

                        _deliver_at = compute_deliver_at(prefs=_prefs, now=_now_utc)
                        try:
                            _notif_id = await insert_deferred_notification(
                                _notify_pool,
                                butler_name=butler_name,
                                channel=channel,
                                message=message or "",
                                priority=priority,
                                envelope=_envelope,
                                deliver_at=_deliver_at,
                                deferred_at=_now_utc,
                            )
                            logger.info(
                                "notify() deferred notification %s (priority=%s) to %s",
                                _notif_id,
                                priority,
                                _deliver_at.isoformat(),
                            )
                            return {
                                "status": "deferred",
                                "notification_id": _notif_id,
                                "deliver_at": _deliver_at.isoformat(),
                                "channel": channel,
                                "priority": priority,
                            }
                        except Exception:
                            # If we can't persist, fall through to immediate delivery
                            logger.exception(
                                "notify() failed to defer notification; delivering immediately"
                            )

            # Approvals-policy quiet-hours gate: suppress owner-default pages.
            # Applies only when no explicit entity_id or recipient is given
            # (i.e. the notification is destined for the owner via the default
            # resolution path), the intent is send/insight, and priority is not
            # high (high-priority always delivers immediately, per §8.6 spec).
            if (
                _notify_pool is not None
                and entity_id is None
                and recipient is None
                and intent in {"send", "insight"}
                and priority != "high"
            ):
                from datetime import UTC as _PUTC
                from datetime import datetime as _pdatetime
                from zoneinfo import ZoneInfo as _PZoneInfo

                from butlers.core.approvals_policy import (
                    get_approvals_policy_quiet_hours,
                    should_suppress_by_policy,
                )

                try:
                    _policy = await get_approvals_policy_quiet_hours(_notify_pool)
                except Exception:
                    logger.debug(
                        "notify() failed to fetch approvals_policy; delivering immediately",
                        exc_info=True,
                    )
                    _policy = None

                if _policy is not None:
                    _policy_tz_name = _policy.get("timezone", "UTC")
                    try:
                        _policy_tz = _PZoneInfo(_policy_tz_name)
                    except Exception:
                        _policy_tz = _PZoneInfo("UTC")
                    _policy_now_local = _pdatetime.now(_PUTC).astimezone(_policy_tz)
                    _policy_current_hour = _policy_now_local.hour

                    if should_suppress_by_policy(_policy, current_hour=_policy_current_hour):
                        logger.info(
                            "notify() suppressed owner page during quiet hours "
                            "(policy tz=%s hour=%d quiet=%s-%s channel=%s butler=%s)",
                            _policy_tz_name,
                            _policy_current_hour,
                            _policy.get("quiet_start_hour"),
                            _policy.get("quiet_end_hour"),
                            channel,
                            butler_name,
                        )
                        return {
                            "status": "suppressed_quiet_hours",
                            "channel": channel,
                            "quiet_start_hour": _policy.get("quiet_start_hour"),
                            "quiet_end_hour": _policy.get("quiet_end_hour"),
                            "timezone": _policy_tz_name,
                        }

            client = daemon.switchboard_client
            if client is None and butler_name != "switchboard":
                return {
                    "status": "error",
                    "error": (
                        "Switchboard is not connected. Cannot deliver notification. "
                        "The Switchboard butler may not be running — this is a transient "
                        "infrastructure issue, not a parameter error. Retry after a delay "
                        "or check butler status."
                    ),
                    "retryable": True,
                }

            # Resolution priority:
            # (1) entity_id → query relationship.entity_facts keyed on the entity;
            #     msg_context is not used for ordering (entity_facts has no context column)
            # (2) recipient string → use as-is (inside _resolve_default_notify_recipient)
            # (3) neither → resolve owner entity's channel identifier (default path)
            if entity_id is not None:
                entity_identifier = await daemon._resolve_entity_channel_identifier(
                    entity_id=entity_id,
                    channel=channel,
                    msg_context=msg_context,
                )
                if entity_identifier is None:
                    # No matching entity_facts triple — park as pending_action and notify owner
                    action_id: uuid.UUID | None = None
                    pool = daemon.db.pool if daemon.db is not None else None
                    if pool is not None:
                        import datetime as _dt

                        action_id = uuid.uuid4()
                        now = _dt.datetime.now(_dt.UTC)
                        expires_at = now + _dt.timedelta(hours=72)
                        info_type = daemon._CHANNEL_TO_CONTACT_INFO_TYPE.get(channel, channel)
                        agent_summary = (
                            f"notify() could not deliver a {channel!r} notification: "
                            f"entity {entity_id} has no {info_type!r} identifier in "
                            f"relationship.entity_facts. The message was: {message!r}. "
                            f"To resolve, assert a channel triple for this entity in the "
                            f"entity graph and re-trigger the notification."
                        )
                        await pool.execute(
                            "INSERT INTO pending_actions "
                            "(id, tool_name, tool_args, agent_summary, session_id, status, "
                            "requested_at, expires_at) "
                            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                            action_id,
                            "notify",
                            json.dumps(
                                {
                                    "channel": channel,
                                    "message": message,
                                    "entity_id": str(entity_id),
                                    "intent": intent,
                                }
                            ),
                            agent_summary,
                            get_current_runtime_session_id(),
                            "pending",  # ActionStatus.PENDING — literal avoids approvals import
                            now,
                            expires_at,
                        )
                        logger.warning(
                            "notify() parked as pending_missing_identifier: "
                            "entity_id=%s has no %r entity_facts triple (action=%s)",
                            entity_id,
                            info_type,
                            action_id,
                        )
                    # Notify the owner about the missing identifier.
                    # Note: _resolve_default_notify_recipient only handles telegram+send;
                    # owner_identifier will be None for non-telegram channels.
                    owner_identifier = await daemon._resolve_default_notify_recipient(
                        channel=channel,
                        intent="send",
                        recipient=None,
                    )
                    if owner_identifier is not None:
                        owner_notify_request: dict[str, Any] = {
                            "schema_version": "notify.v1",
                            "origin_butler": butler_name,
                            "delivery": {
                                "intent": "send",
                                "channel": channel,
                                "message": (
                                    f"A notification could not be delivered to entity "
                                    f"{entity_id} via {channel!r}: missing {info_type!r} "
                                    f"channel identifier. The pending action has been queued "
                                    f"for review."
                                ),
                                "recipient": owner_identifier,
                            },
                        }
                        try:
                            if client is not None:
                                await asyncio.wait_for(
                                    client.call_tool(
                                        "deliver",
                                        {
                                            "source_butler": butler_name,
                                            "notify_request": owner_notify_request,
                                        },
                                    ),
                                    timeout=15,
                                )
                            elif butler_name == "switchboard":
                                _owner_pool = daemon.db.pool if daemon.db is not None else None
                                if _owner_pool is not None:
                                    from butlers.tools.switchboard.notification.deliver import (
                                        deliver as _sw_deliver,
                                    )

                                    await _sw_deliver(
                                        _owner_pool,
                                        source_butler=butler_name,
                                        notify_request=owner_notify_request,
                                    )
                        except Exception as _owner_exc:  # noqa: BLE001
                            logger.warning(
                                "notify() failed to alert owner about missing identifier: %s",
                                _owner_exc,
                            )
                    return {
                        "status": "pending_missing_identifier",
                        "entity_id": str(entity_id),
                        "channel": channel,
                        "pending_action_id": str(action_id) if action_id is not None else None,
                    }
                resolved_recipient = entity_identifier
            else:
                resolved_recipient = await daemon._resolve_default_notify_recipient(
                    channel=channel,
                    intent=intent,
                    recipient=recipient,
                    request_context=request_context,
                )

            if (
                channel == "telegram"
                and intent in {"send", "insight"}
                and resolved_recipient is None
            ):
                return {
                    "status": "error",
                    "error": _NO_TELEGRAM_CHAT_CONFIGURED_ERROR,
                }

            # Validate email recipients against known contacts.
            # This prevents LLM-hallucinated addresses from reaching delivery.
            # NOTE: runs regardless of whether entity_id was used for resolution.
            # The entity_id path resolves to an email address but does NOT verify
            # that the address belongs to a known, non-temporary contact.
            if channel == "email" and resolved_recipient is not None:
                pool = daemon.db.pool if daemon.db is not None else None
                if pool is not None:
                    from butlers.core.approvals_hooks import (
                        check_email_recipient,
                    )

                    _notify_args = {
                        "channel": channel,
                        "message": message,
                        "recipient": resolved_recipient,
                        "intent": intent,
                    }
                    _decision = await check_email_recipient(
                        pool,
                        email_target=resolved_recipient,
                        rule_tool_name="notify",
                        rule_match_args=_notify_args,
                        park_tool_name="notify",
                        park_tool_args=_notify_args,
                        park_summary=(
                            f"notify() rejected: email to "
                            f"{resolved_recipient!r}. Message: {message!r}"
                        ),
                        session_id=get_current_runtime_session_id(),
                        msg_context=msg_context,
                    )
                    if not _decision.allowed:
                        return {
                            "status": "pending_approval",
                            "error": (
                                f"Delivery blocked: email target "
                                f"'{resolved_recipient}' is a "
                                f"{_decision.contact_desc} "
                                f"and no standing approval rule matches. "
                                f"Create a standing rule or approve via the "
                                f"approval dashboard."
                            ),
                            "pending_action_id": str(_decision.action_id),
                        }

            # Channel-general role-based approval gating for non-email channels
            # (telegram, and any future channel).  Owner-directed sends auto-approve
            # on any active verified owner channel; non-owner recipients require a
            # standing rule or are parked (fail-closed).  Email is gated above by
            # check_email_recipient, which additionally enforces the email-only
            # channel-primacy / context-conflict incident behaviour.
            if (
                channel != "email"
                and resolved_recipient is not None
                and intent in {"send", "insight"}
            ):
                pool = daemon.db.pool if daemon.db is not None else None
                if pool is not None:
                    from butlers.core.approvals_hooks import check_recipient

                    _notify_args = {
                        "channel": channel,
                        "message": message,
                        "recipient": resolved_recipient,
                        "intent": intent,
                    }
                    _decision = await check_recipient(
                        pool,
                        channel=channel,
                        target=resolved_recipient,
                        rule_tool_name="notify",
                        rule_match_args=_notify_args,
                        park_tool_name="notify",
                        park_tool_args=_notify_args,
                        park_summary=(
                            f"notify() rejected: {channel} message to "
                            f"{resolved_recipient!r}. Message: {message!r}"
                        ),
                        session_id=get_current_runtime_session_id(),
                        butler_name=butler_name,
                    )
                    if not _decision.allowed:
                        return {
                            "status": "pending_approval",
                            "error": (
                                f"Delivery blocked: {channel} target "
                                f"'{resolved_recipient}' is a "
                                f"{_decision.contact_desc} "
                                f"and no standing approval rule matches. "
                                f"Create a standing rule or approve via the "
                                f"approval dashboard."
                            ),
                            "pending_action_id": str(_decision.action_id),
                        }

            delivery_message = message if message is not None else ""
            notify_request: dict[str, Any] = {
                "schema_version": "notify.v1",
                "origin_butler": butler_name,
                "delivery": {
                    "intent": intent,
                    "channel": channel,
                    "message": delivery_message,
                },
            }
            if emoji is not None:
                notify_request["delivery"]["emoji"] = emoji
            if resolved_recipient is not None:
                notify_request["delivery"]["recipient"] = resolved_recipient
            if subject is not None:
                notify_request["delivery"]["subject"] = subject
            if request_context is not None:
                notify_request["request_context"] = request_context

            deliver_args: dict[str, Any] = {
                "source_butler": butler_name,
                "notify_request": notify_request,
            }

            # Switchboard self-delivery: call deliver() directly instead of
            # proxying through switchboard_client (which is None on switchboard).
            if client is None and butler_name == "switchboard":
                pool = daemon.db.pool if daemon.db is not None else None
                if pool is None:
                    return {
                        "status": "error",
                        "error": "Database not available for direct delivery.",
                    }
                from butlers.tools.switchboard.notification.deliver import (
                    deliver as switchboard_deliver,
                )

                try:
                    result = await switchboard_deliver(
                        pool,
                        source_butler=butler_name,
                        notify_request=notify_request,
                    )
                    status = result.get("status", "sent")
                    if status == "failed":
                        return {
                            "status": "error",
                            "error": result.get("error", "Delivery failed"),
                        }
                    return {"status": "ok", "result": result}
                except Exception as exc:
                    logger.warning(
                        "notify() direct deliver failed for switchboard: %s",
                        exc,
                        exc_info=True,
                    )
                    return {"status": "error", "error": f"Direct delivery failed: {exc}"}

            _NOTIFY_TIMEOUT_S = 30
            try:
                result = await asyncio.wait_for(
                    client.call_tool("deliver", deliver_args),
                    timeout=_NOTIFY_TIMEOUT_S,
                )
                # FastMCP call_tool returns a CallToolResult
                if result.is_error:
                    # Extract error text from the result content
                    error_text = str(result.content[0].text) if result.content else "Unknown error"
                    return {"status": "error", "error": error_text}
                # Check inner payload for delivery-level failures (e.g. validation
                # errors from Switchboard/Messenger that don't raise MCP errors).
                data = result.data
                if isinstance(data, dict) and data.get("status") == "failed":
                    return {
                        "status": "error",
                        "error": data.get("error", "Delivery failed"),
                        "error_class": data.get("error_class", "delivery_error"),
                        "retryable": data.get("retryable", False),
                        "notification_id": data.get("notification_id"),
                    }
                return {"status": "ok", "result": data}
            except TimeoutError:
                logger.warning(
                    "notify() timed out after %ds for butler %s",
                    _NOTIFY_TIMEOUT_S,
                    butler_name,
                )
                return {
                    "status": "error",
                    "error": (
                        f"Switchboard call timed out after {_NOTIFY_TIMEOUT_S}s. "
                        "The Switchboard may be overloaded or unresponsive. "
                        "This is a transient error — retry after a brief delay."
                    ),
                    "retryable": True,
                }
            except (ConnectionError, OSError) as exc:
                logger.warning(
                    "notify() could not reach Switchboard for butler %s: %s",
                    butler_name,
                    exc,
                    exc_info=True,
                )
                return {
                    "status": "error",
                    "error": (
                        f"Switchboard unreachable: {exc}. "
                        "The Switchboard process may have stopped or restarted. "
                        "This is a transient error — retry after a brief delay."
                    ),
                    "retryable": True,
                }
            except Exception as exc:
                logger.warning(
                    "notify() failed for butler %s: %s",
                    butler_name,
                    exc,
                    exc_info=True,
                )
                return {
                    "status": "error",
                    "error": (
                        f"Switchboard call failed: {exc}. "
                        "If this persists, check that all required parameters "
                        "(channel, message, intent) are correct."
                    ),
                    "retryable": False,
                }
