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

``_dispatch_receive_via_switchboard``'s transport loop (timeout/connection
handling, the client-vs-self-delivery split) now lives in the shared
``_switchboard_route_dispatch.dispatch_via_switchboard_route`` (bu-xthtw),
factored out of what used to be two independently-drifting ~60-line copies
in this file and ``_delegation.py``. This module keeps its own
``_unwrap_route_result`` as the classify callback passed into that shared
core -- the one place the two files deliberately diverge (see its
docstring).

``run_domain_event_reconciliation_sweep`` (bu-1yw6d) is the periodic
reconciliation sweep ``src/butlers/core/domain_events.py``'s module
docstring anticipated: it re-drives ``pending`` deliveries stuck since a
crash and retries ``failed`` deliveries a bounded number of times with
backoff.  A non-blocking session advisory lock serializes the complete
reconciliation sweep across overlapping scheduler/manual invocations; within
the winning sweep, ``claim_delivery`` re-observes each selected row before
dispatch so a live path that settled it in the interim is skipped. It always
runs on the Switchboard daemon (the routing backbone, with fleet-wide
``public.*`` table access) and dispatches via
``_SwitchboardInProcessRouteClient``, which calls the real ``route()``
function in-process for every subscriber -- unlike
``_dispatch_receive_via_switchboard``'s existing self-delivery branch (keyed
on the *event's publisher* being Switchboard), the sweep re-drives deliveries
published by every butler, so it cannot rely on that identity check.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Annotated, Any

from pydantic import Field

from butlers.config import ButlerType
from butlers.core.domain_event_contracts import get_contract_registry
from butlers.core.domain_event_reactions import (
    REPORTABLE_REACTION_STATUSES,
    DomainEventReactionError,
    record_reaction,
)
from butlers.core.domain_event_wake import handle_receive_domain_event
from butlers.core.domain_events import (
    claim_delivery,
    get_active_subscribers,
    get_delivery_status,
    is_valid_event_type,
    list_subscriptions,
    mark_delivery_conflict,
    mark_delivery_delivered,
    mark_delivery_failed,
    record_event,
    remove_subscription,
    select_retryable_failed_deliveries,
    select_stale_pending_deliveries,
    upsert_subscription,
)
from butlers.core.metrics import record_domain_event_delivery_failed_permanent
from butlers.core.telemetry import tool_span
from butlers.core.tool_call_capture import get_current_runtime_session_id
from butlers.core_tools._base import ToolContext
from butlers.core_tools._switchboard_route_dispatch import dispatch_via_switchboard_route

logger = logging.getLogger(__name__)

# Bounded-retry policy for the periodic reconciliation sweep (bu-1yw6d).
#
# - _STALE_PENDING_AFTER: a `pending` delivery this old was claimed but never
#   resolved -- crashed after claim, before dispatch. Comfortably above
#   the shared route dispatch timeout (_switchboard_route_dispatch.
#   ROUTE_TIMEOUT_S, 30s) plus scheduling/retry jitter, so a genuinely
#   in-flight dispatch is never mistaken for a stuck one.
# - _FAILED_RETRY_BACKOFF: minimum time between retry attempts on a `failed`
#   row, so a target that is actually down is not hammered every sweep tick.
#   Fixed (not exponential) -- the sweep's own cadence already rate-limits
#   retries, and the attempt cap below bounds total retries to a short
#   window; exponential backoff would add interval-math complexity for
#   little benefit at this scale.
# - _MAX_DELIVERY_RETRY_ATTEMPTS: attempts beyond this transition a `failed`
#   row to the terminal `failed_permanent` status instead of retrying again
#   -- enough attempts to ride out a typical transient blip (network flap,
#   restart window) without retrying a truly broken route forever.
_STALE_PENDING_AFTER = timedelta(minutes=10)
_FAILED_RETRY_BACKOFF = timedelta(minutes=15)
_MAX_DELIVERY_RETRY_ATTEMPTS = 5
_SWEEP_BATCH_LIMIT = 200
_RECONCILIATION_SWEEP_LOCK_KEY = "public.domain_event_deliveries:reconciliation_sweep"

# route() preserves the old ``{"error": "<ExceptionType>: <message>"}``
# envelope and current Switchboard versions add a literal boolean ``retryable``
# classification while they still have the concrete exception hierarchy. Older
# Switchboard versions and test doubles have only that legacy string, so retain
# the exact historical prefixes only when the structured field is absent.
# Everything else -- notably a RuntimeError from an unknown/unregistered tool
# (the shape a missing `domain_events` core group takes) or a LookupError from
# an absent registry entry -- is permanent: retrying can never succeed.
_RETRYABLE_ROUTE_ERROR_PREFIXES = ("ConnectionError:", "OSError:", "TimeoutError:")


def _is_retryable_route_error_text(error_text: str) -> bool:
    """Classify an unstructured legacy route error as transient or permanent."""
    return error_text.startswith(_RETRYABLE_ROUTE_ERROR_PREFIXES)


async def _dispatch_receive_via_switchboard(
    client: Any,
    pool: Any,
    butler_name: str,
    *,
    target_butler: str,
    args: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, bool]:
    """Dispatch one ``receive_domain_event`` call through Switchboard ``route()``.

    Thin wrapper around the shared transport loop
    (``_switchboard_route_dispatch.dispatch_via_switchboard_route``) using
    this module's own route()-result classification rule
    (``_unwrap_route_result``), which -- unlike
    ``_delegation._classify_delegation_route_result`` -- also returns the
    target tool's own result payload on success (the fan-out ledger needs
    the subscriber's reconciliation outcome -- ``task_created`` vs
    ``task_conflict`` -- not just "route() succeeded").

    Returns ``(data, error_text, retryable)``. ``error_text`` is ``None`` on
    a successful dispatch, and ``data`` is the *unwrapped* target-tool
    payload in that case.
    """
    return await dispatch_via_switchboard_route(
        client,
        pool,
        butler_name,
        target_butler=target_butler,
        tool_name="receive_domain_event",
        args=args,
        classify=_unwrap_route_result,
        route_purpose="fan-out dispatch",
    )


def _unwrap_route_result(raw: Any) -> tuple[dict[str, Any] | None, str | None, bool]:
    """Unwrap a raw ``route()`` return value into ``(data, error_text, retryable)``.

    ``route()`` returns ``{"error": "<ExceptionType>: <message>"}`` on any
    route-level failure (target unreachable, unknown tool, registry lookup),
    adding a literal boolean ``"retryable"`` on current envelopes, or
    ``{"result": <target tool's own return value>}`` on success -- see
    ``_dispatch_receive_via_switchboard``'s docstring. This is the single
    place both of that function's branches (real MCP client vs. Switchboard
    in-process self-delivery) unwrap that envelope, so they can never drift
    out of sync on how a route-level error is detected and classified.
    """
    if not isinstance(raw, dict):
        return None, "route() returned a non-dict result.", False
    if "error" in raw:
        error_text = str(raw["error"])
        if "retryable" in raw:
            retryable = raw["retryable"]
            return None, error_text, retryable if isinstance(retryable, bool) else False
        return None, error_text, _is_retryable_route_error_text(error_text)

    data = raw.get("result")
    if isinstance(data, dict) and data.get("status") == "error":
        return None, str(data.get("error") or "receive_domain_event returned an error."), False
    return (data if isinstance(data, dict) else None), None, False


def _record_failed_permanent_delivery_metric(
    *,
    source_butler: str,
    destination_butler: str,
    retryable: bool,
) -> None:
    """Best-effort telemetry after the ledger committed a terminal transition.

    This hook intentionally sits outside the durable write. An OTel exporter
    outage must not change a successfully committed delivery outcome or cause
    a reconciliation retry to fabricate a second transition.
    """
    reason = "attempts_exhausted" if retryable else "non_retryable"
    try:
        record_domain_event_delivery_failed_permanent(
            source_butler=source_butler,
            destination_butler=destination_butler,
            reason=reason,
        )
    except Exception:
        logger.warning(
            "domain-event failed-permanent metric emission failed: "
            "source_butler=%s destination_butler=%s reason=%s",
            source_butler,
            destination_butler,
            reason,
            exc_info=True,
        )


async def _dispatch_and_record_delivery(
    pool: Any,
    switchboard_client: Any,
    *,
    delivery: dict[str, Any],
    subscriber_butler: str,
    event_id: str,
    event_type: str,
    source_butler: str,
    payload: dict[str, Any],
    max_attempts: int = _MAX_DELIVERY_RETRY_ATTEMPTS,
) -> dict[str, Any]:
    """Dispatch one already-claimed delivery and record its outcome.

    ``delivery`` must already be the current ``claim_delivery()``-shaped row
    for this ``(event, subscriber)`` pair -- callers own the claim/re-observe
    step themselves (both current callers do this immediately before
    invoking this helper, so a delivery that has moved on since it was
    selected as a candidate is never blindly re-dispatched). ``subscriber_
    butler`` is passed explicitly (rather than read off ``delivery``) since
    the caller already knows it from its own candidate row/loop variable.
    ``max_attempts`` must match whatever bound the caller used to *select*
    this delivery as a candidate in the first place (the sweep threads its
    own configurable ``max_attempts`` through here) -- otherwise a delivery
    could be selected under one bound but never actually cross the terminal
    ``failed_permanent`` threshold, which uses this same value.

    Shared by :func:`fan_out_event`'s per-subscriber loop and
    ``run_domain_event_reconciliation_sweep``'s re-drive path -- exactly one
    implementation of "dispatch once, record the outcome," so a sweep retry
    can never diverge from a fresh dispatch's idempotence or
    failure-classification contract.

    Returns ``{"subscriber_butler": ..., "status": ...}`` (plus ``"error"``/
    ``"retryable"`` on failure) -- one outcome entry, never raises.  A newly
    won ``failed_permanent`` transition also carries the private
    ``"_newly_permanently_failed"`` bit for the reconciliation sweep; the
    public fan-out result removes that bookkeeping before returning it.
    """
    if delivery["status"] in {"delivered", "failed_permanent"}:
        return {"subscriber_butler": subscriber_butler, "status": delivery["status"]}

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
        transitioned_status: str | None = None
        failure_write_completed = False
        try:
            transitioned_status = await mark_delivery_failed(
                pool,
                delivery["id"],
                route_error,
                retryable=retryable,
                max_attempts=max_attempts,
            )
            failure_write_completed = True
        except Exception:
            logger.warning(
                "fan_out_event: failed to record delivery failure for event_id=%s "
                "subscriber_butler=%s",
                event_id,
                subscriber_butler,
                exc_info=True,
            )
        newly_permanently_failed = transitioned_status == "failed_permanent"
        if newly_permanently_failed:
            _record_failed_permanent_delivery_metric(
                source_butler=source_butler,
                destination_butler=subscriber_butler,
                retryable=retryable,
            )
        observed_status = transitioned_status
        if failure_write_completed and observed_status is None:
            try:
                observed_status = await get_delivery_status(pool, delivery["id"])
            except Exception:
                logger.warning(
                    "fan_out_event: failed to re-observe terminal delivery state for event_id=%s "
                    "subscriber_butler=%s",
                    event_id,
                    subscriber_butler,
                    exc_info=True,
                )
        status = observed_status or "failed"
        outcome: dict[str, Any] = {
            "subscriber_butler": subscriber_butler,
            "status": status,
            "error": route_error,
        }
        if retryable and transitioned_status == "failed":
            outcome["retryable"] = True
        if newly_permanently_failed:
            outcome["_newly_permanently_failed"] = True
        return outcome

    state = (data or {}).get("state")
    if state == "task_conflict":
        resulting_status: str | None = None
        try:
            resulting_status = await mark_delivery_conflict(pool, delivery["id"])
        except Exception:
            logger.warning(
                "fan_out_event: failed to record delivery conflict for event_id=%s "
                "subscriber_butler=%s",
                event_id,
                subscriber_butler,
                exc_info=True,
            )
        return {
            "subscriber_butler": subscriber_butler,
            "status": resulting_status or "failed",
        }

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
        transitioned_status: str | None = None
        failure_write_completed = False
        try:
            transitioned_status = await mark_delivery_failed(
                pool,
                delivery["id"],
                error_text,
                retryable=False,
                max_attempts=max_attempts,
            )
            failure_write_completed = True
        except Exception:
            logger.warning(
                "fan_out_event: failed to record incomplete-success failure for "
                "event_id=%s subscriber_butler=%s",
                event_id,
                subscriber_butler,
                exc_info=True,
            )
        newly_permanently_failed = transitioned_status == "failed_permanent"
        if newly_permanently_failed:
            _record_failed_permanent_delivery_metric(
                source_butler=source_butler,
                destination_butler=subscriber_butler,
                retryable=False,
            )
        observed_status = transitioned_status
        if failure_write_completed and observed_status is None:
            try:
                observed_status = await get_delivery_status(pool, delivery["id"])
            except Exception:
                logger.warning(
                    "fan_out_event: failed to re-observe terminal delivery state for "
                    "incomplete-success event_id=%s subscriber_butler=%s",
                    event_id,
                    subscriber_butler,
                    exc_info=True,
                )
        outcome = {
            "subscriber_butler": subscriber_butler,
            "status": observed_status or "failed",
            "error": error_text,
        }
        if newly_permanently_failed:
            outcome["_newly_permanently_failed"] = True
        return outcome

    resulting_status: str | None = None
    try:
        resulting_status = await mark_delivery_delivered(
            pool, delivery["id"], task_id=task_id, task_name=task_name
        )
    except Exception:
        logger.warning(
            "fan_out_event: failed to record delivery success for event_id=%s subscriber_butler=%s",
            event_id,
            subscriber_butler,
            exc_info=True,
        )
    return {"subscriber_butler": subscriber_butler, "status": resulting_status or "failed"}


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
    the periodic reconciliation sweep, ``run_domain_event_reconciliation_
    sweep``, over undelivered deliveries): ``claim_delivery`` is the atomic
    per-(event, subscriber) claim, so a ``delivered`` outcome is never
    re-dispatched, while a ``pending``/``failed`` outcome is retried in place
    rather than duplicated.

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
        outcome = await _dispatch_and_record_delivery(
            pool,
            switchboard_client,
            delivery=delivery,
            subscriber_butler=subscriber_butler,
            event_id=event_id,
            event_type=event_type,
            source_butler=source_butler,
            payload=payload,
        )
        outcome.pop("_newly_permanently_failed", None)
        outcomes.append(outcome)

    return {"event_id": event_id, "deliveries": outcomes}


class _SwitchboardInProcessRouteClient:
    """Adapter so the sweep can reuse ``_dispatch_receive_via_switchboard``'s
    normal client-driven branch, unmodified, while running in-process.

    The periodic reconciliation sweep always executes on the Switchboard
    daemon (the routing backbone with fleet-wide ``public.*`` table access),
    so it never needs a real MCP round trip to reach Switchboard's own
    ``route()`` tool -- it can call the underlying ``route()`` function
    directly, exactly mirroring what a real ``client.call_tool("route", ...)``
    would do (see ``roster/switchboard/modules/tools.py``'s ``route`` tool,
    which does nothing but forward to this same function) minus the network
    hop.

    Unlike ``_dispatch_receive_via_switchboard``'s existing ``butler_name ==
    "switchboard"`` self-delivery branch -- which only fires when the
    *event's publisher* is Switchboard itself -- the sweep re-drives
    deliveries published by every butler, so it cannot rely on that identity
    check and instead always dispatches in-process regardless of
    ``source_butler``.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        from types import SimpleNamespace

        from butlers.tools.switchboard.routing.route import route as _switchboard_route

        assert tool_name == "route", f"unexpected tool_name={tool_name!r}"
        result = await _switchboard_route(
            self._pool,
            args["target_butler"],
            args["tool_name"],
            args["args"],
            source_butler=args["source_butler"],
        )
        return SimpleNamespace(is_error=False, data=result)


async def _run_domain_event_reconciliation_sweep_locked(
    connection: Any,
    *,
    stale_pending_after: timedelta = _STALE_PENDING_AFTER,
    failed_retry_backoff: timedelta = _FAILED_RETRY_BACKOFF,
    max_attempts: int = _MAX_DELIVERY_RETRY_ATTEMPTS,
    limit: int = _SWEEP_BATCH_LIMIT,
) -> dict[str, Any]:
    """Periodic reconciliation sweep for stuck domain-event-bus deliveries.

    ``claim_delivery``/``mark_delivery_*`` make a single retry idempotent,
    but nothing re-drove a delivery row stuck at ``pending`` (crashed after
    claim, before dispatch) or ``failed`` (a route error) until this landed
    (bu-1yw6d) -- previously the only recovery path was a manual
    ``publish_event`` retry.

    Two independent passes, both reusing ``claim_delivery``'s exact
    idempotent claim-or-observe primitive so a delivery that has already
    resolved by a live in-flight dispatch is re-observed and skipped rather
    than blindly re-dispatched.  The public wrapper serializes concurrent
    sweep runs before this helper begins:

    1. **Stale pending** (:func:`butlers.core.domain_events.
       select_stale_pending_deliveries`): a ``pending`` row untouched for
       longer than *stale_pending_after* is presumptively an abandoned claim
       (the claiming process crashed before recording an outcome) rather than
       a delivery genuinely still in flight -- a live dispatch completes (one
       way or the other) well inside that window. Re-claiming and
       re-observing it immediately before dispatch is what protects against
       the case where it resolved in the interim between selection and
       processing.
    2. **Retryable failed** (:func:`butlers.core.domain_events.
       select_retryable_failed_deliveries`): a ``failed`` row with
       ``attempt_count < max_attempts`` and at least *failed_retry_backoff*
       since the last attempt gets one more dispatch attempt. A route error
       classified permanent (see ``_is_retryable_route_error_text`` --
       e.g. the subscriber lacks the ``domain_events`` core group) or one
       that exhausts ``max_attempts`` transitions the row to the terminal
       ``failed_permanent`` status instead of retrying it forever; each such
       transition is logged at ``ERROR`` so a permanently-undeliverable
       fan-out is surfaced, not silently dropped (it also remains visible via
       ``GET /api/domain-events/deliveries?status=failed_permanent``).

    Both passes dispatch via ``_SwitchboardInProcessRouteClient`` (this sweep
    always runs on the Switchboard daemon) and share
    ``_dispatch_and_record_delivery`` with a fresh publish's dispatch path --
    there is exactly one implementation of "dispatch once, record the
    outcome."

    Returns a summary dict of candidate/outcome counts (never raises; a
    per-delivery dispatch failure is recorded in the ledger, not propagated).
    """
    client = _SwitchboardInProcessRouteClient(connection)

    stale_pending = await select_stale_pending_deliveries(
        connection, older_than=stale_pending_after, limit=limit
    )
    pending_redriven = 0
    pending_now_delivered = 0
    for row in stale_pending:
        delivery = await claim_delivery(
            connection, event_id=row["event_id"], subscriber_butler=row["subscriber_butler"]
        )
        if delivery["status"] != "pending":
            # Resolved by a concurrent dispatch/sweep since it was selected as
            # a candidate -- claim_delivery's idempotent re-observe is what
            # keeps this safe; never re-dispatch a delivery that has moved on.
            continue
        outcome = await _dispatch_and_record_delivery(
            connection,
            client,
            delivery=delivery,
            subscriber_butler=row["subscriber_butler"],
            event_id=str(row["event_id"]),
            event_type=row["event_type"],
            source_butler=row["source_butler"],
            payload=row["payload"] or {},
            max_attempts=max_attempts,
        )
        pending_redriven += 1
        if outcome["status"] == "delivered":
            pending_now_delivered += 1

    retryable_failed = await select_retryable_failed_deliveries(
        connection, backoff_after=failed_retry_backoff, max_attempts=max_attempts, limit=limit
    )
    failed_retried = 0
    newly_permanent = 0
    for row in retryable_failed:
        delivery = await claim_delivery(
            connection, event_id=row["event_id"], subscriber_butler=row["subscriber_butler"]
        )
        if delivery["status"] != "failed":
            continue
        outcome = await _dispatch_and_record_delivery(
            connection,
            client,
            delivery=delivery,
            subscriber_butler=row["subscriber_butler"],
            event_id=str(row["event_id"]),
            event_type=row["event_type"],
            source_butler=row["source_butler"],
            payload=row["payload"] or {},
            max_attempts=max_attempts,
        )
        failed_retried += 1
        if outcome.pop("_newly_permanently_failed", False):
            newly_permanent += 1
            logger.error(
                "domain-event delivery permanently failed after retries: event_id=%s "
                "subscriber_butler=%s event_type=%s error=%s",
                row["event_id"],
                row["subscriber_butler"],
                row["event_type"],
                outcome.get("error"),
            )

    return {
        "stale_pending_candidates": len(stale_pending),
        "stale_pending_redriven": pending_redriven,
        "stale_pending_delivered": pending_now_delivered,
        "failed_retry_candidates": len(retryable_failed),
        "failed_retried": failed_retried,
        "newly_permanently_failed": newly_permanent,
        "skipped_due_to_active_sweep": False,
    }


async def run_domain_event_reconciliation_sweep(
    pool: Any,
    *,
    stale_pending_after: timedelta = _STALE_PENDING_AFTER,
    failed_retry_backoff: timedelta = _FAILED_RETRY_BACKOFF,
    max_attempts: int = _MAX_DELIVERY_RETRY_ATTEMPTS,
    limit: int = _SWEEP_BATCH_LIMIT,
) -> dict[str, Any]:
    """Run at most one domain-event reconciliation sweep across the fleet.

    Re-observing an existing delivery row is not itself an exclusive claim: two
    overlapping sweeps could both read the same retryable row and each record
    a route failure.  Hold a non-blocking, session-scoped advisory lock for
    the complete select -> dispatch -> outcome sequence so a manual force tick
    or another replica returns a truthful skipped result instead of consuming
    a second retry attempt.  The held connection is deliberately threaded
    through the full sequence; a transaction-scoped lock would be released
    before the network dispatch, and acquiring a second pool connection while
    holding this one can deadlock a constrained pool.

    PostgreSQL releases the lock if the owning connection dies.  The normal
    path explicitly unlocks it before returning the per-pass summary.
    """
    async with pool.acquire() as connection:
        lock_acquired = await connection.fetchval(
            "SELECT pg_try_advisory_lock(hashtext($1))", _RECONCILIATION_SWEEP_LOCK_KEY
        )
        if not lock_acquired:
            logger.info("domain-event reconciliation sweep skipped: another sweep is active")
            return {
                "stale_pending_candidates": 0,
                "stale_pending_redriven": 0,
                "stale_pending_delivered": 0,
                "failed_retry_candidates": 0,
                "failed_retried": 0,
                "newly_permanently_failed": 0,
                "skipped_due_to_active_sweep": True,
            }

        try:
            return await _run_domain_event_reconciliation_sweep_locked(
                connection,
                stale_pending_after=stale_pending_after,
                failed_retry_backoff=failed_retry_backoff,
                max_attempts=max_attempts,
                limit=limit,
            )
        finally:
            lock_released = await connection.fetchval(
                "SELECT pg_advisory_unlock(hashtext($1))", _RECONCILIATION_SWEEP_LOCK_KEY
            )
            if not lock_released:
                logger.error("domain-event reconciliation sweep advisory lock was not held")


def _contract_error(error: str) -> dict[str, Any]:
    """Shape a fail-closed admission refusal as an ordinary tool error result."""
    return {"status": "error", "error": error}


def _refuse_undeclared_publish(
    *, event_type: str, source_butler: str, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Return a refusal when the publisher's git contract does not permit this publish.

    Read from the git declarations, never from the ``public.
    domain_event_contracts`` projection: a projection that is stale, partly
    materialized, or missing must never be able to *widen* what a butler may
    publish. Called before any durable write so a refused publish leaves no
    event row behind.
    """
    error = get_contract_registry().check_publish(
        event_type=event_type, publisher=source_butler, payload=payload or {}
    )
    return _contract_error(error) if error is not None else None


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
    refusal = _refuse_undeclared_publish(
        event_type=event_type, source_butler=source_butler, payload=payload
    )
    if refusal is not None:
        return refusal

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
    refusal = _refuse_undeclared_publish(
        event_type=event_type, source_butler=source_butler, payload=payload
    )
    if refusal is not None:
        return refusal

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
        error = get_contract_registry().check_subscription(
            event_type=event_type, subscriber=butler_name
        )
        if error is not None:
            return _contract_error(error)
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

    @_core_tool("domain_events")
    @tool_span("report_event_reaction", butler_name=butler_name)
    async def report_event_reaction(
        event_id: Annotated[
            str,
            Field(
                description=(
                    "The event_id from the domain-event wake prompt that brought you here."
                )
            ),
        ],
        status: Annotated[
            str,
            Field(
                description=(
                    "How this wake ended: 'acted' (you took a domain action), 'ignored' (you "
                    "judged it not actionable), 'deferred' (you will act later), or 'failed' "
                    "(you tried and could not)."
                )
            ),
        ],
        note: Annotated[
            str | None,
            Field(description="One or two sentences on what you did and why."),
        ] = None,
        evidence: Annotated[
            list[dict[str, str]] | None,
            Field(
                description=(
                    "Typed references to what you produced, e.g. "
                    "[{'kind': 'task', 'ref': '<task name>'}]. Allowed kinds: task, session, "
                    "event, delegation, memory. Prose belongs in 'note', not here."
                )
            ),
        ] = None,
    ) -> dict:
        """Close out a domain-event wake with what you actually did.

        A delivery only records that you were woken. This records the
        outcome, and nothing else can: a session that ends without calling
        this is recorded as ``unreported``, never as success. ``ignored`` is
        a real, respectable outcome — if the event was not relevant to your
        domain, say so here rather than exiting silently.

        Whether acting was the right call is your manifesto's business, not
        the bus's. One wake closes exactly once.
        """
        if status not in REPORTABLE_REACTION_STATUSES:
            return {
                "status": "error",
                "error": (
                    f"status={status!r} is not something a session may report. Use one of "
                    f"{', '.join(sorted(REPORTABLE_REACTION_STATUSES))}. In particular "
                    "'unreported' describes a wake that said nothing at all, which is "
                    "recorded for you rather than claimed by you."
                ),
            }
        try:
            reaction_id = await record_reaction(
                pool,
                event_id=event_id,
                subscriber_butler=butler_name,
                status=status,
                session_id=get_current_runtime_session_id(),
                task_name=None,
                note=note,
                evidence=evidence,
            )
        except DomainEventReactionError as exc:
            return {"status": "error", "error": str(exc)}
        except ValueError as exc:
            return {"status": "error", "error": f"event_id={event_id!r} is not a valid id: {exc}"}
        return {"status": "ok", "reaction_id": reaction_id, "reaction_status": status}
