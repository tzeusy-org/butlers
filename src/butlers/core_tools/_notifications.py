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
from butlers.core.attention_ledger import get_suppressing_context, record_attention_event
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
                Literal["telegram", "email"] | None,
                Field(
                    description=(
                        "Delivery channel. Allowed values: telegram | email. "
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
                        "entity_facts triple exists and approval parking is available for this "
                        "butler, the notification is parked as a pending_action and "
                        "status=pending_missing_identifier is returned. Otherwise notify() "
                        "fails closed without creating a pending action or owner notification."
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
            _why: Annotated[
                Any | None,
                Field(
                    description=(
                        "Required non-empty rationale for a non-owner recipient that "
                        "requires approval. Owner-directed notifications may omit it."
                    )
                ),
            ] = None,
            _evidence: Annotated[
                Any | None,
                Field(
                    description=(
                        "Optional typed evidence list. Each item must contain exactly "
                        "type, ref, and note."
                    )
                ),
            ] = None,
            _blast_radius: Annotated[
                Any | None,
                Field(
                    description=(
                        "Optional affected-scope classification: none, self, contact, or external."
                    )
                ),
            ] = None,
            _reversibility: Annotated[
                Any | None,
                Field(
                    description=(
                        "Optional reversibility classification: reversible, compensable, "
                        "or irreversible."
                    )
                ),
            ] = None,
        ) -> dict:
            """Send a `notify.v1` envelope through Switchboard `deliver()`.

            Required fields:
            - `channel` (string enum): `telegram` or `email`
            - `message` (string): required for `send`/`reply`, omitted for `react`

            Optional fields:
            - `recipient` (string): explicit recipient identity (e.g. email address or chat ID)
            - `entity_id` (UUID): resolve recipient from relationship.entity_facts (active
              triple preferred) keyed on this entity. If no matching triple exists and approval
              parking is available for this butler, the notification is parked as a pending_action
              and `{"status": "pending_missing_identifier"}` is returned. Otherwise notify()
              fails closed without creating a pending action or owner notification.
            - `subject` (string)
            - `intent` (string enum): `send` | `reply` | `react` | `insight`
            - `emoji` (string): required when `intent="react"`
            - `_why` (string): required for a non-owner recipient that enters approval gating;
              owner-directed notifications may omit it
            - `_evidence` (list): optional typed evidence objects with `type`, `ref`, and `note`
            - `_blast_radius` (string enum): optional `none` | `self` | `contact` | `external`
            - `_reversibility` (string enum): optional `reversible` | `compensable` | `irreversible`
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
            # --- Session correlation for the attention ledger (bu-358jk) ---
            # Bound once here, at the top of the call, so every ledger row this
            # call can write names the runtime session that made it. A caller
            # that spawned a session to send a notice can then ask the ledger
            # what became of it, instead of inferring delivery from whatever
            # state moved at around the same time. None outside a spawned
            # session (a daemon-internal notify has no session to name).
            _ledger_session_id = get_current_runtime_session_id()

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

            _notify_pool = daemon.db.pool if daemon.db is not None else None
            client = daemon.switchboard_client

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
                    # No matching entity_facts triple — validate the dossier, then park only when
                    # this butler has a durable approval-parking implementation.
                    action_id: uuid.UUID | None = None
                    pool = daemon.db.pool if daemon.db is not None else None
                    from butlers.core.approvals_hooks import (
                        validate_non_owner_dossier,
                        validate_owner_dossier,
                    )

                    # No channel identity exists to resolve through the normal recipient
                    # guard, so resolve the target entity's owner role directly before
                    # applying the dossier rule. A failed lookup is conservative: it
                    # remains a non-owner path and therefore requires `_why`.
                    is_owner_target = False
                    if pool is not None:
                        from butlers.core.owner import fetch_owner_entity_id

                        try:
                            owner_entity_id = await fetch_owner_entity_id(pool)
                            is_owner_target = owner_entity_id is not None and str(
                                owner_entity_id
                            ) == str(entity_id)
                        except Exception:  # noqa: BLE001
                            logger.warning(
                                "notify() could not resolve owner for missing identifier; "
                                "requiring a non-owner decision dossier",
                                exc_info=True,
                            )

                    dossier_or_error = (
                        validate_owner_dossier(
                            raw_why=_why,
                            raw_evidence=_evidence,
                            raw_blast_radius=_blast_radius,
                            raw_reversibility=_reversibility,
                        )
                        if is_owner_target
                        else validate_non_owner_dossier(
                            raw_why=_why,
                            raw_evidence=_evidence,
                            raw_blast_radius=_blast_radius,
                            raw_reversibility=_reversibility,
                        )
                    )
                    if isinstance(dossier_or_error, dict):
                        return dossier_or_error
                    dossier = dossier_or_error

                    info_type = daemon._CHANNEL_TO_CONTACT_INFO_TYPE.get(channel, channel)
                    from butlers.core.approvals_hooks import (
                        is_approval_parking_available,
                        park_pending_action,
                    )

                    if pool is None or not is_approval_parking_available(pool):
                        logger.error(
                            "notify() could not park missing-identifier notification: "
                            "entity_id=%s has no %r entity_facts triple; approvals are unavailable",
                            entity_id,
                            info_type,
                        )
                        await record_attention_event(
                            _notify_pool,
                            origin_butler=butler_name,
                            source="notify",
                            outcome="failed",
                            channel=channel,
                            intent=intent,
                            priority=priority,
                            reason="approval_parking_unavailable",
                            metadata={"entity_id": str(entity_id), "retryable": False},
                            session_id=_ledger_session_id,
                        )
                        return {
                            "status": "error",
                            "error": (
                                "Cannot deliver "
                                f"{channel!r} notification to entity {entity_id}: "
                                f"no {info_type!r} identifier is configured. "
                                "Approval parking is unavailable for this butler. "
                                "No pending action or owner notification was created. "
                                "Add the missing identifier or enable/recover approval parking, "
                                "then retry."
                            ),
                            "retryable": False,
                        }

                    import datetime as _dt

                    action_id = uuid.uuid4()
                    now = _dt.datetime.now(_dt.UTC)
                    expires_at = now + _dt.timedelta(hours=72)
                    agent_summary = (
                        f"notify() could not deliver a {channel!r} notification: "
                        f"entity {entity_id} has no {info_type!r} identifier in "
                        f"relationship.entity_facts. The message was: {message!r}. "
                        f"To resolve, assert a channel triple for this entity in the "
                        f"entity graph and re-trigger the notification."
                    )
                    # Bind the sanitized dict directly (no json.dumps, no
                    # ::jsonb cast) — asyncpg's registered jsonb codec
                    # already serializes once; pre-serializing double-
                    # encodes into a jsonb-typed STRING (bu-cymc4/bu-bstqu).
                    safe_park_tool_args = json.loads(
                        json.dumps(
                            {
                                "channel": channel,
                                "message": message,
                                "entity_id": str(entity_id),
                                "intent": intent,
                            },
                            default=str,
                        )
                    )
                    # park_pending_action is the single choke point for
                    # PENDING inserts: it writes the row AND attempts the
                    # owner-facing push in one call, replacing the ad hoc
                    # owner-alert deliver() this site used to build by hand
                    # (which had no reservation, no quiet-hours deferral, and
                    # no Approve/Reject affordance -- see bu-mda0r).
                    await park_pending_action(
                        pool,
                        action_id=action_id,
                        tool_name="notify",
                        tool_args=safe_park_tool_args,
                        agent_summary=agent_summary,
                        requested_at=now,
                        expires_at=expires_at,
                        session_id=get_current_runtime_session_id(),
                        why=dossier.why,
                        evidence=dossier.evidence,
                        blast_radius=dossier.blast_radius,
                        reversibility=dossier.reversibility,
                        origin_butler=butler_name,
                        approval_push_runtime=daemon._approval_push_runtime,
                    )
                    logger.warning(
                        "notify() parked as pending_missing_identifier: "
                        "entity_id=%s has no %r entity_facts triple (action=%s)",
                        entity_id,
                        info_type,
                        action_id,
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
                        why=_why,
                        evidence=_evidence,
                        blast_radius=_blast_radius,
                        reversibility=_reversibility,
                        enforce_dossier=True,
                        approval_push_runtime=daemon._approval_push_runtime,
                    )
                    if _decision.dossier_error is not None:
                        return _decision.dossier_error
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
                        why=_why,
                        evidence=_evidence,
                        blast_radius=_blast_radius,
                        reversibility=_reversibility,
                        enforce_dossier=True,
                        approval_push_runtime=daemon._approval_push_runtime,
                    )
                    if _decision.dossier_error is not None:
                        return _decision.dossier_error
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
            if any(value is not None for value in (_why, _evidence, _blast_radius, _reversibility)):
                # The recipient guards above have already validated every supplied
                # value. Keep the validated call context on the immutable delivery
                # envelope so a deferred flush is rechecked with the same dossier.
                notify_request["decision_dossier"] = {
                    "why": _why,
                    "evidence": _evidence if _evidence is not None else [],
                    "blast_radius": _blast_radius,
                    "reversibility": _reversibility,
                }

            # Quiet-hours delivery is a persistence boundary. Resolve the
            # recipient and enforce the decision dossier before enqueuing so a
            # deferred non-owner send cannot bypass the approval guard.
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
                    # Table may not exist yet or pool unavailable; deliver immediately.
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
                        _deliver_at = compute_deliver_at(prefs=_prefs, now=_now_utc)
                        try:
                            _notif_id = await insert_deferred_notification(
                                _notify_pool,
                                butler_name=butler_name,
                                channel=channel,
                                message=delivery_message,
                                priority=priority,
                                envelope=notify_request,
                                deliver_at=_deliver_at,
                                deferred_at=_now_utc,
                            )
                            logger.info(
                                "notify() deferred notification %s (priority=%s) to %s",
                                _notif_id,
                                priority,
                                _deliver_at.isoformat(),
                            )
                            await record_attention_event(
                                _notify_pool,
                                origin_butler=butler_name,
                                source="notify",
                                outcome="deferred",
                                channel=channel,
                                intent=intent,
                                priority=priority,
                                reason="delivery_preferences_quiet_hours",
                                notification_ref=_notif_id,
                                session_id=_ledger_session_id,
                            )
                            return {
                                "status": "deferred",
                                "notification_id": _notif_id,
                                "deliver_at": _deliver_at.isoformat(),
                                "channel": channel,
                                "priority": priority,
                            }
                        except Exception:
                            # If we cannot persist, fall through to immediate delivery.
                            logger.exception(
                                "notify() failed to defer notification; delivering immediately"
                            )

            # Approval-policy and context-bus gates apply only to routine,
            # implicit-owner notifications.  They intentionally remain after
            # delivery-preference deferral so the latter retains its existing
            # per-butler behaviour unchanged.
            if (
                _notify_pool is not None
                and entity_id is None
                and recipient is None
                and intent in {"send", "insight"}
                and priority != "high"
            ):
                from datetime import UTC as _PUTC
                from datetime import datetime as _pdatetime

                from butlers.core.approvals_policy import (
                    get_approvals_policy_quiet_hours,
                    policy_quiet_hours_deliver_at,
                )
                from butlers.core.temporal.delivery_db import (
                    insert_deferred_notification as _insert_deferred_notification,
                )

                _deferred_at = _pdatetime.now(_PUTC)
                try:
                    _policy = await get_approvals_policy_quiet_hours(_notify_pool)
                except Exception:
                    logger.debug(
                        "notify() failed to fetch approvals_policy; failing open",
                        exc_info=True,
                    )
                    _policy = None

                _policy_deliver_at = None
                if _policy is not None:
                    try:
                        _policy_deliver_at = policy_quiet_hours_deliver_at(
                            _policy, now=_deferred_at
                        )
                    except Exception:
                        logger.debug(
                            "notify() could not calculate approvals-policy delivery time; "
                            "failing open",
                            exc_info=True,
                        )

                try:
                    _context_suppression = await get_suppressing_context(_notify_pool)
                except Exception:
                    logger.debug(
                        "notify() failed to fetch suppressing context; failing open",
                        exc_info=True,
                    )
                    _context_suppression = None

                _context_deliver_at = (
                    _context_suppression.expires_at if _context_suppression is not None else None
                )
                _defer_candidates = [
                    candidate
                    for candidate in (_policy_deliver_at, _context_deliver_at)
                    if candidate is not None
                ]
                if _defer_candidates:
                    # When both guards are active, retain the notification until
                    # both clear rather than letting an earlier policy boundary
                    # violate a still-active DND/sleeping signal.
                    _deliver_at = max(_defer_candidates)
                    _reason_parts = []
                    if _policy_deliver_at is not None:
                        _reason_parts.append("policy_quiet_hours")
                    if _context_suppression is not None:
                        _reason_parts.append(f"context_bus:{_context_suppression.signal_type}")
                    _defer_reason = "+".join(_reason_parts)

                    try:
                        _notif_id = await _insert_deferred_notification(
                            _notify_pool,
                            butler_name=butler_name,
                            channel=channel,
                            message=delivery_message,
                            priority=priority,
                            envelope=notify_request,
                            deliver_at=_deliver_at,
                            deferred_at=_deferred_at,
                        )
                    except Exception as _defer_exc:
                        # Once a routine owner-default notification has been
                        # classified for a durable hold, an unavailable queue is
                        # retryable.  Do not turn a failed persistence boundary
                        # into immediate delivery or destructive suppression.
                        logger.exception(
                            "notify() could not persist owner-default deferred notification "
                            "(reason=%s channel=%s butler=%s)",
                            _defer_reason,
                            channel,
                            butler_name,
                        )
                        try:
                            await record_attention_event(
                                _notify_pool,
                                origin_butler=butler_name,
                                source="notify",
                                outcome="failed",
                                channel=channel,
                                intent=intent,
                                priority=priority,
                                reason=(f"deferred_persistence_error:{type(_defer_exc).__name__}"),
                                session_id=_ledger_session_id,
                            )
                        except Exception:
                            logger.warning(
                                "notify() could not record deferred persistence failure",
                                exc_info=True,
                            )
                        return {
                            "status": "error",
                            "error": {
                                "code": "deferred_notification_persistence_failed",
                                "message": (
                                    "The notification could not be queued for deferred delivery; "
                                    "retry the notify call."
                                ),
                                "retryable": True,
                            },
                            "retryable": True,
                        }

                    logger.info(
                        "notify() deferred routine owner-default notification %s "
                        "(reason=%s deliver_at=%s channel=%s butler=%s)",
                        _notif_id,
                        _defer_reason,
                        _deliver_at.isoformat(),
                        channel,
                        butler_name,
                    )
                    try:
                        await record_attention_event(
                            _notify_pool,
                            origin_butler=butler_name,
                            source="notify",
                            outcome="deferred",
                            channel=channel,
                            intent=intent,
                            priority=priority,
                            reason=_defer_reason,
                            notification_ref=_notif_id,
                            session_id=_ledger_session_id,
                        )
                    except Exception:
                        # The queue is already durable; ledger observability must
                        # never undo the caller-visible deferred result.
                        logger.warning(
                            "notify() deferred notification %s but could not record its ledger row",
                            _notif_id,
                            exc_info=True,
                        )

                    _deferred_result: dict[str, Any] = {
                        "status": "deferred",
                        "notification_id": _notif_id,
                        "deliver_at": _deliver_at.isoformat(),
                        "channel": channel,
                        "priority": priority,
                        "reason": _defer_reason,
                    }
                    if _policy_deliver_at is not None and _policy is not None:
                        _deferred_result.update(
                            {
                                "quiet_start_hour": _policy.get("quiet_start_hour"),
                                "quiet_end_hour": _policy.get("quiet_end_hour"),
                                "timezone": _policy.get("timezone", "UTC"),
                            }
                        )
                    if _context_suppression is not None:
                        _deferred_result["context_signal"] = _context_suppression.signal_type
                    return _deferred_result

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

            deliver_args: dict[str, Any] = {
                "source_butler": butler_name,
                "notify_request": notify_request,
            }

            async def _emit_notification_event() -> None:
                """Fan a "notification" event onto the multiplexed fleet event
                bus (bu-86c4c.8, move 5) via Postgres LISTEN/NOTIFY (RFC 0022,
                bu-01r64.1) for a successfully-delivered notify() call.
                Best-effort: never lets a bus hiccup fail delivery.
                """
                notification_event_data = {
                    "butler": butler_name,
                    "channel": channel,
                    "intent": intent,
                }
                notify_pool = daemon.db.pool if daemon.db is not None else None
                if notify_pool is not None:
                    try:
                        from butlers.fleet_events import publish_fleet_event

                        await publish_fleet_event(
                            notify_pool, "notification", notification_event_data
                        )
                    except Exception:
                        logger.debug(
                            "publish_fleet_event('notification') failed (non-fatal)",
                            exc_info=True,
                        )

            async def _record_failed_attention(
                reason: str,
                *,
                retryable: bool | None = None,
                notification_ref: str | None = None,
            ) -> None:
                """Stamp a terminal ``outcome="failed"`` attention-ledger row for a
                notify() dispatch attempt that errored out (bu-zcos8).

                Every delivery-failure return below (switchboard self-delivery
                failure/exception, and the proxied path's inner ``status="failed"``,
                MCP-level error, timeout, unreachable, and unexpected-exception
                returns) previously returned an error to the caller without writing
                any ledger row — so a genuine outage read identically to a benign
                quiet-hours hold (nothing recorded) on the exact surface built to
                prove silence is chosen. This mirrors the process-boundary consumers'
                contract (bu-hmdqz.3, core-notify spec §"Attention Ledger Recording
                at the notify() Boundary"): a real failure is ``"failed"``, never
                ``"deferred"`` (which is reserved for a hold the system retries on its
                own). ``retryable`` records whether the transport error may succeed on
                a later caller retry; it does NOT auto-retry here.

                Best-effort/fail-open: ``record_attention_event`` never raises and
                no-ops when the pool is absent, so a ledger-write hiccup can never
                mask the original notify() error being returned.
                """
                await record_attention_event(
                    _notify_pool,
                    origin_butler=butler_name,
                    source="notify",
                    outcome="failed",
                    channel=channel,
                    intent=intent,
                    priority=priority,
                    reason=reason,
                    notification_ref=notification_ref,
                    metadata={"retryable": retryable} if retryable is not None else None,
                    session_id=_ledger_session_id,
                )

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
                        await _record_failed_attention(
                            f"delivery_error:{result.get('error', 'unknown')}",
                            retryable=bool(result.get("retryable", False)),
                            notification_ref=(
                                result.get("notification_id") if isinstance(result, dict) else None
                            ),
                        )
                        return {
                            "status": "error",
                            "error": result.get("error", "Delivery failed"),
                        }
                    await _emit_notification_event()
                    await record_attention_event(
                        pool,
                        origin_butler=butler_name,
                        source="notify",
                        outcome="delivered",
                        channel=channel,
                        intent=intent,
                        priority=priority,
                        notification_ref=result.get("notification_id")
                        if isinstance(result, dict)
                        else None,
                        session_id=_ledger_session_id,
                    )
                    return {"status": "ok", "result": result}
                except Exception as exc:
                    logger.warning(
                        "notify() direct deliver failed for switchboard: %s",
                        exc,
                        exc_info=True,
                    )
                    await _record_failed_attention(f"unexpected_error:{type(exc).__name__}")
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
                    await _record_failed_attention(f"delivery_error:{error_text}")
                    return {"status": "error", "error": error_text}
                # Check inner payload for delivery-level failures (e.g. validation
                # errors from Switchboard/Messenger that don't raise MCP errors).
                data = result.data
                if isinstance(data, dict) and data.get("status") == "failed":
                    await _record_failed_attention(
                        f"delivery_error:{data.get('error', 'unknown')}",
                        retryable=bool(data.get("retryable", False)),
                        notification_ref=data.get("notification_id"),
                    )
                    return {
                        "status": "error",
                        "error": data.get("error", "Delivery failed"),
                        "error_class": data.get("error_class", "delivery_error"),
                        "retryable": data.get("retryable", False),
                        "notification_id": data.get("notification_id"),
                    }
                await _emit_notification_event()
                await record_attention_event(
                    daemon.db.pool if daemon.db is not None else None,
                    origin_butler=butler_name,
                    source="notify",
                    outcome="delivered",
                    channel=channel,
                    intent=intent,
                    priority=priority,
                    notification_ref=(
                        data.get("notification_id") if isinstance(data, dict) else None
                    ),
                    session_id=_ledger_session_id,
                )
                return {"status": "ok", "result": data}
            except TimeoutError:
                logger.warning(
                    "notify() timed out after %ds for butler %s",
                    _NOTIFY_TIMEOUT_S,
                    butler_name,
                )
                await _record_failed_attention(
                    f"delivery_error:switchboard_timeout_{_NOTIFY_TIMEOUT_S}s",
                    retryable=True,
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
                await _record_failed_attention(
                    f"delivery_error:switchboard_unreachable:{type(exc).__name__}",
                    retryable=True,
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
                await _record_failed_attention(f"unexpected_error:{type(exc).__name__}")
                return {
                    "status": "error",
                    "error": (
                        f"Switchboard call failed: {exc}. "
                        "If this persists, check that all required parameters "
                        "(channel, message, intent) are correct."
                    ),
                    "retryable": False,
                }
