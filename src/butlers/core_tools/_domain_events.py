"""Domain-event bus tools: publish/subscribe/unsubscribe/receive.

bu-ep4ks.10 (2026-07-25 JARVIS pursuit dossier, ranked move #10). See
``src/butlers/core/domain_events.py`` for the shared writer/reader,
``src/butlers/core/domain_event_wake.py`` for the subscriber-local task
reconciliation, and ``alembic/versions/core/core_186_domain_events.py`` for
the tables.

Registered fleet-wide (non-STAFFER only, mirroring ``notify``/``delegate_*``
in ``_notifications.py``/``_delegation.py``) behind the reserved
``domain_events`` core group, so any domain butler can publish a standing
event, subscribe to another butler's event vocabulary, and receive the
fan-out wake for one it subscribed to.

Fan-out always goes through the Switchboard's existing ``route()``
primitive -- via ``daemon.switchboard_client.call_tool("route", ...)`` for
every butler except Switchboard itself, which calls the underlying
``route()`` function directly in-process -- exactly mirroring
``_delegation._dispatch_via_switchboard``'s client-vs-self-delivery split.
``fan_out_event`` below is the shared record-then-dispatch sequence a
deterministic caller can also invoke directly (mirrors ``dispatch_
delegated_ask``); see ``publish_domain_event`` for the convenience wrapper
``roster/travel/tools/bookings.py`` calls after a new trip is booked.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Annotated, Any

from pydantic import Field

from butlers.config import ButlerType
from butlers.core.domain_event_wake import handle_receive_domain_event
from butlers.core.domain_events import (
    claim_delivery,
    get_active_subscribers,
    is_valid_event_type,
    list_subscriptions,
    mark_delivery_conflict,
    mark_delivery_delivered,
    mark_delivery_failed,
    record_event,
    remove_subscription,
    upsert_subscription,
)
from butlers.core.telemetry import tool_span
from butlers.core_tools._base import ToolContext

logger = logging.getLogger(__name__)

_ROUTE_TIMEOUT_S = 30


def _extract_mcp_error_text(result: Any) -> str:
    """Best-effort extraction of MCP error text from a CallToolResult."""
    content = getattr(result, "content", None) or []
    if content:
        first = content[0]
        return str(getattr(first, "text", "") or first)
    return "route tool returned an error"


async def _dispatch_receive_via_switchboard(
    client: Any,
    pool: Any,
    butler_name: str,
    *,
    target_butler: str,
    args: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, bool]:
    """Dispatch one ``receive_domain_event`` call through Switchboard ``route()``.

    Mirrors ``_delegation._dispatch_via_switchboard`` exactly, except it also
    returns the target tool's own result payload on success (the fan-out
    ledger needs the subscriber's reconciliation outcome -- ``task_created``
    vs ``task_conflict`` -- not just "route() succeeded").

    Returns ``(data, error_text, retryable)``. ``error_text`` is ``None`` on
    a successful dispatch.
    """
    route_tool_args = {
        "target_butler": target_butler,
        "tool_name": "receive_domain_event",
        "args": args,
        "source_butler": butler_name,
    }

    if client is not None:
        try:
            result = await asyncio.wait_for(
                client.call_tool("route", route_tool_args),
                timeout=_ROUTE_TIMEOUT_S,
            )
        except TimeoutError:
            return None, f"Switchboard route() call timed out after {_ROUTE_TIMEOUT_S}s.", True
        except (ConnectionError, OSError) as exc:
            return None, f"Switchboard unreachable: {exc}", True
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}", False

        if result.is_error:
            return None, _extract_mcp_error_text(result), False
        data = result.data
        if isinstance(data, dict) and data.get("status") == "error":
            return None, str(data.get("error") or "receive_domain_event returned an error."), False
        return (data if isinstance(data, dict) else None), None, False

    if butler_name == "switchboard":
        from butlers.tools.switchboard.routing.route import route as _switchboard_route

        try:
            raw = await _switchboard_route(
                pool,
                target_butler,
                "receive_domain_event",
                args,
                source_butler=butler_name,
            )
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}", False

        if isinstance(raw, dict) and raw.get("status") == "error":
            return None, str(raw.get("error") or "receive_domain_event returned an error."), False
        return (raw if isinstance(raw, dict) else None), None, False

    return (
        None,
        "Switchboard is not connected. Cannot route the fan-out dispatch. "
        "This is a transient infrastructure issue — retry after a delay.",
        True,
    )


async def fan_out_event(
    pool: Any,
    switchboard_client: Any,
    *,
    event_id: str,
    event_type: str,
    source_butler: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch one published event to every active subscriber, idempotently.

    Safe to call more than once for the same ``event_id`` (a caller retry, or
    a future periodic reconciliation sweep over undelivered deliveries):
    ``claim_delivery`` is the atomic per-(event, subscriber) claim, so a
    ``delivered`` outcome is never re-dispatched, while a ``pending``/
    ``failed`` outcome is retried in place rather than duplicated.

    The publishing butler is never its own subscriber's target here (self-
    delivery would just wake the same in-flight session that already knows
    what it published).

    Returns ``{"event_id": ..., "deliveries": [{"subscriber_butler": ..., "status": ...}, ...]}``.
    A per-subscriber dispatch failure is recorded in the delivery ledger and
    reported back in this result; it never raises past the caller (the
    publish itself already durably succeeded before fan-out runs).
    """
    subscribers = await get_active_subscribers(pool, event_type)
    outcomes: list[dict[str, Any]] = []

    for subscriber_butler in subscribers:
        if subscriber_butler == source_butler:
            continue

        delivery = await claim_delivery(
            pool, event_id=event_id, subscriber_butler=subscriber_butler
        )
        if delivery["status"] == "delivered":
            outcomes.append({"subscriber_butler": subscriber_butler, "status": "delivered"})
            continue

        data, route_error, retryable = await _dispatch_receive_via_switchboard(
            switchboard_client,
            pool,
            source_butler,
            target_butler=subscriber_butler,
            args={
                "event_id": event_id,
                "event_type": event_type,
                "source_butler": source_butler,
                "payload": payload,
            },
        )

        if route_error is not None:
            try:
                await mark_delivery_failed(pool, delivery["id"], route_error)
            except Exception:
                logger.warning(
                    "fan_out_event: failed to record delivery failure for event_id=%s "
                    "subscriber_butler=%s",
                    event_id,
                    subscriber_butler,
                    exc_info=True,
                )
            outcomes.append(
                {
                    "subscriber_butler": subscriber_butler,
                    "status": "failed",
                    "error": route_error,
                    "retryable": retryable,
                }
            )
            continue

        state = (data or {}).get("state")
        if state == "task_conflict":
            try:
                await mark_delivery_conflict(pool, delivery["id"])
            except Exception:
                logger.warning(
                    "fan_out_event: failed to record delivery conflict for event_id=%s "
                    "subscriber_butler=%s",
                    event_id,
                    subscriber_butler,
                    exc_info=True,
                )
            outcomes.append({"subscriber_butler": subscriber_butler, "status": "conflict"})
            continue

        task_id = (data or {}).get("task_id")
        task_name = (data or {}).get("task_name")
        if not task_id or not task_name:
            # The receiving tool returned success but not the fields needed
            # to record what it did -- an honest failure, not a fabricated
            # "delivered".
            error_text = (
                f"receive_domain_event on {subscriber_butler!r} returned an incomplete "
                f"success payload: {data!r}"
            )
            try:
                await mark_delivery_failed(pool, delivery["id"], error_text)
            except Exception:
                logger.warning(
                    "fan_out_event: failed to record incomplete-success failure for "
                    "event_id=%s subscriber_butler=%s",
                    event_id,
                    subscriber_butler,
                    exc_info=True,
                )
            outcomes.append(
                {"subscriber_butler": subscriber_butler, "status": "failed", "error": error_text}
            )
            continue

        try:
            await mark_delivery_delivered(
                pool, delivery["id"], task_id=task_id, task_name=task_name
            )
        except Exception:
            logger.warning(
                "fan_out_event: failed to record delivery success for event_id=%s "
                "subscriber_butler=%s",
                event_id,
                subscriber_butler,
                exc_info=True,
            )
        outcomes.append({"subscriber_butler": subscriber_butler, "status": "delivered"})

    return {"event_id": event_id, "deliveries": outcomes}


def _invalid_event_type_error(event_type: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error": (
            f"event_type={event_type!r} must match '<namespace>.<event>' "
            "(lowercase, e.g. 'travel.trip_booked')."
        ),
    }


async def publish_domain_event(
    pool: Any,
    switchboard_client: Any,
    *,
    event_type: str,
    source_butler: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record one event and fan it out to every active subscriber.

    The shared record-then-fan-out sequence behind the ``publish_event`` MCP
    tool below, factored out so a deterministic caller with its own pool/
    switchboard_client (e.g. ``roster/travel/tools/bookings.py`` after a new
    trip is booked) can publish without going through the MCP layer at all --
    mirrors ``_delegation.dispatch_delegated_ask``.
    """
    if not is_valid_event_type(event_type):
        return _invalid_event_type_error(event_type)

    event_id = await record_event(
        pool, event_type=event_type, source_butler=source_butler, payload=payload
    )
    fanout = await fan_out_event(
        pool,
        switchboard_client,
        event_id=event_id,
        event_type=event_type,
        source_butler=source_butler,
        payload=payload or {},
    )
    return {"status": "ok", "event_id": event_id, "deliveries": fanout["deliveries"]}


async def _claim_and_record_event(
    pool: Any,
    *,
    state_key: str,
    dedup_key: str,
    event_type: str,
    source_butler: str,
    payload: dict[str, Any] | None,
) -> str | None:
    """Atomically claim *state_key* for *dedup_key* and, only if the claim is
    won, durably record the event -- both in one Postgres transaction.

    This is the fix for a check-then-act race in the old
    ``publish_domain_event_once`` (state_get the last key, publish, state_set
    the new key): two overlapping callers for the same dedup_key could both
    read the pre-update value and both publish. ``state_claim_if_changed``
    (see ``butlers.core.state``) is a single atomic ``INSERT ... ON CONFLICT
    DO UPDATE ... WHERE value IS DISTINCT FROM ... RETURNING`` claim -- of two
    concurrent callers racing the same ``(state_key, dedup_key)`` pair,
    exactly one gets ``True`` back (mirrors ``claim_delivery``'s atomic
    claim-before-dispatch, generalized from "row absent" to "value unchanged").

    Performing the event-log insert (:func:`record_event`) on the *same*
    connection, inside the *same* transaction as the claim, means the two
    outcomes commit atomically together: either both the claim and the event
    row land, or neither does (a raised exception inside the ``async with``
    rolls the claim back too) -- so a losing claim never leaves an orphaned
    event, and a winning claim never leaves the dedup key pointing at an
    event that was never recorded. Fan-out (:func:`fan_out_event`) happens
    *outside* this transaction, on the caller's own pool, deliberately -- it
    makes network calls (via ``switchboard_client``) that must not hold a DB
    transaction/row-lock open, and its own failure surface (a single
    subscriber's dispatch failing) is already handled per-subscriber via the
    delivery ledger (``claim_delivery``/``mark_delivery_failed``), exactly as
    for the ordinary ``publish_domain_event`` path -- it never risks a lost
    or duplicated *event*, only a fan-out that a caller/reconciliation sweep
    may need to retry against the now-durably-recorded event_id.

    Returns the new event_id, or ``None`` if the claim was lost (dedup_key
    unchanged since the last successful claim for this state_key).
    """
    from butlers.core.state import state_claim_if_changed

    async with pool.acquire() as conn, conn.transaction():
        claimed = await state_claim_if_changed(conn, state_key, dedup_key)
        if not claimed:
            return None
        return await record_event(
            conn, event_type=event_type, source_butler=source_butler, payload=payload
        )


async def publish_domain_event_once(
    pool: Any,
    switchboard_client: Any,
    *,
    event_type: str,
    source_butler: str,
    dedup_namespace: str,
    dedup_key: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Publish *event_type* unless *dedup_key* was the last one published for
    *dedup_namespace*.

    For a deterministic, recurring producer (a scheduled job re-evaluating the
    same condition every run -- e.g. Finance's ``budget_pressure`` or Health's
    ``recovery_state`` derived advisory, or Travel's ``trip_active`` transition
    detector) calling :func:`publish_domain_event` unconditionally would
    re-publish -- and re-wake every subscriber -- on every single run for as
    long as the condition holds, rather than once per distinct occurrence.
    This wraps the publish with a state-store-memoized "last key I published
    for this namespace" check (mirrors the deterministic idempotence pattern
    ``butlers.jobs.context_producers`` and the insight broker's ``dedup_key``
    both use, applied here to the fan-out side instead of the owner-
    notification side).

    ``dedup_namespace`` scopes the memory (e.g. ``"travel.trip_active"`` or
    ``f"finance.budget_pressure:{category}"``); ``dedup_key`` is the value that
    must change for a new publish to occur (e.g. a trip id, or a
    ``f"{period_scope}-{status}"`` token). Uses the caller's own pool's
    ``state`` table (per-butler-schema KV store) -- never a sibling schema's.

    The dedup claim is atomic (see :func:`_claim_and_record_event`): two
    overlapping invocations for the same dedup_namespace/dedup_key (e.g.
    overlapping consecutive cron occurrences, or a dashboard run-now racing
    cron) will have exactly one claim and publish; the other observes the
    claim as already taken and returns ``None`` without publishing.

    Returns ``None`` when skipped as a duplicate of the last publish for this
    namespace; otherwise the :func:`publish_domain_event`-shaped result
    (``{"status": "ok", "event_id": ..., "deliveries": [...]}``).
    """
    if not is_valid_event_type(event_type):
        return _invalid_event_type_error(event_type)

    state_key = f"domain_event_once:{event_type}:{dedup_namespace}"
    event_id = await _claim_and_record_event(
        pool,
        state_key=state_key,
        dedup_key=dedup_key,
        event_type=event_type,
        source_butler=source_butler,
        payload=payload,
    )
    if event_id is None:
        return None

    fanout = await fan_out_event(
        pool,
        switchboard_client,
        event_id=event_id,
        event_type=event_type,
        source_butler=source_butler,
        payload=payload or {},
    )
    return {"status": "ok", "event_id": event_id, "deliveries": fanout["deliveries"]}


def register_domain_event_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register domain-event-bus tools: publish/subscribe/unsubscribe/list/receive.

    Registered for every non-STAFFER butler (same gate as ``delegate_*``)
    since any domain butler may publish a standing event, subscribe to
    another butler's event vocabulary, or be the fan-out target of one it
    subscribed to.
    """
    if ctx.butler_type == ButlerType.STAFFER:
        return

    daemon = ctx.daemon
    pool = ctx.pool
    butler_name = ctx.butler_name

    @_core_tool("domain_events")
    @tool_span("publish_event", butler_name=butler_name)
    async def publish_event(
        event_type: Annotated[
            str,
            Field(
                description=(
                    "Namespaced event type, '<namespace>.<event>' (e.g. 'travel.trip_booked'). "
                    "Open vocabulary -- no registration step needed, any butler can mint a new "
                    "event_type its subscribers know to look for."
                )
            ),
        ],
        payload: Annotated[
            dict[str, Any],
            Field(description="Event data. Delivered to subscribers as fenced reference data."),
        ],
    ) -> dict:
        """Publish a standing domain event and fan it out to every active subscriber.

        Durably recorded in ``public.domain_events`` regardless of fan-out
        outcome — publishing never silently fails, and a per-subscriber
        dispatch failure is recorded in the delivery ledger (status
        ``"failed"``/``"conflict"``) rather than raised back to you or
        dropped.

        Returns ``{"status": "ok", "event_id": ..., "deliveries": [...]}`` --
        one entry per active subscriber with its dispatch outcome.
        """
        return await publish_domain_event(
            pool,
            daemon.switchboard_client,
            event_type=event_type,
            source_butler=butler_name,
            payload=payload,
        )

    @_core_tool("domain_events")
    @tool_span("subscribe_to_event", butler_name=butler_name)
    async def subscribe_to_event(
        event_type: Annotated[
            str,
            Field(description="Namespaced event type to subscribe to, e.g. 'travel.trip_booked'."),
        ],
    ) -> dict:
        """Stand up (or reactivate) a durable subscription to another butler's event type.

        Every future ``publish_event`` call for this ``event_type`` wakes
        this butler via a one-shot scheduled task (see
        ``receive_domain_event``) until you call ``unsubscribe_from_event``.
        """
        if not is_valid_event_type(event_type):
            return {
                "status": "error",
                "error": (
                    f"event_type={event_type!r} must match '<namespace>.<event>' "
                    "(lowercase, e.g. 'travel.trip_booked')."
                ),
            }
        row = await upsert_subscription(pool, subscriber_butler=butler_name, event_type=event_type)
        return {"status": "ok", "subscription": row}

    @_core_tool("domain_events")
    @tool_span("unsubscribe_from_event", butler_name=butler_name)
    async def unsubscribe_from_event(
        event_type: Annotated[str, Field(description="Event type to stop receiving.")],
    ) -> dict:
        """Deactivate a standing subscription. Idempotent — a no-op if you weren't subscribed."""
        existed = await remove_subscription(
            pool, subscriber_butler=butler_name, event_type=event_type
        )
        return {"status": "ok", "existed": existed}

    @_core_tool("domain_events")
    @tool_span("list_my_subscriptions", butler_name=butler_name)
    async def list_my_subscriptions() -> dict:
        """List this butler's subscriptions, active and inactive."""
        rows = await list_subscriptions(pool, subscriber_butler=butler_name)
        return {"status": "ok", "subscriptions": rows}

    @_core_tool("domain_events")
    @tool_span("receive_domain_event", butler_name=butler_name)
    async def receive_domain_event(
        event_id: Annotated[str, Field(description="domain_events row id from publish_event.")],
        event_type: Annotated[str, Field(description="The published event's type.")],
        source_butler: Annotated[str, Field(description="Name of the butler that published it.")],
        payload: Annotated[dict[str, Any], Field(description="The published event's payload.")],
    ) -> dict:
        """Receive a fanned-out domain event routed to this butler via the Switchboard.

        Schedules a one-shot task (fires within ~1 minute) that hands this
        butler's next spawned session the event's fenced payload and asks it
        to take whatever domain action applies (or exit silently). Does not
        act synchronously — like ``delegate_receive``/``notify()``, dispatch
        and reaction are decoupled so this call (and the Switchboard
        ``route()`` call ahead of it) returns quickly.

        Reachable only through Switchboard fan-out for an event_type this
        butler actively subscribes to in practice, but this tool does not
        itself re-verify the subscription — the publisher-side fan-out
        already resolved subscribers before dispatching. Never call this
        directly.
        """
        return await handle_receive_domain_event(
            pool,
            event_id=event_id,
            event_type=event_type,
            source_butler=source_butler,
            payload=payload,
            subscriber_butler=butler_name,
        )
