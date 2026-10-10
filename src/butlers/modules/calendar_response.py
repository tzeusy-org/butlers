"""Approval-bound invitation commands and their closed admission failures.

Provider resources stay in memory. Only the verified participant, occurrence
and version needed to authorize one conditional write belong in a command.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from butlers.modules.approvals.execution_context import get_approval_execution_context
from butlers.modules.approvals.park import park_pending_action


class CalendarResponseError(RuntimeError):
    """A closed failure category, without provider arguments or event content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CalendarResponseUncertainError(CalendarResponseError):
    """A started request has no attributable, verified terminal result."""


def _text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\r" in value or "\n" in value:
        return None
    return value.strip()


@dataclass(frozen=True, slots=True)
class SelfResponseSnapshot:
    """Exact server-read self and occurrence binding; never a caller claim."""

    event_id: str
    self_email: str
    response_status: str
    etag: str
    recurring_event_id: str | None
    original_start: dict[str, str] | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def invitation_snapshot(
    resource: dict[str, Any], *, event_id: str, account_email: str
) -> SelfResponseSnapshot:
    """Require explicit, uniquely configured participant and actual occurrence.

    Google's ``self`` flag identifies a calendar copy. It does not establish
    which account the daemon's credential belongs to, so the caller of this
    function must first verify its account registry/credential/source binding.
    """
    if not isinstance(resource, dict) or resource.get("id") != event_id:
        raise CalendarResponseError("response_target_mismatch")
    if not isinstance(resource.get("status"), str) or resource["status"] not in {
        "confirmed",
        "tentative",
    }:
        raise CalendarResponseError("response_event_ineligible")
    etag = _text(resource.get("etag"))
    if etag is None:
        raise CalendarResponseError("response_version_unavailable")
    if "attendeesOmitted" in resource and resource["attendeesOmitted"] is not False:
        raise CalendarResponseError("response_attendees_incomplete")
    attendees = resource.get("attendees")
    account = _text(account_email)
    if not isinstance(attendees, list) or account is None:
        raise CalendarResponseError("response_self_unverified")
    selves = [a for a in attendees if isinstance(a, dict) and a.get("self") is True]
    if len(selves) != 1:
        raise CalendarResponseError("response_self_unverified")
    self_attendee = selves[0]
    email = _text(self_attendee.get("email"))
    if email is None or email.casefold() != account.casefold():
        raise CalendarResponseError("response_account_mismatch")
    same_email = [
        a
        for a in attendees
        if isinstance(a, dict)
        and isinstance(a.get("email"), str)
        and a["email"].strip().casefold() == email.casefold()
    ]
    organizer = resource.get("organizer")
    organizer = organizer if isinstance(organizer, dict) else {}
    organizer_email = _text(organizer.get("email"))
    if (
        len(same_email) != 1
        or self_attendee.get("organizer") is True
        or organizer.get("self") is True
        or (organizer_email is not None and organizer_email.casefold() == email.casefold())
    ):
        raise CalendarResponseError("response_self_unverified")
    status = self_attendee.get("responseStatus")
    if not isinstance(status, str) or status not in {
        "needsAction",
        "accepted",
        "declined",
        "tentative",
    }:
        raise CalendarResponseError("response_status_unavailable")
    # A recurring master is not an occurrence. No expansion or guessed ID is
    # allowed at this admission boundary.
    if resource.get("recurrence"):
        raise CalendarResponseError("response_series_root")
    recurring = resource.get("recurringEventId")
    original = resource.get("originalStartTime")
    original_start = None
    if recurring is not None or original is not None:
        recurring = _text(recurring)
        if recurring is None or not isinstance(original, dict):
            raise CalendarResponseError("response_occurrence_unverified")
        boundaries = [key for key in ("date", "dateTime") if _text(original.get(key))]
        if len(boundaries) != 1:
            raise CalendarResponseError("response_occurrence_unverified")
        try:
            if boundaries[0] == "date":
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", original["date"]):
                    raise ValueError("invalid occurrence date")
                date.fromisoformat(original["date"])
            else:
                value = original["dateTime"]
                if not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})?",
                    value,
                ):
                    raise ValueError("invalid occurrence datetime")
                timestamp = datetime.fromisoformat(
                    value.replace("Z", "+00:00").replace("z", "+00:00")
                )
                if timestamp.tzinfo is None and not _text(original.get("timeZone")):
                    raise ValueError("missing occurrence offset or timezone")
        except ValueError:
            raise CalendarResponseError("response_occurrence_unverified") from None
        original_start = {boundaries[0]: original[boundaries[0]]}
        if "timeZone" in original:
            zone = _text(original["timeZone"])
            if zone is None:
                raise CalendarResponseError("response_occurrence_unverified")
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError):
                raise CalendarResponseError("response_occurrence_unverified") from None
            original_start["timeZone"] = zone
    return SelfResponseSnapshot(event_id, email, status, etag, recurring, original_start)


def verified_response_result(
    resource: dict[str, Any],
    *,
    before: SelfResponseSnapshot,
    response_status: str,
) -> SelfResponseSnapshot:
    """Only the original write's exact verified result earns an applied receipt."""
    try:
        after = invitation_snapshot(
            resource, event_id=before.event_id, account_email=before.self_email
        )
    except CalendarResponseError:
        raise CalendarResponseUncertainError("response_result_unverified") from None
    if (
        after.response_status != response_status
        or after.etag == before.etag
        or after.recurring_event_id != before.recurring_event_id
        or after.original_start != before.original_start
    ):
        raise CalendarResponseUncertainError("response_result_unverified")
    return after


# The ordinary executor transaction owns pending_actions. These command
# transactions intentionally acquire another connection: a rollback of approval
# dispatch must never erase evidence that an external write may have started.
_ENTRY_SQL = """
    SELECT i.id AS entry_id, i.event_id, i.source_id, i.status AS instance_status,
           e.origin_ref AS provider_event_id, e.status AS event_status,
           s.source_key, s.source_kind, s.lane, s.provider, s.calendar_id,
           s.butler_name, s.writable, s.metadata AS source_metadata
    FROM calendar_event_instances i
    JOIN calendar_events e ON e.id = i.event_id AND e.source_id = i.source_id
    JOIN calendar_sources s ON s.id = i.source_id
    WHERE i.id = $1
"""
_COMMAND_SQL = "SELECT * FROM calendar_action_log WHERE id = $1"


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _object(value: Any) -> dict[str, Any]:
    # Our owning pool registers the JSONB codec; strings/arrays are not commands.
    return value if isinstance(value, dict) else {}


class CalendarResponseCoordinator:
    """One source-owned command, decision and conditional write lifecycle.

    No task is detached and no time-based fence expiry exists. Retention may
    remove presentation details, but must retain this existing action-log row
    (or an equivalent content-blind target fence) while its attempt is unresolved.
    """

    public_arguments = frozenset({"entry_id", "response_status", "request_id", "send_updates"})

    def __init__(self, module: Any) -> None:
        self.module = module

    @property
    def pool(self) -> Any:
        return self.module._db.pool

    @staticmethod
    def public_args(tool_args: dict[str, Any]) -> dict[str, str]:
        if set(tool_args) - CalendarResponseCoordinator.public_arguments:
            raise CalendarResponseError("response_public_arguments_invalid")
        try:
            entry_id = str(uuid.UUID(tool_args["entry_id"]))
            request_id = str(uuid.UUID(tool_args["request_id"]))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise CalendarResponseError("response_request_invalid") from None
        status = tool_args.get("response_status")
        updates = tool_args.get("send_updates", "none")
        if not isinstance(status, str) or status not in {"accepted", "declined", "tentative"}:
            raise CalendarResponseError("response_status_invalid")
        if not isinstance(updates, str) or updates not in {"none", "all", "externalOnly"}:
            raise CalendarResponseError("response_send_updates_invalid")
        return dict(
            entry_id=entry_id, request_id=request_id, response_status=status, send_updates=updates
        )

    async def _target(self, entry_id: str) -> tuple[dict[str, Any], dict[str, str]]:
        row = await self.pool.fetchrow(_ENTRY_SQL, uuid.UUID(entry_id))
        if row is None:
            raise CalendarResponseError("response_entry_unavailable")
        target = dict(row)
        if (
            target["butler_name"] not in {None, self.module._butler_name}
            or target["source_kind"] != "provider_event"
            or target["provider"] != "google"
            or target["lane"] != "user"
            or target["writable"] is not True
            or target["instance_status"] not in {"confirmed", "tentative"}
            or target["event_status"] not in {"confirmed", "tentative"}
            or not _text(target["calendar_id"])
            or not _text(target["provider_event_id"])
        ):
            raise CalendarResponseError("response_source_ineligible")
        account = await self.module._verified_response_account()
        metadata = _object(target["source_metadata"])
        source_account = _text(metadata.get("account_email"))
        if source_account is None or source_account.casefold() != account["email"].casefold():
            raise CalendarResponseError("response_source_account_mismatch")
        return target, account

    async def _snapshot(
        self, target: dict[str, Any], account: dict[str, str]
    ) -> SelfResponseSnapshot:
        provider = self.module._require_provider()
        calendar = await provider.get_response_calendar(calendar_id=target["calendar_id"])
        if (
            not isinstance(calendar, dict)
            or calendar.get("id") != target["calendar_id"]
            or calendar.get("accessRole") not in {"owner", "writer"}
        ):
            raise CalendarResponseError("response_calendar_readonly")
        raw = await provider.get_response_event(
            calendar_id=target["calendar_id"], event_id=target["provider_event_id"]
        )
        return invitation_snapshot(
            raw, event_id=target["provider_event_id"], account_email=account["email"]
        )

    def _args(self, command_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
        args = dict(payload["command"]["arguments"])
        args.update(_command_id=str(command_id), _command_digest=payload["digest"])
        return args

    @staticmethod
    def _pending(command_id: uuid.UUID) -> dict[str, Any]:
        return {
            "status": "pending_approval",
            "action_id": str(command_id),
            "command_id": str(command_id),
        }

    def _approval_replay(self, command_id: uuid.UUID, status: str) -> dict[str, Any]:
        if status in {"rejected", "expired"}:
            return {
                "status": "rejected",
                "command_id": str(command_id),
                "reason": "response_approval_" + status,
            }
        if status == "approved":
            return {"status": "approved", "command_id": str(command_id)}
        if status != "pending":
            raise CalendarResponseError("response_approval_state_unavailable")
        return self._pending(command_id)

    async def _existing(self, request: dict[str, str]) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM calendar_action_log WHERE idempotency_key = $1",
            "respond:" + request["request_id"],
        )
        if row is None:
            return None
        payload = _object(row["action_payload"])
        if payload.get("request_digest") != _digest(request):
            raise CalendarResponseError("response_request_conflict")
        if payload.get("phase") == "reserved":
            return None
        result = _object(row["action_result"])
        if result:
            return result
        if payload.get("phase") in {"egress_started", "uncertain"}:
            return {"status": "uncertain", "command_id": str(row["id"])}
        pending = await self.pool.fetchrow(
            "SELECT status FROM pending_actions WHERE id = $1", row["id"]
        )
        if pending is None:
            raise CalendarResponseError("response_approval_unavailable")
        return self._approval_replay(row["id"], pending["status"])

    async def prepare_and_park(
        self,
        *,
        tool_args: dict[str, Any],
        requested_at: datetime,
        expires_at: datetime,
        dossier: Any,
    ) -> dict[str, Any]:
        try:
            if not self.module._response_ready:
                raise CalendarResponseError("response_capability_unavailable")
            request = self.public_args(tool_args)
            existing = await self._existing(request)
            if existing is not None:
                return existing
            target, account = await self._target(request["entry_id"])
            before = await self._snapshot(target, account)
            inverse = await self.pool.fetchrow(
                "SELECT * FROM calendar_action_log WHERE idempotency_key = $1",
                "respond:" + request["request_id"],
            )
            inverse_of = None
            arguments = dict(request)
            if inverse is not None:
                reservation = _object(inverse["action_payload"])
                if reservation.get("phase") != "reserved":
                    existing = await self._existing(request)
                    if existing is not None:
                        return existing
                    raise CalendarResponseError("response_request_conflict")
                if reservation.get("request_digest") != _digest(request):
                    raise CalendarResponseError("response_request_conflict")
                inverse_of = reservation.get("inverse_of")
                original = await self.pool.fetchrow(_COMMAND_SQL, uuid.UUID(inverse_of))
                receipt = _object(original["action_result"]) if original else {}
                if receipt.get("after") != before.to_dict():
                    raise CalendarResponseError("response_inverse_stale")
                arguments["response_status"] = receipt["before"]["response_status"]
            command = {
                "version": 1,
                "owner": self.module._butler_name,
                "arguments": arguments,
                "source_id": str(target["source_id"]),
                "event_id": str(target["event_id"]),
                "source_key": target["source_key"],
                "calendar_id": target["calendar_id"],
                "account": account,
                "before": before.to_dict(),
                "inverse_of": inverse_of,
            }
            # The provider instance ID is the stable occurrence identity. Raw
            # originalStartTime formatting or source/account row replacement
            # must not give an unresolved physical target another fence key.
            target_key = _digest(
                {
                    "provider": "google",
                    "calendar_id": target["calendar_id"],
                    "event_id": before.event_id,
                    "self": before.self_email.casefold(),
                }
            )
            payload = {
                "command": command,
                "digest": _digest(command),
                "request_digest": _digest(request),
                "target_key": target_key,
                "phase": "prepared",
            }
            command_id = inverse["id"] if inverse is not None else uuid.uuid4()
            async with self.pool.acquire() as connection, connection.transaction():
                await connection.execute(
                    "INSERT INTO calendar_action_log "
                    "(id,idempotency_key,request_id,action_type,source_id,event_id,"
                    "instance_id,origin_ref,action_payload) "
                    "VALUES ($1,$2,$3,'workspace_user_respond',$4,$5,$6,$7,$8) ON CONFLICT "
                    "(idempotency_key) DO NOTHING",
                    command_id,
                    "respond:" + request["request_id"],
                    request["request_id"],
                    target["source_id"],
                    target["event_id"],
                    uuid.UUID(request["entry_id"]),
                    before.event_id,
                    payload,
                )
                stored = await connection.fetchrow(
                    "SELECT * FROM calendar_action_log WHERE idempotency_key=$1 FOR UPDATE",
                    "respond:" + request["request_id"],
                )
                old = _object(stored["action_payload"])
                if old.get("phase") == "reserved":
                    # This reservation was created only by the authenticated undo
                    # service, under the original receipt's single-use lock.
                    if (
                        old.get("request_digest") != payload["request_digest"]
                        or old.get("inverse_of") != inverse_of
                    ):
                        raise CalendarResponseError("response_request_conflict")
                    await connection.execute(
                        "UPDATE calendar_action_log SET action_payload=$2, "
                        "action_type='workspace_user_respond',source_id=$3,event_id=$4, "
                        "instance_id=$5,origin_ref=$6 WHERE id=$1",
                        stored["id"],
                        payload,
                        target["source_id"],
                        target["event_id"],
                        uuid.UUID(request["entry_id"]),
                        before.event_id,
                    )
                elif old.get("digest") != payload["digest"]:
                    raise CalendarResponseError("response_request_conflict")
                command_id = stored["id"]
                pending = await connection.fetchrow(
                    "SELECT status FROM pending_actions WHERE id=$1", command_id
                )
                if pending is not None:
                    receipt = _object(stored["action_result"])
                    if receipt:
                        return receipt
                    if old.get("phase") in {"egress_started", "uncertain"}:
                        return {"status": "uncertain", "command_id": str(command_id)}
                    return self._approval_replay(command_id, pending["status"])
                if pending is None:
                    await park_pending_action(
                        connection,
                        action_id=command_id,
                        tool_name="calendar_respond",
                        tool_args=self._args(command_id, payload),
                        agent_summary="Restore invitation response"
                        if inverse_of
                        else "Respond to invitation",
                        requested_at=requested_at,
                        expires_at=expires_at,
                        why=dossier.why,
                        evidence=dossier.evidence,
                        blast_radius=dossier.blast_radius,
                        reversibility=dossier.reversibility,
                        origin_butler=self.module._butler_name,
                    )
            return self._pending(command_id)
        except CalendarResponseError as exc:
            return {"error": exc.code}
        except Exception:
            # Provider/SQL diagnostic text cannot enter the pending dossier.
            return {"error": "response_preparation_unavailable"}

    async def _claim(self, command_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
        lock_key = int.from_bytes(bytes.fromhex(payload["target_key"])[:8], "big", signed=True)
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock($1)", lock_key)
            row = await connection.fetchrow(_COMMAND_SQL + " FOR UPDATE", command_id)
            current = _object(row["action_payload"])
            if (
                current.get("digest") != payload["digest"]
                or current.get("command") != payload.get("command")
                or current.get("target_key") != payload.get("target_key")
            ):
                raise CalendarResponseError("response_command_conflict")
            if current.get("phase") != "prepared":
                return _object(row["action_result"]) or {
                    "status": "uncertain",
                    "command_id": str(command_id),
                }
            unresolved = await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM calendar_action_log WHERE id<>$1 "
                "AND action_payload->>'target_key'=$2 AND action_payload->>'phase' IN "
                "('egress_started','uncertain'))",
                command_id,
                payload["target_key"],
            )
            if unresolved:
                raise CalendarResponseError("response_target_uncertain")
            await connection.execute(
                "UPDATE calendar_action_log SET action_payload=action_payload || "
                "$2::jsonb,updated_at=now() WHERE id=$1",
                command_id,
                {"phase": "egress_started"},
            )
        return None

    async def _finish(self, command_id: uuid.UUID, status: str, result: dict[str, Any]) -> None:
        phase = "uncertain" if status == "uncertain" else "terminal"
        action_status = "pending" if status == "uncertain" else status
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "UPDATE calendar_action_log SET action_status=$2,action_result=$3,"
                "action_payload=action_payload || $4::jsonb,updated_at=now(),"
                "applied_at=CASE WHEN $2='applied' THEN now() ELSE applied_at END WHERE id=$1",
                command_id,
                action_status,
                result,
                {"phase": phase},
            )
            if status == "applied":
                row = await connection.fetchrow(_COMMAND_SQL, command_id)
                inverse_of = _object(row["action_payload"])["command"].get("inverse_of")
                if inverse_of:
                    await connection.execute(
                        "UPDATE calendar_action_log SET action_result=action_result || "
                        "$2::jsonb WHERE id=$1",
                        uuid.UUID(inverse_of),
                        {"undo": {"action_id": str(command_id)}},
                    )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        context = get_approval_execution_context(tool_name="calendar_respond", tool_args=arguments)
        if context is None or not self.module._response_ready:
            return {"error": "response_approval_required"}
        admitted_command_id = None
        try:
            command_id = uuid.UUID(arguments["_command_id"])
            if command_id != context.action_id:
                raise CalendarResponseError("response_approval_mismatch")
            row = await self.pool.fetchrow(_COMMAND_SQL, command_id)
            payload = _object(row["action_payload"]) if row else {}
            if (
                payload.get("digest") != arguments.get("_command_digest")
                or self._args(command_id, payload) != arguments
            ):
                raise CalendarResponseError("response_command_mismatch")
            pending = await self.pool.fetchrow(
                "SELECT tool_name,tool_args,status,decided_by FROM pending_actions WHERE id=$1",
                command_id,
            )
            if (
                pending is None
                or pending["tool_name"] != "calendar_respond"
                or pending["tool_args"] != arguments
                or pending["status"] not in {"approved", "executed"}
                or not isinstance(pending["decided_by"], str)
                or not pending["decided_by"].startswith("human:")
                or pending["decided_by"] != context.actor
            ):
                raise CalendarResponseError("response_approval_mismatch")
            admitted_command_id = command_id
            if payload["phase"] != "prepared":
                return _object(row["action_result"]) or {
                    "status": "uncertain",
                    "command_id": str(command_id),
                }
            command = payload["command"]
            if (
                command.get("owner") != self.module._butler_name
                or _digest(command) != payload["digest"]
            ):
                raise CalendarResponseError("response_command_owner_changed")
            target, account = await self._target(command["arguments"]["entry_id"])
            if (
                account != command["account"]
                or str(target["source_id"]) != command["source_id"]
                or str(target["event_id"]) != command["event_id"]
                or target["source_key"] != command["source_key"]
                or target["calendar_id"] != command["calendar_id"]
            ):
                raise CalendarResponseError("response_source_changed")
            current = await self._snapshot(target, account)
            desired = command["arguments"]["response_status"]
            if current.response_status == desired and current == SelfResponseSnapshot(
                **command["before"]
            ):
                result = {
                    "status": "noop",
                    "command_id": str(command_id),
                    "projection_available": True,
                }
                await self._finish(command_id, "noop", result)
                return result
            if current.to_dict() != command["before"]:
                raise CalendarResponseError("response_version_changed")
            provider = self.module._require_provider()
            token = await provider.prepare_response_write()
            replay = await self._claim(command_id, payload)
            if replay is not None:
                return replay
            try:
                resource = await provider.respond_to_invitation(
                    calendar_id=command["calendar_id"],
                    before=current,
                    response_status=desired,
                    send_updates=command["arguments"]["send_updates"],
                    access_token=token,
                )
                after = verified_response_result(resource, before=current, response_status=desired)
            except asyncio.CancelledError:
                # The committed start marker itself is the durable uncertainty
                # fence even if cancellation prevents this best-effort receipt.
                raise
            except CalendarResponseUncertainError:
                result = {
                    "status": "uncertain",
                    "command_id": str(command_id),
                    "reason": "response_result_unverified",
                    "projection_available": False,
                }
                await self._finish(command_id, "uncertain", result)
                return result
            except Exception as exc:
                # Only the dedicated provider's fixed explicit rejection type
                # carries an attributable rejection; arbitrary failures remain
                # uncertain after the committed marker.
                from butlers.modules.calendar import CalendarRequestError

                known_rejection = (
                    isinstance(exc, CalendarRequestError)
                    and 400 <= exc.status_code < 500
                    and exc.status_code != 408
                )
                status = "failed" if known_rejection else "uncertain"
                result = {
                    "status": status,
                    "command_id": str(command_id),
                    "reason": "response_provider_rejected"
                    if known_rejection
                    else "response_provider_uncertain",
                    "projection_available": False,
                }
                await self._finish(command_id, status, result)
                return result
            result = {
                "status": "applied",
                "command_id": str(command_id),
                "before": current.to_dict(),
                "after": after.to_dict(),
                "projection_available": False,
            }
            # Commit attributable provider success before attempting projection.
            await self._finish(command_id, "applied", result)
            try:
                from butlers.modules.calendar import _google_event_to_calendar_event

                projected = _google_event_to_calendar_event(
                    resource, fallback_timezone=self.module._config.timezone
                )
                if projected is None:
                    raise CalendarResponseError("response_projection_unavailable")
                await self.module._project_provider_changes(
                    source_id=uuid.UUID(command["source_id"]),
                    provider_name="google",
                    calendar_id=command["calendar_id"],
                    updated_events=[projected],
                    cancelled_ids=[],
                )
            except Exception:
                return result
            result["projection_available"] = True
            await self._finish(command_id, "applied", result)
            return result
        except CalendarResponseError as exc:
            if admitted_command_id is not None:
                try:
                    await self._finish(
                        admitted_command_id,
                        "failed",
                        {
                            "status": "failed",
                            "command_id": str(admitted_command_id),
                            "reason": exc.code,
                            "projection_available": False,
                        },
                    )
                except Exception:
                    return {"error": "response_command_unavailable"}
            return {"error": exc.code}
        except Exception:
            # SQL/provider diagnostics never become executor result text. A
            # previously committed start marker remains intact on this path.
            return {"error": "response_command_unavailable"}


async def reserve_response_inverse(pool: Any, action_id: uuid.UUID) -> dict[str, str]:
    """Reserve one server-owned inverse; no caller status/version/reference.

    A reservation never creates execution authority. The same registered public
    operation still prepares, parks and receives a separate verified decision.
    Its public status is the original confirmed post-status; the private command
    takes the retained prior status, including needsAction, from this receipt.
    """
    async with pool.acquire() as connection, connection.transaction():
        row = await connection.fetchrow(_COMMAND_SQL + " FOR UPDATE", action_id)
        if (
            row is None
            or row["action_type"] != "workspace_user_respond"
            or row["action_status"] != "applied"
        ):
            raise CalendarResponseError("response_inverse_unearned")
        receipt = _object(row["action_result"])
        payload = _object(row["action_payload"])
        command = _object(payload.get("command"))
        if receipt.get("undo") or payload.get("inverse_claim") or command.get("inverse_of"):
            raise CalendarResponseError("response_inverse_used")
        before, after = _object(receipt.get("before")), _object(receipt.get("after"))
        if (
            not isinstance(before.get("response_status"), str)
            or before.get("response_status")
            not in {
                "needsAction",
                "accepted",
                "declined",
                "tentative",
            }
            or not isinstance(after.get("response_status"), str)
            or after.get("response_status") not in {"accepted", "declined", "tentative"}
        ):
            raise CalendarResponseError("response_inverse_unavailable")
        request_id = str(uuid.uuid4())
        command_id = uuid.uuid4()
        arguments = dict(
            command["arguments"], request_id=request_id, response_status=after["response_status"]
        )
        reservation = {
            "phase": "reserved",
            "inverse_of": str(action_id),
            "request_digest": _digest(arguments),
        }
        await connection.execute(
            "INSERT INTO calendar_action_log "
            "(id,idempotency_key,request_id,action_type,action_payload) "
            "VALUES ($1,$2,$3,'workspace_response_inverse_reservation',$4)",
            command_id,
            "respond:" + request_id,
            request_id,
            reservation,
        )
        await connection.execute(
            "UPDATE calendar_action_log SET action_payload=action_payload || "
            "$2::jsonb,updated_at=now() WHERE id=$1",
            action_id,
            {"inverse_claim": str(command_id)},
        )
    return arguments
