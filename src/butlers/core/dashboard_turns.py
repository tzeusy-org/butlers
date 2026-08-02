"""Durable control-plane helpers for one dashboard conversation turn.

The dashboard API, Switchboard, target butlers, and their spawners run in
different processes and schemas.  These helpers deliberately call the small
``public.dashboard_turn_*`` SECURITY DEFINER surface rather than reading or
writing the control tables directly.  That gives a dashboard message one
monotonic cancellation authority across routing handoff and route-inbox
recovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

import asyncpg

DashboardTurnPhase = Literal["classification", "route"]
DashboardTurnTerminalState = Literal["completed", "failed"]


@dataclass(frozen=True)
class DashboardTurnResult:
    """One authoritative dashboard-turn state transition result."""

    outcome: str
    message_id: UUID | None
    conversation_id: UUID | None
    request_id: UUID | None
    target_butler: str | None
    target_kind: str | None
    route_inbox_id: UUID | None
    cancel_requested_at: datetime | None
    cancel_confirmed_at: datetime | None
    terminal_state: str | None
    terminal_at: datetime | None

    @property
    def ingress_is_claimed(self) -> bool:
        """Return whether this caller owns the one allowed ingress submission."""
        return self.outcome == "dispatch"

    @property
    def cancellation_requested(self) -> bool:
        """Return whether the durable cancel bit won this transition."""
        return self.outcome in {"cancelled", "cancelling"} or self.cancel_requested_at is not None


@dataclass(frozen=True)
class DashboardTurnSession:
    """A session durably linked to a dashboard message turn."""

    message_id: UUID
    session_id: UUID
    butler_name: str
    phase: DashboardTurnPhase
    registered_at: datetime | None
    invoke_claimed_at: datetime | None
    invoke_active: bool


_Queryable = asyncpg.Pool | asyncpg.Connection


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _as_turn_result(row: Any | None) -> DashboardTurnResult:
    """Normalize an asyncpg record or mapping returned by a control function."""
    if row is None:
        return DashboardTurnResult(
            outcome="missing",
            message_id=None,
            conversation_id=None,
            request_id=None,
            target_butler=None,
            target_kind=None,
            route_inbox_id=None,
            cancel_requested_at=None,
            cancel_confirmed_at=None,
            terminal_state=None,
            terminal_at=None,
        )

    def _value(name: str) -> Any:
        return row.get(name) if hasattr(row, "get") else row[name]

    return DashboardTurnResult(
        outcome=str(_value("outcome")),
        message_id=_as_uuid(_value("message_id")),
        conversation_id=_as_uuid(_value("conversation_id")),
        request_id=_as_uuid(_value("request_id")),
        target_butler=str(_value("target_butler")) if _value("target_butler") else None,
        target_kind=str(_value("target_kind")) if _value("target_kind") else None,
        route_inbox_id=_as_uuid(_value("route_inbox_id")),
        cancel_requested_at=_value("cancel_requested_at"),
        cancel_confirmed_at=_value("cancel_confirmed_at"),
        terminal_state=str(_value("terminal_state")) if _value("terminal_state") else None,
        terminal_at=_value("terminal_at"),
    )


async def open_turn(
    conn: _Queryable,
    *,
    message_id: UUID,
    conversation_id: UUID,
) -> DashboardTurnResult:
    """Create or load the durable dashboard turn before SSE begins."""
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_open($1, $2)", message_id, conversation_id
    )
    return _as_turn_result(row)


async def claim_ingress(
    conn: _Queryable,
    *,
    message_id: UUID,
) -> DashboardTurnResult:
    """Claim the outbound Switchboard submission at its final safe boundary."""
    row = await conn.fetchrow("SELECT * FROM public.dashboard_turn_claim_ingress($1)", message_id)
    return _as_turn_result(row)


async def bind_ingress(
    conn: _Queryable,
    *,
    message_id: UUID,
    request_id: UUID,
) -> DashboardTurnResult:
    """Associate the canonical Switchboard request id after accepted ingress."""
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_bind_ingress($1, $2)", message_id, request_id
    )
    return _as_turn_result(row)


async def record_ingress_failure(
    conn: _Queryable,
    *,
    message_id: UUID,
    state: Literal["retryable_error", "rejected"],
    detail: str,
) -> DashboardTurnResult:
    """Persist a truthful ingress outcome without clearing a cancel intent."""
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_record_ingress_failure($1, $2, $3)",
        message_id,
        state,
        detail,
    )
    return _as_turn_result(row)


async def claim_target(
    conn: _Queryable,
    *,
    message_id: UUID,
    request_id: UUID,
    target_butler: str,
) -> DashboardTurnResult:
    """Atomically claim the one domain target before route-inbox enqueue."""
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_claim_target($1, $2, $3)",
        message_id,
        request_id,
        target_butler,
    )
    return _as_turn_result(row)


async def mark_route_enqueued(
    conn: _Queryable,
    *,
    message_id: UUID,
    route_inbox_id: UUID,
) -> DashboardTurnResult:
    """Record the route inbox row in the same transaction as its insertion."""
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_mark_route_enqueued($1, $2)",
        message_id,
        route_inbox_id,
    )
    return _as_turn_result(row)


async def register_session_and_check_cancel(
    conn: _Queryable,
    *,
    message_id: UUID,
    session_id: UUID,
    request_id: UUID,
    butler_name: str,
    phase: DashboardTurnPhase,
) -> DashboardTurnResult:
    """Publish a pending session and atomically observe durable cancellation."""
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_register_session($1, $2, $3, $4, $5)",
        message_id,
        session_id,
        request_id,
        butler_name,
        phase,
    )
    return _as_turn_result(row)


async def reconcile_route_recovery(
    conn: _Queryable,
    *,
    message_id: UUID,
    request_id: UUID,
    route_inbox_id: UUID,
) -> DashboardTurnResult:
    """Mark a route predecessor with an unprovable stale lease as ambiguous.

    Callers must hold the corresponding ``route_inbox`` processing lease.  The
    SQL control function additionally binds the operation to this target,
    request, and inbox id.  A stale lease is not proof that an already-started
    runtime died, so dashboard recovery must not replay it automatically.
    """
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_reconcile_route_recovery($1, $2, $3)",
        message_id,
        request_id,
        route_inbox_id,
    )
    return _as_turn_result(row)


async def claim_invoke(
    conn: _Queryable,
    *,
    message_id: UUID,
    session_id: UUID,
) -> DashboardTurnResult:
    """Atomically claim the pre-invocation boundary or observe a prior Stop."""
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_claim_invoke($1, $2)",
        message_id,
        session_id,
    )
    return _as_turn_result(row)


async def release_invoke(
    conn: _Queryable,
    *,
    message_id: UUID,
    session_id: UUID,
) -> DashboardTurnResult:
    """Mark one runtime attempt no longer live before any retry can begin."""
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_release_invoke($1, $2)",
        message_id,
        session_id,
    )
    return _as_turn_result(row)


async def complete_session(
    conn: _Queryable,
    *,
    message_id: UUID,
    session_id: UUID,
    success: bool,
) -> DashboardTurnResult:
    """Close a linked session and advance a terminal turn when appropriate."""
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_complete_session($1, $2, $3)",
        message_id,
        session_id,
        success,
    )
    return _as_turn_result(row)


async def claim_external_action(
    conn: _Queryable,
    *,
    message_id: UUID,
    request_id: UUID,
    kind: Literal["bug_report", "dead_letter"],
) -> DashboardTurnResult:
    """Claim an at-most-once terminal side effect or observe a prior Stop."""
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_claim_external_action($1, $2, $3)",
        message_id,
        request_id,
        kind,
    )
    return _as_turn_result(row)


async def claim_bug_report(
    conn: _Queryable,
    *,
    message_id: UUID,
    request_id: UUID,
) -> DashboardTurnResult:
    """Claim the irreversible QA-report commit point or observe a prior Stop."""
    return await claim_external_action(
        conn,
        message_id=message_id,
        request_id=request_id,
        kind="bug_report",
    )


async def claim_dead_letter(
    conn: _Queryable,
    *,
    message_id: UUID,
    request_id: UUID,
) -> DashboardTurnResult:
    """Claim a dead-letter/reply terminal side effect or observe a prior Stop."""
    return await claim_external_action(
        conn,
        message_id=message_id,
        request_id=request_id,
        kind="dead_letter",
    )


async def mark_terminal(
    conn: _Queryable,
    *,
    message_id: UUID,
    state: DashboardTurnTerminalState,
) -> DashboardTurnResult:
    """Mark a non-session dashboard lane terminal without erasing cancellation."""
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_mark_terminal($1, $2)", message_id, state
    )
    return _as_turn_result(row)


async def request_cancel(
    conn: _Queryable,
    *,
    message_id: UUID,
) -> DashboardTurnResult:
    """Commit monotonic cancellation intent before any MCP cancellation calls."""
    row = await conn.fetchrow("SELECT * FROM public.dashboard_turn_request_cancel($1)", message_id)
    return _as_turn_result(row)


async def acknowledge_cancel(
    conn: _Queryable,
    *,
    message_id: UUID,
    session_id: UUID,
) -> DashboardTurnResult:
    """Persist that one invoking runtime accepted the owner's Stop request."""
    row = await conn.fetchrow(
        "SELECT * FROM public.dashboard_turn_acknowledge_cancel($1, $2)",
        message_id,
        session_id,
    )
    return _as_turn_result(row)


async def confirm_cancel(
    conn: _Queryable,
    *,
    message_id: UUID,
) -> DashboardTurnResult:
    """Persist an MCP-confirmed runtime cancellation as the terminal outcome."""
    row = await conn.fetchrow("SELECT * FROM public.dashboard_turn_confirm_cancel($1)", message_id)
    return _as_turn_result(row)


async def live_sessions(
    conn: _Queryable,
    *,
    message_id: UUID,
) -> list[DashboardTurnSession]:
    """Return every published session that may still need a runtime cancel."""
    rows = await conn.fetch("SELECT * FROM public.dashboard_turn_live_sessions($1)", message_id)
    sessions: list[DashboardTurnSession] = []
    for row in rows:
        session_id = _as_uuid(row["session_id"])
        if session_id is None:
            raise RuntimeError("dashboard turn session id is missing")
        phase = str(row["phase"])
        if phase not in {"classification", "route"}:
            raise RuntimeError(f"invalid dashboard turn session phase: {phase!r}")
        sessions.append(
            DashboardTurnSession(
                message_id=_as_uuid(row["message_id"]) or message_id,
                session_id=session_id,
                butler_name=str(row["butler_name"]),
                phase=cast(DashboardTurnPhase, phase),
                registered_at=row["registered_at"],
                invoke_claimed_at=row.get("invoke_claimed_at") if hasattr(row, "get") else None,
                invoke_active=bool(row.get("invoke_active")) if hasattr(row, "get") else False,
            )
        )
    return sessions


async def dispatch_status(
    conn: _Queryable,
    *,
    message_id: UUID,
) -> DashboardTurnResult:
    """Check whether a dashboard tool may still begin a side effect."""
    row = await conn.fetchrow("SELECT * FROM public.dashboard_turn_dispatch_status($1)", message_id)
    return _as_turn_result(row)


__all__ = [
    "DashboardTurnPhase",
    "DashboardTurnResult",
    "DashboardTurnSession",
    "acknowledge_cancel",
    "bind_ingress",
    "claim_ingress",
    "claim_dead_letter",
    "claim_bug_report",
    "claim_external_action",
    "claim_invoke",
    "claim_target",
    "complete_session",
    "confirm_cancel",
    "dispatch_status",
    "live_sessions",
    "mark_route_enqueued",
    "mark_terminal",
    "open_turn",
    "record_ingress_failure",
    "reconcile_route_recovery",
    "release_invoke",
    "register_session_and_check_cancel",
    "request_cancel",
]
