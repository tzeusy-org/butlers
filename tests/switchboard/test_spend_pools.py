"""The monthly spend ceiling at goal admission and launch (bu-jv1moa.1), real PostgreSQL."""

import asyncio
from decimal import Decimal

import pytest

from butlers.core.goal_admission import Evidence, SourceRef
from butlers.core.spend_pools import SpendPools
from tests.switchboard.goal_admission_fixtures import limits
from tests.switchboard.goal_ledger_fixtures import admit, configure  # noqa: F401

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

OWNER = Evidence("message", "chat", owner=True)
THIRD_PARTY = Evidence("message", "mail")


async def set_ceiling(owner, usd):
    await owner.execute("DELETE FROM public.spend_ceiling")
    if usd is not None:
        await owner.execute(
            "INSERT INTO public.spend_ceiling (id, monthly_usd) VALUES (1, $1)", Decimal(usd)
        )
    await owner.execute("DELETE FROM switchboard.budget_pools")


async def pool_row(owner, key):
    return await owner.fetchrow(
        "SELECT reserved_usd, spent_usd, cap_usd FROM switchboard.budget_pools WHERE pool_key=$1",
        key,
    )


# Each goal reserves max_spend 1.25. A ceiling of 10 gives the background lane 8 (five goals
# fit: 6.25; a sixth makes 7.5 and fits; a seventh would make 8.75).
async def test_background_stops_at_the_lane_cap_and_the_owner_keeps_the_reserve(ledger):
    owner, runtime = ledger
    await set_ceiling(owner, "10")
    results = [await admit(runtime, THIRD_PARTY) for _ in range(7)]
    assert [r.result for r in results] == ["admitted"] * 6 + ["refused"]
    assert results[-1].reason == "lane_exhausted"
    assert (await admit(runtime, OWNER)).result == "admitted"
    background = await pool_row(owner, "background")
    fleet = await pool_row(owner, "fleet_month")
    assert background["reserved_usd"] == Decimal("7.5") and background["cap_usd"] == Decimal(8)
    assert fleet["reserved_usd"] == Decimal("8.75")


async def test_two_goals_racing_for_the_last_unit_commit_exactly_one(ledger):
    owner, runtime = ledger
    await set_ceiling(owner, "1.5")
    a, b = await asyncio.gather(admit(runtime, OWNER), admit(runtime, OWNER))
    assert sorted(r.result for r in (a, b)) == ["admitted", "refused"]
    assert await owner.fetchval("SELECT count(*) FROM switchboard.pool_reservations") == 1
    assert (await pool_row(owner, "fleet_month"))["reserved_usd"] == Decimal("1.25")


async def test_an_unreadable_spend_state_refuses_every_origin(ledger):
    owner, runtime = ledger
    await set_ceiling(owner, "10")
    await owner.execute("ALTER TABLE switchboard.budget_pools RENAME TO budget_pools_away")
    try:
        third = await admit(runtime, THIRD_PARTY)
        mine = await admit(runtime, OWNER)
    finally:
        await owner.execute("ALTER TABLE switchboard.budget_pools_away RENAME TO budget_pools")
    assert (third.result, third.reason) == ("refused", "spend_state_unreadable")
    assert (mine.result, mine.reason) == ("refused", "spend_state_unreadable")
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 0


async def test_no_ceiling_configured_reserves_nothing(ledger):
    owner, runtime = ledger
    await set_ceiling(owner, None)
    assert (await admit(runtime, THIRD_PARTY)).result == "admitted"
    assert await owner.fetchval("SELECT count(*) FROM switchboard.pool_reservations") == 0


async def test_a_spent_fleet_pool_refuses_every_launch(ledger):
    owner, runtime = ledger
    await set_ceiling(owner, "10")
    assert (await admit(runtime, OWNER)).result == "admitted"
    async with runtime.acquire() as connection:
        assert await SpendPools(connection).launch_refusal() is None
    await owner.execute(
        "UPDATE switchboard.budget_pools SET spent_usd = 10 WHERE pool_key='fleet_month'"
    )
    async with runtime.acquire() as connection:
        assert await SpendPools(connection).launch_refusal() == "ceiling_reached"


async def test_a_repeat_admission_reuses_its_one_reservation(ledger):
    owner, runtime = ledger
    await set_ceiling(owner, "10")
    source = SourceRef("message", "one-envelope")
    first = await admit(runtime, OWNER, source=source)
    again = await admit(runtime, OWNER, source=source)
    assert first == again
    assert await owner.fetchval("SELECT count(*) FROM switchboard.pool_reservations") == 1
    # The same goal asking again (a recovery epoch) moves nothing.
    async with runtime.acquire() as connection, connection.transaction():
        assert await SpendPools(connection).reserve(first.goal_id, "owner", Decimal("1.25")) is None
    assert (await pool_row(owner, "fleet_month"))["reserved_usd"] == Decimal("1.25")


async def test_usage_charges_in_the_goal_transaction_and_the_end_settles_the_rest(ledger):
    owner, runtime = ledger
    await set_ceiling(owner, "10")
    priced = await admit(runtime, OWNER)
    unpriced = await admit(runtime, OWNER)
    async with runtime.acquire() as connection, connection.transaction():
        await SpendPools(connection).charge(priced.goal_id, Decimal("0.50"))
        await SpendPools(connection).charge(priced.goal_id, Decimal("9"))  # never past the hold
    fleet = await pool_row(owner, "fleet_month")
    assert (fleet["reserved_usd"], fleet["spent_usd"]) == (Decimal("1.25"), Decimal("1.25"))
    # An unpriced goal that used tokens keeps its whole reservation charged at its end.
    await owner.execute(
        "UPDATE switchboard.budgets SET used_tokens = 10 WHERE goal_id = $1", unpriced.goal_id
    )
    await owner.execute(
        "UPDATE switchboard.goals SET status='failed' WHERE id=$1", unpriced.goal_id
    )
    await owner.execute(
        "UPDATE switchboard.goals SET status='completed' WHERE id=$1", priced.goal_id
    )
    fleet = await pool_row(owner, "fleet_month")
    assert (fleet["reserved_usd"], fleet["spent_usd"]) == (Decimal(0), Decimal("2.5"))


async def test_lanes_off_keeps_the_fleet_pool_and_drops_the_reserve(ledger):
    owner, runtime = ledger
    await set_ceiling(owner, "2")
    config = limits()
    config["spend"] = {"lanes_enabled": False}
    await configure(owner, config)
    assert (await admit(runtime, THIRD_PARTY)).result == "admitted"
    refused = await admit(runtime, THIRD_PARTY)
    assert (refused.result, refused.reason) == ("refused", "ceiling_reached")
    assert await owner.fetchval("SELECT bool_or(lane) FROM switchboard.pool_reservations") is False
