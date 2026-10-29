"""Calendar event tools implementation."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal

from butlers.core.tool_call_capture import get_current_runtime_session_id as _get_session_id

from .constants import (
    MUTATION_STATUS_APPLIED,
    MUTATION_STATUS_FAILED,
    MUTATION_STATUS_NOOP,
    MUTATION_STATUS_PENDING,
    SOURCE_KIND_PROVIDER,
    CalendarConflictPolicy,
    logger,
)
from .errors import CalendarAuthError, _build_structured_error
from .models import (
    CalendarEventCreate,
    CalendarEventUpdate,
    CalendarNotificationInput,
    EventStatus,
    EventVisibility,
)

if TYPE_CHECKING:
    from . import CalendarModule


def register_event_tools(
    module: CalendarModule, mcp: Any, _tool: Callable[..., Any], butler_name: str
) -> None:

    @_tool("core")
    async def calendar_create_event(
        title: str,
        start_at: date | datetime,
        end_at: date | datetime,
        all_day: bool | None = None,
        timezone: str | None = None,
        description: str | None = None,
        body: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        recurrence_rule: str | None = None,
        notification: CalendarNotificationInput | bool | int | None = None,
        status: EventStatus | None = None,
        visibility: EventVisibility | None = None,
        notes: str | None = None,
        color_id: str | None = None,
        calendar_id: str | None = None,
        conflict_policy: CalendarConflictPolicy | None = None,
        entity_ids: list[uuid.UUID] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an event and mark it as Butler-generated.

        For all-day events, pass date objects (not datetime) for start_at and
        end_at, or set all_day=True with date-only values.
        Fail-closed: provider errors return a structured error dict rather than
        silently dropping the mutation (spec section 4.4 / 15.2).
        """
        await module._require_calendar_write_permission()
        provider = module._require_provider()
        resolved_calendar_id = module._resolve_calendar_id(calendar_id, for_create=True)
        resolved_conflict_policy = module._resolve_conflict_policy(conflict_policy)
        normalized_request_id = module._normalize_request_id(request_id)
        resolved_entity_ids = entity_ids or []
        action_payload = {
            "title": title,
            "start_at": start_at,
            "end_at": end_at,
            "all_day": all_day,
            "timezone": timezone,
            "description": description,
            "body": body,
            "location": location,
            "attendees": attendees or [],
            "recurrence_rule": recurrence_rule,
            "status": status,
            "visibility": visibility,
            "notes": notes,
            "color_id": color_id,
            "calendar_id": resolved_calendar_id,
            "conflict_policy": resolved_conflict_policy,
            "entity_ids": [str(e) for e in resolved_entity_ids],
        }
        idempotency_key, replay = await module._prepare_workspace_mutation(
            action_type="workspace_user_create",
            request_id=normalized_request_id,
            action_payload=action_payload,
        )
        if replay is not None:
            return replay
        source_id = await module._resolve_action_source_id(
            source_kind=SOURCE_KIND_PROVIDER,
            lane="user",
            calendar_id=resolved_calendar_id,
        )

        try:
            create_payload = CalendarEventCreate(
                title=module._ensure_butler_title(title),
                start_at=start_at,
                end_at=end_at,
                all_day=all_day,
                timezone=timezone,
                description=description,
                body=body,
                location=location,
                attendees=attendees or [],
                recurrence_rule=recurrence_rule,
                notification=notification,
                status=status,
                visibility=visibility,
                notes=notes,
                color_id=color_id,
                private_metadata=module._build_butler_private_metadata(
                    butler_name=module._butler_name
                ),
                entity_ids=resolved_entity_ids,
            )
            conflict_result = await module._check_conflicts(
                provider=provider,
                calendar_id=resolved_calendar_id,
                candidate=create_payload,
                conflict_policy=resolved_conflict_policy,
            )
        except CalendarAuthError as exc:
            logger.error(
                "calendar_create_event failed during conflict check (calendar_id=%s): %s",
                resolved_calendar_id,
                exc,
                exc_info=True,
            )
            error_payload = _build_structured_error(
                exc,
                provider=provider.name,
                calendar_id=resolved_calendar_id,
            )
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_create",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_FAILED,
                action_payload=action_payload,
                action_result=error_payload,
                source_id=source_id,
                origin_ref=None,
                error=error_payload.get("error"),
            )
            return error_payload
        if conflict_result["status"] == "conflict":
            conflict_response = {
                "status": "conflict",
                "policy": resolved_conflict_policy,
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "conflicts": conflict_result["conflicts"],
                "suggested_slots": conflict_result["suggested_slots"],
            }
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_create",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_NOOP,
                action_payload=action_payload,
                action_result=conflict_response,
                source_id=source_id,
                origin_ref=None,
            )
            return conflict_response

        # Gate overlap override with conditional approval when configured.
        if conflict_result["status"] == "allow_overlap":
            approval_result = await module._gate_overlap_approval(
                tool_name="calendar_create_event",
                tool_args={
                    "title": title,
                    "start_at": start_at.isoformat(),
                    "end_at": end_at.isoformat(),
                    "all_day": all_day,
                    "timezone": timezone,
                    "description": description,
                    "body": body,
                    "location": location,
                    "attendees": attendees or [],
                    "recurrence_rule": recurrence_rule,
                    "notification": notification,
                    "status": status,
                    "visibility": visibility,
                    "notes": notes,
                    "color_id": color_id,
                    "calendar_id": calendar_id,
                    "conflict_policy": resolved_conflict_policy,
                    "entity_ids": [str(e) for e in resolved_entity_ids],
                },
                conflicts=conflict_result["conflicts"],
                provider_name=provider.name,
                resolved_calendar_id=resolved_calendar_id,
            )
            if approval_result is not None:
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_user_create",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_PENDING,
                    action_payload=action_payload,
                    action_result=approval_result,
                    source_id=source_id,
                    origin_ref=None,
                )
                return approval_result

        try:
            event = await provider.create_event(
                calendar_id=resolved_calendar_id,
                payload=create_payload,
            )
        except CalendarAuthError as exc:
            logger.error(
                "calendar_create_event provider write failed (calendar_id=%s): %s",
                resolved_calendar_id,
                exc,
                exc_info=True,
            )
            error_payload = _build_structured_error(
                exc,
                provider=provider.name,
                calendar_id=resolved_calendar_id,
            )
            await module._emit_calendar_audit(
                "create",
                title,
                resolved_calendar_id,
                result="error",
                error=str(exc),
            )
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_create",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_FAILED,
                action_payload=action_payload,
                action_result=error_payload,
                source_id=source_id,
                origin_ref=None,
                error=error_payload.get("error"),
            )
            return error_payload
        # Annotate the event with authorship metadata before projecting.
        session_id_str = _get_session_id()
        event = event.model_copy(
            update={
                "body": body if body is not None else event.body,
                "source_butler": module._butler_name,
                "source_session_id": session_id_str,
                "entity_ids": resolved_entity_ids,
            }
        )
        await module._emit_calendar_audit("create", title, resolved_calendar_id)
        result: dict[str, Any] = {
            "status": "created",
            "provider": provider.name,
            "calendar_id": resolved_calendar_id,
            "event": module._event_to_payload(event),
        }
        if conflict_result["conflicts"]:
            result["policy"] = resolved_conflict_policy
            result["conflicts"] = conflict_result["conflicts"]
            result["suggested_slots"] = []
        # Eagerly project the event to avoid Google indexing latency race.
        await module._project_provider_mutation(
            source_id=source_id,
            calendar_id=resolved_calendar_id,
            updated_events=[event],
        )
        result["projection_freshness"] = await module._refresh_user_projection(resolved_calendar_id)
        await module._finalize_workspace_mutation(
            idempotency_key=idempotency_key,
            action_type="workspace_user_create",
            request_id=normalized_request_id,
            action_status=MUTATION_STATUS_APPLIED,
            action_payload=action_payload,
            action_result=result,
            source_id=source_id,
            origin_ref=event.event_id,
        )
        return result

    @_tool("core")
    async def calendar_update_event(
        event_id: str,
        title: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        all_day: bool | None = None,
        timezone: str | None = None,
        description: str | None = None,
        body: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        recurrence_rule: str | None = None,
        recurrence_scope: Literal["this", "following", "series"] = "series",
        instance_start_at: datetime | None = None,
        color_id: str | None = None,
        calendar_id: str | None = None,
        conflict_policy: CalendarConflictPolicy | None = None,
        entity_ids: list[uuid.UUID] | None = None,
        clear_entity_ids: bool = False,
        request_id: str | None = None,
        _approval_bypass: bool = False,
    ) -> dict[str, Any]:
        """Update an event and preserve Butler tags for Butler-generated entries.

        ``recurrence_scope`` controls how a recurring event is mutated:
        ``series`` (default) edits the whole series; ``this`` edits only the
        occurrence named by ``instance_start_at``; ``following`` edits that
        occurrence and every later one (splitting the series at the boundary).
        ``this`` / ``following`` require ``instance_start_at``.

        ``entity_ids`` replaces a non-empty link set. To remove every linked
        entity, callers must pass ``entity_ids=[]`` with
        ``clear_entity_ids=True``; an omitted or empty list without that
        explicit signal preserves existing links.

        Fail-closed: provider errors return a structured error dict rather than
        silently dropping the mutation (spec section 4.4 / 15.2).
        """
        if clear_entity_ids and entity_ids != []:
            raise ValueError("clear_entity_ids requires entity_ids=[]")
        if recurrence_scope != "series":
            if all_day is not None:
                raise ValueError("all_day updates require recurrence_scope='series'")
            return await module._apply_occurrence_update(
                event_id=event_id,
                instance_start_at=instance_start_at,
                recurrence_scope=recurrence_scope,
                title=title,
                start_at=start_at,
                end_at=end_at,
                timezone=timezone,
                description=description,
                body=body,
                location=location,
                recurrence_rule=recurrence_rule,
                color_id=color_id,
                calendar_id=calendar_id,
                entity_ids=entity_ids,
                clear_entity_ids=clear_entity_ids,
                request_id=request_id,
                tool_name="calendar_update_event",
                approval_bypass=_approval_bypass,
            )
        await module._require_calendar_write_permission()
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        provider = module._require_provider()
        resolved_calendar_id = await module._resolve_home_calendar_id(
            event_id=normalized_event_id, override_calendar_id=calendar_id
        )
        resolved_conflict_policy = module._resolve_conflict_policy(conflict_policy)
        normalized_request_id = module._normalize_request_id(request_id)
        action_payload = {
            "event_id": normalized_event_id,
            "title": title,
            "start_at": start_at,
            "end_at": end_at,
            "all_day": all_day,
            "timezone": timezone,
            "description": description,
            "body": body,
            "location": location,
            "attendees": attendees,
            "recurrence_rule": recurrence_rule,
            "recurrence_scope": recurrence_scope,
            "color_id": color_id,
            "calendar_id": resolved_calendar_id,
            "conflict_policy": resolved_conflict_policy,
            "entity_ids": [str(e) for e in entity_ids] if entity_ids is not None else None,
            "clear_entity_ids": clear_entity_ids,
        }
        idempotency_key, replay = await module._prepare_workspace_mutation(
            action_type="workspace_user_update",
            request_id=normalized_request_id,
            action_payload=action_payload,
        )
        if replay is not None:
            return replay
        source_id = await module._resolve_action_source_id(
            source_kind=SOURCE_KIND_PROVIDER,
            lane="user",
            calendar_id=resolved_calendar_id,
        )
        try:
            existing_event = await provider.get_event(
                calendar_id=resolved_calendar_id,
                event_id=normalized_event_id,
            )
        except CalendarAuthError as exc:
            logger.error(
                "calendar_update_event failed fetching existing event "
                "(event_id=%s, calendar_id=%s): %s",
                normalized_event_id,
                resolved_calendar_id,
                exc,
                exc_info=True,
            )
            error_payload = _build_structured_error(
                exc,
                provider=provider.name,
                calendar_id=resolved_calendar_id,
            )
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_update",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_FAILED,
                action_payload=action_payload,
                action_result=error_payload,
                source_id=source_id,
                origin_ref=normalized_event_id,
                error=error_payload.get("error"),
            )
            return error_payload
        if existing_event is None:
            not_found_result = {
                "status": "not_found",
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event_id": normalized_event_id,
            }
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_update",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_NOOP,
                action_payload=action_payload,
                action_result=not_found_result,
                source_id=source_id,
                origin_ref=normalized_event_id,
            )
            return not_found_result

        normalized_title = title.strip() if isinstance(title, str) else None
        update_title = normalized_title
        patch_all_day = all_day
        if (
            patch_all_day is None
            and existing_event.all_day
            and (start_at is not None or end_at is not None)
        ):
            patch_all_day = True
        private_metadata: dict[str, str] | None = None
        if existing_event.butler_generated:
            update_title = module._ensure_butler_title(
                existing_event.title if update_title is None else update_title
            )
            private_metadata = module._build_butler_private_metadata(
                butler_name=existing_event.butler_name or module._butler_name
            )

        conflict_result: dict[str, Any] = {
            "status": "clear",
            "conflicts": [],
            "suggested_slots": [],
        }
        if module._time_window_changed(
            existing_event=existing_event,
            start_at=start_at,
            end_at=end_at,
        ):
            try:
                candidate = CalendarEventCreate(
                    title=update_title or existing_event.title,
                    start_at=start_at if start_at is not None else existing_event.start_at,
                    end_at=end_at if end_at is not None else existing_event.end_at,
                    all_day=(
                        patch_all_day if patch_all_day is not None else existing_event.all_day
                    ),
                    timezone=timezone or existing_event.timezone,
                    description=(
                        description if description is not None else existing_event.description
                    ),
                    location=location if location is not None else existing_event.location,
                    attendees=(
                        attendees
                        if attendees is not None
                        else [a.email for a in existing_event.attendees]
                    ),
                    recurrence_rule=(
                        recurrence_rule
                        if recurrence_rule is not None
                        else existing_event.recurrence_rule
                    ),
                    color_id=color_id if color_id is not None else existing_event.color_id,
                )
                conflict_result = await module._check_conflicts(
                    provider=provider,
                    calendar_id=resolved_calendar_id,
                    candidate=candidate,
                    conflict_policy=resolved_conflict_policy,
                    ignore_start_at=existing_event.start_at,
                    ignore_end_at=existing_event.end_at,
                )
            except CalendarAuthError as exc:
                logger.error(
                    "calendar_update_event failed during conflict check "
                    "(event_id=%s, calendar_id=%s): %s",
                    normalized_event_id,
                    resolved_calendar_id,
                    exc,
                    exc_info=True,
                )
                error_payload = _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_user_update",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_FAILED,
                    action_payload=action_payload,
                    action_result=error_payload,
                    source_id=source_id,
                    origin_ref=normalized_event_id,
                    error=error_payload.get("error"),
                )
                return error_payload
            if conflict_result["status"] == "conflict":
                conflict_response = {
                    "status": "conflict",
                    "policy": resolved_conflict_policy,
                    "provider": provider.name,
                    "calendar_id": resolved_calendar_id,
                    "conflicts": conflict_result["conflicts"],
                    "suggested_slots": conflict_result["suggested_slots"],
                }
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_user_update",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_NOOP,
                    action_payload=action_payload,
                    action_result=conflict_response,
                    source_id=source_id,
                    origin_ref=normalized_event_id,
                )
                return conflict_response

            # Gate overlap override with conditional approval when configured.
            if conflict_result["status"] == "allow_overlap":
                approval_result = await module._gate_overlap_approval(
                    tool_name="calendar_update_event",
                    tool_args={
                        "event_id": event_id,
                        "title": title,
                        "start_at": start_at.isoformat() if start_at else None,
                        "end_at": end_at.isoformat() if end_at else None,
                        "all_day": all_day,
                        "timezone": timezone,
                        "description": description,
                        "body": body,
                        "location": location,
                        "attendees": attendees,
                        "recurrence_rule": recurrence_rule,
                        "recurrence_scope": recurrence_scope,
                        "color_id": color_id,
                        "calendar_id": calendar_id,
                        "conflict_policy": resolved_conflict_policy,
                        "entity_ids": (
                            [str(e) for e in entity_ids] if entity_ids is not None else None
                        ),
                        "clear_entity_ids": clear_entity_ids,
                    },
                    conflicts=conflict_result["conflicts"],
                    provider_name=provider.name,
                    resolved_calendar_id=resolved_calendar_id,
                )
                if approval_result is not None:
                    await module._finalize_workspace_mutation(
                        idempotency_key=idempotency_key,
                        action_type="workspace_user_update",
                        request_id=normalized_request_id,
                        action_status=MUTATION_STATUS_PENDING,
                        action_payload=action_payload,
                        action_result=approval_result,
                        source_id=source_id,
                        origin_ref=normalized_event_id,
                    )
                    return approval_result

        update_patch = CalendarEventUpdate(
            title=update_title,
            start_at=start_at,
            end_at=end_at,
            all_day=patch_all_day,
            timezone=timezone,
            description=description,
            body=body,
            location=location,
            attendees=attendees,
            recurrence_rule=recurrence_rule,
            recurrence_scope=recurrence_scope,
            color_id=color_id,
            private_metadata=private_metadata,
            entity_ids=entity_ids,
            etag=existing_event.etag,
        )
        pre_state_entity_ids = await module._fetch_pre_state_entity_ids(
            source_id=source_id,
            origin_ref=normalized_event_id,
            fallback=existing_event.entity_ids,
        )
        pre_state = module._capture_event_pre_state(
            existing_event,
            resolved_calendar_id,
            entity_ids=pre_state_entity_ids,
        )
        try:
            event = await provider.update_event(
                calendar_id=resolved_calendar_id,
                event_id=normalized_event_id,
                patch=update_patch,
            )
        except CalendarAuthError as exc:
            logger.error(
                "calendar_update_event provider write failed (event_id=%s, calendar_id=%s): %s",
                normalized_event_id,
                resolved_calendar_id,
                exc,
                exc_info=True,
            )
            error_payload = _build_structured_error(
                exc,
                provider=provider.name,
                calendar_id=resolved_calendar_id,
            )
            await module._emit_calendar_audit(
                "update",
                title,
                resolved_calendar_id,
                result="error",
                error=str(exc),
            )
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_update",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_FAILED,
                action_payload=action_payload,
                action_result=error_payload,
                source_id=source_id,
                origin_ref=normalized_event_id,
                error=error_payload.get("error"),
            )
            return error_payload
        # Annotate updated event with authorship and body.
        session_id_str = _get_session_id()
        event = event.model_copy(
            update={
                "body": body if body is not None else event.body,
                "source_butler": module._butler_name,
                "source_session_id": session_id_str,
                "entity_ids": entity_ids if entity_ids is not None else event.entity_ids,
            }
        )
        await module._emit_calendar_audit("update", event.title or title, resolved_calendar_id)
        result = {
            "status": "updated",
            "provider": provider.name,
            "calendar_id": resolved_calendar_id,
            "event": module._event_to_payload(event),
        }
        if conflict_result["conflicts"]:
            result["policy"] = resolved_conflict_policy
            result["conflicts"] = conflict_result["conflicts"]
            result["suggested_slots"] = []
        # Eagerly project the update to avoid Google indexing latency race.
        await module._project_provider_mutation(
            source_id=source_id,
            calendar_id=resolved_calendar_id,
            updated_events=[event],
            clear_entity_ids=clear_entity_ids,
        )
        result["projection_freshness"] = await module._refresh_user_projection(resolved_calendar_id)
        # Capture the pre-mutation pre-image so the dashboard undo endpoint
        # can reverse-apply an inverse calendar_update_event. It reuses the
        # already-fetched provider event plus its local projection links.
        result["pre_state"] = pre_state
        await module._finalize_workspace_mutation(
            idempotency_key=idempotency_key,
            action_type="workspace_user_update",
            request_id=normalized_request_id,
            action_status=MUTATION_STATUS_APPLIED,
            action_payload=action_payload,
            action_result=result,
            source_id=source_id,
            origin_ref=normalized_event_id,
        )
        return result

    @_tool("core")
    async def calendar_delete_event(
        event_id: str,
        calendar_id: str | None = None,
        recurrence_scope: Literal["this", "following", "series"] = "series",
        instance_start_at: datetime | None = None,
        send_updates: str | None = None,
        request_id: str | None = None,
        _approval_bypass: bool = False,
    ) -> dict[str, Any]:
        """Delete or cancel a calendar event.

        For events with attendees, pass send_updates="all" to send
        cancellation notifications.  By default (send_updates=None),
        no notification emails are sent.

        ``recurrence_scope`` controls how a recurring event is deleted:
        ``series`` (default) removes the whole series; ``this`` removes only
        the occurrence named by ``instance_start_at`` (EXDATE); ``following``
        removes that occurrence and every later one (UNTIL split). ``this`` /
        ``following`` require ``instance_start_at`` and the base recurring
        event ID — not an individual occurrence ID.

        Returns status="deleted" on success, or status="not_found" when
        the event did not exist (already deleted — treated as success).
        """
        if recurrence_scope != "series":
            return await module._apply_occurrence_delete(
                event_id=event_id,
                instance_start_at=instance_start_at,
                recurrence_scope=recurrence_scope,
                calendar_id=calendar_id,
                send_updates=send_updates,
                request_id=request_id,
                tool_name="calendar_delete_event",
                approval_bypass=_approval_bypass,
            )
        await module._require_calendar_write_permission()
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        provider = module._require_provider()
        resolved_calendar_id = await module._resolve_home_calendar_id(
            event_id=normalized_event_id, override_calendar_id=calendar_id
        )
        normalized_request_id = module._normalize_request_id(request_id)
        action_payload = {
            "event_id": normalized_event_id,
            "calendar_id": resolved_calendar_id,
            "recurrence_scope": recurrence_scope,
            "send_updates": send_updates,
        }
        idempotency_key, replay = await module._prepare_workspace_mutation(
            action_type="workspace_user_delete",
            request_id=normalized_request_id,
            action_payload=action_payload,
        )
        if replay is not None:
            return replay
        source_id = await module._resolve_action_source_id(
            source_kind=SOURCE_KIND_PROVIDER,
            lane="user",
            calendar_id=resolved_calendar_id,
        )

        # Fetch the event first to confirm existence and capture metadata.
        # 404 from the provider is treated as success (idempotent delete).
        try:
            existing_event = await provider.get_event(
                calendar_id=resolved_calendar_id,
                event_id=normalized_event_id,
            )
        except CalendarAuthError as exc:
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_delete",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_FAILED,
                action_payload=action_payload,
                action_result={
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "provider": provider.name,
                    "calendar_id": resolved_calendar_id,
                },
                source_id=source_id,
                origin_ref=normalized_event_id,
                error=str(exc),
            )
            raise
        if existing_event is None:
            result = {
                "status": "not_found",
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event_id": normalized_event_id,
            }
            result["projection_freshness"] = await module._refresh_user_projection(
                resolved_calendar_id
            )
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_delete",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_NOOP,
                action_payload=action_payload,
                action_result=result,
                source_id=source_id,
                origin_ref=normalized_event_id,
            )
            return result

        pre_state_entity_ids = await module._fetch_pre_state_entity_ids(
            source_id=source_id,
            origin_ref=normalized_event_id,
            fallback=existing_event.entity_ids,
        )
        pre_state = module._capture_event_pre_state(
            existing_event,
            resolved_calendar_id,
            entity_ids=pre_state_entity_ids,
        )
        try:
            await provider.delete_event(
                calendar_id=resolved_calendar_id,
                event_id=normalized_event_id,
                send_updates=send_updates,
            )
        except CalendarAuthError as exc:
            await module._emit_calendar_audit(
                "delete",
                existing_event.title if existing_event else None,
                resolved_calendar_id,
                result="error",
                error=str(exc),
            )
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_delete",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_FAILED,
                action_payload=action_payload,
                action_result={
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "provider": provider.name,
                    "calendar_id": resolved_calendar_id,
                },
                source_id=source_id,
                origin_ref=normalized_event_id,
                error=str(exc),
            )
            raise
        await module._emit_calendar_audit(
            "delete",
            existing_event.title if existing_event else None,
            resolved_calendar_id,
        )
        result = {
            "status": "deleted",
            "provider": provider.name,
            "calendar_id": resolved_calendar_id,
            "event_id": normalized_event_id,
        }
        # Eagerly cancel in projection to avoid Google indexing latency race.
        await module._project_provider_mutation(
            source_id=source_id,
            calendar_id=resolved_calendar_id,
            cancelled_ids=[normalized_event_id],
        )
        result["projection_freshness"] = await module._refresh_user_projection(resolved_calendar_id)
        # Capture the pre-deletion pre-image so the dashboard undo endpoint
        # can reverse-apply an inverse calendar_create_event that recreates
        # the event and its local links on its home calendar.
        result["pre_state"] = pre_state
        await module._finalize_workspace_mutation(
            idempotency_key=idempotency_key,
            action_type="workspace_user_delete",
            request_id=normalized_request_id,
            action_status=MUTATION_STATUS_APPLIED,
            action_payload=action_payload,
            action_result=result,
            source_id=source_id,
            origin_ref=normalized_event_id,
        )
        return result

    @_tool("core")
    async def calendar_update_event_instance(
        event_id: str,
        instance_start_at: datetime,
        title: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        timezone: str | None = None,
        description: str | None = None,
        body: str | None = None,
        location: str | None = None,
        recurrence_scope: Literal["this", "following"] = "this",
        calendar_id: str | None = None,
        entity_ids: list[uuid.UUID] | None = None,
        request_id: str | None = None,
        _approval_bypass: bool = False,
    ) -> dict[str, Any]:
        """Edit a single occurrence (or this-and-following) of a recurring event.

        ``event_id`` is the base recurring event ID and ``instance_start_at``
        is the occurrence's original start.  ``this`` detaches just that
        occurrence as an exception (the original slot is EXDATE-d and the edit
        is carried onto the detached occurrence); ``following`` splits the
        series at the boundary and applies the edit to the remainder.  The
        matching ``calendar_event_instances`` row is marked ``is_exception``.
        """
        return await module._apply_occurrence_update(
            event_id=event_id,
            instance_start_at=instance_start_at,
            recurrence_scope=recurrence_scope,
            title=title,
            start_at=start_at,
            end_at=end_at,
            timezone=timezone,
            description=description,
            body=body,
            location=location,
            recurrence_rule=None,
            color_id=None,
            calendar_id=calendar_id,
            entity_ids=entity_ids,
            request_id=request_id,
            tool_name="calendar_update_event_instance",
            approval_bypass=_approval_bypass,
        )

    @_tool("core")
    async def calendar_delete_event_instance(
        event_id: str,
        instance_start_at: datetime,
        recurrence_scope: Literal["this", "following"] = "this",
        send_updates: str | None = None,
        calendar_id: str | None = None,
        request_id: str | None = None,
        _approval_bypass: bool = False,
    ) -> dict[str, Any]:
        """Delete a single occurrence (or this-and-following) of a recurring event.

        ``event_id`` is the base recurring event ID and ``instance_start_at``
        is the occurrence's original start.  ``this`` appends a timezone-correct
        ``EXDATE`` for that occurrence, leaving the rest of the series intact;
        ``following`` bounds the series RRULE with ``UNTIL`` just before the
        occurrence.  The matching ``calendar_event_instances`` row is marked
        ``is_exception`` (status cancelled).  A non-existent base event surfaces
        a fail-open not-found response.
        """
        return await module._apply_occurrence_delete(
            event_id=event_id,
            instance_start_at=instance_start_at,
            recurrence_scope=recurrence_scope,
            calendar_id=calendar_id,
            send_updates=send_updates,
            request_id=request_id,
            tool_name="calendar_delete_event_instance",
            approval_bypass=_approval_bypass,
        )
