"""The monthly spend ceiling at goal admission and launch (RFC 0038 D10, bu-jv1moa.1).

The spawner's monthly ceiling dies with the spawner. This module carries it onto the goal path as
two pools per UTC month in ``switchboard.budget_pools``:

* ``fleet_month`` holds every goal, whatever its origin. Its cap is the owner's ceiling.
* ``background`` holds obligation, third-party and staffer goals. Its cap is the ceiling less the
  owner reserve (default 20%), so the owner's own turns keep that share.

Admission reserves the goal's ``max_spend`` in the pools inside the admission transaction, under
row locks, so two goals racing for the last unit commit exactly one. The runner's per-call meter
moves the reservation to spent in the goal budget's own transaction; a trigger settles whatever a
goal leaves when it ends (sw_084). Every unreadable state refuses (fail closed): a ceiling the
owner set that cannot be read never lets work through.

No ceiling configured (no row, or a non-positive one) means no pools and no reservation, the same
fast path the spawner took. Turning ``lanes_enabled`` off drops the owner reserve and the
background lane but keeps the fleet pool: there is no fail-open fallback to per-goal budgets alone.

Frozen-path spend (``public.token_usage_ledger``) counts against the fleet pool but not against
the background lane, which meters goal spend only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final, Literal
from uuid import UUID

from butlers.core import model_routing

logger = logging.getLogger(__name__)

FLEET_POOL: Final = "fleet_month"
BACKGROUND_POOL: Final = "background"

#: Admission refusal reasons (admission_decisions.reason) this module produces.
LANE_EXHAUSTED: Final = "lane_exhausted"
CEILING_REACHED: Final = "ceiling_reached"
SPEND_UNREADABLE: Final = "spend_state_unreadable"

DEFAULT_OWNER_RESERVE_PCT: Final = Decimal("0.20")

Origin = Literal["owner", "obligation", "third_party", "staffer"]


@dataclass(frozen=True)
class SpendPolicy:
    """The reserve and the lanes switch; carried in the goal admission config."""

    owner_reserve_pct: Decimal = DEFAULT_OWNER_RESERVE_PCT
    lanes_enabled: bool = True


@dataclass(frozen=True)
class Ceiling:
    """The owner's monthly ceiling and the priced frozen-path spend, or none configured."""

    usd: Decimal
    frozen_mtd_usd: Decimal


class SpendUnreadable(Exception):
    """The ceiling or the month's spend could not be read; the caller refuses."""


def _is_absent_relation(exc: Exception) -> bool:
    return exc.__class__.__name__ == "UndefinedTableError"


class SpendPools:
    """Pool reads and writes on one transaction's connection."""

    def __init__(self, connection, policy: SpendPolicy | None = None):
        self.connection = connection
        self.policy = policy or SpendPolicy()

    async def ceiling(self) -> Ceiling | None:
        """The configured ceiling with the month's priced frozen-path spend; ``None`` if unset.

        A savepoint keeps a failed read from aborting the caller's transaction. A missing
        ``spend_ceiling`` relation is an unconfigured ceiling, not an unreadable one.
        """
        try:
            async with self.connection.transaction():
                row = await self.connection.fetchrow(model_routing._CEILING_SELECT_SQL)
                if row is None or Decimal(row["monthly_usd"]) <= 0:
                    return None
                spend = await model_routing.price_mtd_from_ledger(self.connection)
                return Ceiling(Decimal(row["monthly_usd"]), Decimal(str(spend.cost_usd)))
        except Exception as exc:
            if _is_absent_relation(exc):
                return None
            logger.error("spend ceiling unreadable; refusing", exc_info=True)
            raise SpendUnreadable from exc

    async def _month(self) -> date:
        return await self.connection.fetchval(
            "SELECT date_trunc('month', now() AT TIME ZONE 'UTC')::date"
        )

    async def _locked_pools(self, month: date, ceiling: Ceiling) -> dict[str, dict]:
        """Seed the month's pools (caps follow the ceiling) and lock them in a fixed order."""
        reserve = self.policy.owner_reserve_pct if self.policy.lanes_enabled else Decimal(0)
        caps = {FLEET_POOL: ceiling.usd, BACKGROUND_POOL: ceiling.usd * (1 - reserve)}
        for key, cap in sorted(caps.items()):  # the one lock order: pool_key
            await self.connection.execute(
                "INSERT INTO switchboard.budget_pools (pool_key, month, cap_usd) "
                "VALUES ($1, $2, $3) ON CONFLICT (pool_key, month) DO UPDATE "
                "SET cap_usd = EXCLUDED.cap_usd, updated_at = now() "
                "WHERE switchboard.budget_pools.cap_usd IS DISTINCT FROM EXCLUDED.cap_usd",
                key,
                month,
                cap,
            )
        rows = await self.connection.fetch(
            "SELECT pool_key, cap_usd, reserved_usd, spent_usd FROM switchboard.budget_pools "
            "WHERE month = $1 AND pool_key = ANY($2::text[]) ORDER BY pool_key FOR UPDATE",
            month,
            list(caps),
        )
        return {row["pool_key"]: row for row in rows}

    async def reserve(self, goal_id: UUID, origin: Origin, amount: Decimal) -> str | None:
        """Reserve ``amount`` for a goal; return the refusal reason, or ``None`` when held.

        Idempotent per goal: a repeat (duplicate admission, recovery epoch) reuses the one
        reservation and moves nothing. Everything runs in one savepoint, so any pool error
        (missing table, lock timeout, deadlock) rolls back to it and refuses
        ``spend_state_unreadable`` without aborting the admission transaction: the caller can
        still record the refusal and answer the owner.
        """
        try:
            async with self.connection.transaction():
                return await self._reserve(goal_id, origin, amount)
        except Exception:
            logger.error("spend pools unreadable; refusing", exc_info=True)
            return SPEND_UNREADABLE

    async def _reserve(self, goal_id: UUID, origin: Origin, amount: Decimal) -> str | None:
        ceiling = await self.ceiling()
        if ceiling is None:
            return None
        month = await self._month()
        existing = await self.connection.fetchval(
            "SELECT 1 FROM switchboard.pool_reservations WHERE goal_id = $1", goal_id
        )
        if existing:
            return None
        pools = await self._locked_pools(month, ceiling)
        fleet = pools[FLEET_POOL]
        if (
            ceiling.frozen_mtd_usd + fleet["reserved_usd"] + fleet["spent_usd"] + amount
            > (fleet["cap_usd"])
        ):
            return CEILING_REACHED
        background = origin != "owner" and self.policy.lanes_enabled
        if background:
            lane = pools[BACKGROUND_POOL]
            if lane["reserved_usd"] + lane["spent_usd"] + amount > lane["cap_usd"]:
                return LANE_EXHAUSTED
        keys = [FLEET_POOL] + ([BACKGROUND_POOL] if background else [])
        await self.connection.execute(
            "UPDATE switchboard.budget_pools SET reserved_usd = reserved_usd + $3, "
            "updated_at = now() WHERE month = $1 AND pool_key = ANY($2::text[])",
            month,
            keys,
            amount,
        )
        await self.connection.execute(
            "INSERT INTO switchboard.pool_reservations (goal_id, month, origin, amount_usd, lane) "
            "VALUES ($1, $2, $3, $4, $5)",
            goal_id,
            month,
            origin,
            amount,
            background,
        )
        return None

    async def charge(self, goal_id: UUID, spend: Decimal, *, tokens: int = 0) -> None:
        """Move priced spend from the goal's reservation to spent, in the caller's transaction.

        Never charges past the reservation, and a goal with no reservation (no ceiling, or one
        admitted before the ceiling was set) moves nothing. Usage with tokens but no priced
        spend marks the reservation unpriced: it is then held in full at the goal's end, so
        unpriced usage is never counted as zero, even after earlier priced calls.
        """
        unpriced = tokens > 0 and spend <= 0
        if spend <= 0 and not unpriced:
            return
        await self.connection.execute(
            "SELECT switchboard.charge_goal_reservation($1, $2, $3)",
            goal_id,
            max(spend, Decimal(0)),
            unpriced,
        )

    async def launch_refusal(self) -> str | None:
        """``"ceiling_reached"`` when the fleet pool has no room left for any origin, else ``None``.

        Reservations are not counted here (a goal launching owns its own); only spend is. An
        unreadable state refuses as ``spend_state_unreadable`` without aborting the caller's
        transaction.
        """
        try:
            async with self.connection.transaction():
                ceiling = await self.ceiling()
                if ceiling is None:
                    return None
                month = await self._month()
                spent = await self.connection.fetchval(
                    "SELECT spent_usd FROM switchboard.budget_pools "
                    "WHERE pool_key = $1 AND month = $2",
                    FLEET_POOL,
                    month,
                )
        except Exception:
            return SPEND_UNREADABLE
        total = ceiling.frozen_mtd_usd + (spent or Decimal(0))
        return CEILING_REACHED if total >= ceiling.usd else None
