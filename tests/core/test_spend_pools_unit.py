"""Spend pool policy and fail-closed behaviour without a database (bu-jv1moa.1)."""

from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

import pytest

from butlers.core.model_routing import CEILING_DENIAL_REASON_PREFIX
from butlers.core.runtime_config import GoalAdmissionConfig
from butlers.core.spend_pools import (
    CEILING_REACHED,
    LANE_EXHAUSTED,
    SPEND_UNREADABLE,
    SpendPolicy,
    SpendPools,
)
from butlers.goal_runner.owner_failure import (
    CEILING_REACHED_LINE,
    SPEND_UNREADABLE_LINE,
    line_for,
)
from tests.switchboard.goal_admission_fixtures import limits

pytestmark = pytest.mark.unit


class FakeConnection:
    """Just enough asyncpg surface: one ceiling row, pool rows, and recorded writes."""

    def __init__(self, ceiling="10", fleet=(0, 0), background=(0, 0), fail=False, absent=False):
        self.ceiling, self.fail, self.absent = ceiling, fail, absent
        self.pools = {"fleet_month": list(fleet), "background": list(background)}
        self.reservations = []
        self.executed = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, sql, *args):
        if self.absent:
            raise type("UndefinedTableError", (Exception,), {})()
        if self.fail:
            raise RuntimeError("down")
        return None if self.ceiling is None else {"monthly_usd": Decimal(self.ceiling)}

    async def fetch(self, sql, *args):
        if "token_usage_ledger" in sql:
            return []
        month, keys = args
        return [
            {
                "pool_key": key,
                "cap_usd": self.caps[key],
                "reserved_usd": Decimal(self.pools[key][0]),
                "spent_usd": Decimal(self.pools[key][1]),
            }
            for key in keys
        ]

    async def fetchval(self, sql, *args):
        if "date_trunc" in sql:
            return "2026-11-01"
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if "INSERT INTO switchboard.budget_pools" in sql:
            self.caps = getattr(self, "caps", {})
            self.caps[args[0]] = args[2]
        if "INSERT INTO switchboard.pool_reservations" in sql:
            self.reservations.append(args)


@pytest.fixture(autouse=True)
def no_ledger_spend(monkeypatch):
    class Spend:
        cost_usd = 0.0

    async def priced(connection, pricing=None):
        return Spend()

    monkeypatch.setattr("butlers.core.model_routing.price_mtd_from_ledger", priced)


async def test_the_owner_keeps_the_reserve_from_background_goals():
    conn = FakeConnection(background=(7.5, 0))
    pools = SpendPools(conn)
    assert await pools.reserve(uuid4(), "third_party", Decimal("1.25")) == LANE_EXHAUSTED
    assert await pools.reserve(uuid4(), "owner", Decimal("1.25")) is None
    assert len(conn.reservations) == 1


async def test_a_full_fleet_pool_refuses_the_owner_too():
    conn = FakeConnection(fleet=(9, 0))
    assert await SpendPools(conn).reserve(uuid4(), "owner", Decimal("1.25")) == CEILING_REACHED


async def test_an_unreadable_ceiling_fails_closed_and_a_missing_one_is_unconfigured():
    assert (
        await SpendPools(FakeConnection(fail=True)).reserve(uuid4(), "owner", Decimal(1))
        == SPEND_UNREADABLE
    )
    assert (
        await SpendPools(FakeConnection(absent=True)).reserve(uuid4(), "owner", Decimal(1)) is None
    )
    assert (
        await SpendPools(FakeConnection(ceiling=None)).reserve(uuid4(), "owner", Decimal(1)) is None
    )


async def test_lanes_off_drops_the_background_cap():
    conn = FakeConnection(background=(7.5, 0))
    pools = SpendPools(conn, SpendPolicy(Decimal("0.2"), lanes_enabled=False))
    assert await pools.reserve(uuid4(), "third_party", Decimal("1.25")) is None


def test_spend_policy_is_read_from_config_without_changing_the_digest():
    base = limits()
    plain = GoalAdmissionConfig.model_validate(base)
    custom = GoalAdmissionConfig.model_validate({**base, "spend": {"owner_reserve_pct": "0.5"}})
    assert plain.spend.owner_reserve_pct == Decimal("0.20") and plain.spend.lanes_enabled
    assert custom.spend.owner_reserve_pct == Decimal("0.5")
    assert plain.model_dump(mode="json") == custom.model_dump(mode="json")


def test_the_owner_hears_a_spend_state_reason():
    assert line_for("admission_refused", "spend_state_unreadable") == SPEND_UNREADABLE_LINE


def test_a_ceiling_refusal_has_its_own_owner_line_and_is_retryable():
    from butlers.core.goal_ledger import RETRYABLE_REFUSALS

    assert line_for("admission_refused", "ceiling_reached") == CEILING_REACHED_LINE
    assert CEILING_DENIAL_REASON_PREFIX in CEILING_REACHED_LINE
    assert "ceiling_reached" in RETRYABLE_REFUSALS


class ExplodingPools(FakeConnection):
    """A pool read that fails after the ceiling is read, as a missing table or lock timeout."""

    def __init__(self):
        super().__init__()
        self.savepoints = 0

    @asynccontextmanager
    async def transaction(self):
        self.savepoints += 1
        yield

    async def fetch(self, sql, *args):
        if "token_usage_ledger" in sql:
            return []
        raise RuntimeError("relation switchboard.budget_pools does not exist")


async def test_a_pool_error_inside_reserve_is_contained_by_its_savepoint():
    conn = ExplodingPools()
    reason = await SpendPools(conn).reserve(uuid4(), "owner", Decimal(1))
    assert reason == SPEND_UNREADABLE
    # reserve opened its own savepoint (plus ceiling's), so the error never reaches the caller.
    assert conn.savepoints >= 2
