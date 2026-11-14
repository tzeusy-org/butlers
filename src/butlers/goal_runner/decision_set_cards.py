"""The goal runner's decision-set card sweep (bu-jv1moa.75, RFC 0038 M7).

Two bounded steps per runner sweep, both dark behind ``OWNER_DECISIONS_LIVE`` and the
owner-delivery rollout (a runner built without this collaborator never runs them):

* **Present**: each open, decidable set version with no card already handed to its provider is
  read once and sent through :func:`send_set_card`. The send is exactly-once per
  ``(set_id, version)`` because the owner-delivery subject and its presentation are keyed on it:
  a replay, a restart or a second runner finds the presentation ``sent``/``unknown`` and sends
  nothing. Owner suppression (quiet hours, the context bus) is checked *before* anything is
  minted, so a suppressed owner creates no presentation and no token.
* **Close**: a version that left ``open`` by any path other than a Telegram token (superseded,
  cancelled, expired, or decided on the dashboard) has its subject closed through
  :func:`close_for_authority`, which stales every token and lists the card for the Telegram
  connector's retraction drain. A decided version closes with the verdict in its receipt, never
  with a verdict taken from the subject.

A set the card cannot offer a decision on (open-only) is never card-sent: it has no
exactly-once subject, and each member effect already has its own approval card.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from butlers.capability_broker import owner_decisions
from butlers.core import owner_delivery as od
from butlers.modules.approvals.decision_set_card import render_set_card
from butlers.modules.approvals.decision_set_delivery import (
    SendResult,
    Transport,
    send_set_card,
    set_source_ref,
)
from butlers.modules.approvals.decision_set_view import load_set_view

logger = logging.getLogger(__name__)

#: Sets read per sweep, oldest first. A set that cannot be card-sent is re-read each sweep, so the
#: scan is wider than the number of cards one sweep may send.
SCAN_LIMIT: Final = 50
SEND_LIMIT: Final = 5
CLOSE_LIMIT: Final = 50

_CLOSE_STATUS: Final = {"superseded": "cancelled", "cancelled": "cancelled", "expired": "expired"}


def owner_notify_transport(pool: Any, *, origin_butler: str = "switchboard") -> Transport:
    """The existing owner-notify path for one prepared ``notify.v1`` card.

    Only an outcome decided before any provider call is a proven non-send (``failed``, safe to
    present again); anything after the call began is ``unknown`` and is never sent again.
    """
    from butlers.jobs.owner_notify import send_owner_telegram_checked

    async def send(envelope: dict[str, Any]) -> SendResult:
        result = await send_owner_telegram_checked(
            pool,
            origin_butler=origin_butler,
            message="",
            log_context="decision_set_card",
            dedup_key=None,
            envelope=envelope,
        )
        if result.outcome == "delivered":
            return SendResult("sent", result.provider_ref)
        return SendResult("unknown" if result.call_started else "failed")

    return send


class DecisionSetCards:
    """The sweep collaborator ``GoalRunner`` calls after resolving decision sets."""

    def __init__(
        self,
        pool: Any,
        *,
        action_pools: Sequence[tuple[str, Any]],
        transport: Transport,
        recipient: Callable[[Any], Awaitable[str | None]] | None = None,
        suppression: Callable[..., Awaitable[str | None]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        origin_butler: str = "switchboard",
        live: bool | None = None,
    ) -> None:
        from butlers.core.attention_ledger import check_owner_notify_suppression
        from butlers.credential_store import resolve_owner_telegram_recipient

        self.pool = pool
        self.action_pools = tuple(action_pools)
        self.transport = transport
        self._recipient = recipient or resolve_owner_telegram_recipient
        self._suppression = suppression or check_owner_notify_suppression
        self._clock = clock
        self.origin_butler = origin_butler
        self._live = live

    def _is_live(self) -> bool:
        return owner_decisions.OWNER_DECISIONS_LIVE if self._live is None else self._live

    async def sweep(self) -> int:
        """Close stale cards, then present new ones. Returns the cards handed off this sweep."""
        try:
            await self._close_stale()
        except Exception:
            logger.exception("decision set card close sweep failed")
        try:
            return await self._present()
        except Exception:
            logger.exception("decision set card present sweep failed")
            return 0

    # -- present --------------------------------------------------------------

    async def _present(self) -> int:
        if not self._is_live():
            return 0
        async with self.pool.acquire() as conn:
            if not await od.rollout_enabled(conn):
                return 0
        # Before any view is read, presentation created or token minted.
        if await self._suppression(self.pool, log_context="decision_set_card") is not None:
            return 0
        recipient = await self._recipient(self.pool)
        if not recipient:
            return 0
        now = self._clock()
        async with self.pool.acquire() as conn:
            candidates = await conn.fetch(
                "SELECT v.set_id, v.version FROM switchboard.decision_set_versions v "
                "WHERE v.state = 'open' AND v.expires_at > $1 AND NOT EXISTS ("
                "  SELECT 1 FROM public.owner_delivery_subjects s "
                "  JOIN public.owner_delivery_presentations p ON p.subject_id = s.id "
                "  WHERE s.owning_schema = current_schema() AND s.kind = 'decision_set' "
                "  AND s.source_ref = v.set_id::text || ':' || v.version::text "
                "  AND p.state IN ('sent', 'unknown')) "
                "ORDER BY v.created_at, v.set_id LIMIT $2",
                now,
                SCAN_LIMIT,
            )
        handed_off = attempted = 0
        for row in candidates:
            if attempted >= SEND_LIMIT:
                break
            set_id, version = row["set_id"], row["version"]
            try:
                view = await load_set_view(
                    set_id,
                    version,
                    ledger_pool=self.pool,
                    action_pools=self.action_pools,
                    now=now,
                )
                if view is None or not render_set_card(view).offer_decision:
                    continue
                sent = await send_set_card(
                    self.pool,
                    view,
                    origin_butler=self.origin_butler,
                    owner_recipient=recipient,
                    transport=self.transport,
                    live=self._is_live(),
                )
            except Exception:
                logger.exception("decision set card skipped set=%s version=%s", set_id, version)
                continue
            attempted += 1
            if sent.status in ("sent", "unknown"):
                handed_off += 1
            elif sent.status == "failed":
                # A pre-call failure (no client, suppressed, no recipient) is global: stop,
                # rather than present and mint tokens for every scanned set during an outage.
                break
        return handed_off

    # -- close ----------------------------------------------------------------

    async def _close_stale(self) -> None:
        async with self.pool.acquire() as conn:
            if await conn.fetchval("SELECT to_regclass('public.owner_delivery_subjects')") is None:
                return
            stale = await conn.fetch(
                "SELECT v.set_id, v.version, v.state FROM public.owner_delivery_subjects s "
                "JOIN switchboard.decision_set_versions v "
                "  ON s.source_ref = v.set_id::text || ':' || v.version::text "
                "WHERE s.owning_schema = current_schema() AND s.kind = 'decision_set' "
                "AND s.state NOT IN ('decided', 'cancelled', 'expired') AND v.state <> 'open' "
                "ORDER BY s.created_at LIMIT $1",
                CLOSE_LIMIT,
            )
        for row in stale:
            try:
                await self._close(row["set_id"], row["version"], row["state"])
            except Exception:
                logger.exception(
                    "decision set card close failed set=%s version=%s",
                    row["set_id"],
                    row["version"],
                )

    async def _close(self, set_id: UUID, version: int, state: str) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            if state == "decided":
                verdict = await conn.fetchval(
                    "SELECT decision FROM switchboard.owner_decisions "
                    "WHERE set_id = $1 AND set_version = $2",
                    set_id,
                    version,
                )
                status = {"approve": "approved", "reject": "rejected"}.get(verdict or "")
            else:
                status = _CLOSE_STATUS.get(state)
            if status is None:
                logger.warning(
                    "decision set card not closed set=%s version=%s state=%s: no verdict",
                    set_id,
                    version,
                    state,
                )
                return
            await od.close_for_authority(
                conn,
                kind="decision_set",
                source_ref=set_source_ref(set_id, version),
                status=status,
            )
