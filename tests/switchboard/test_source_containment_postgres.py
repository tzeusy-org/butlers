"""M7 Contain over the real admission ledger (RFC 0038 D13, bu-4c7nvp.1.2).

Real Alembic PostgreSQL as the Switchboard runtime role. The dashboard step-up
store is a collaborator here (``_consume``): its real factor and kind checks
are proven by its owner (``tests/integration/test_dashboard_stepup.py``), and
this database has no step-up interface, which is itself the fail-closed case.
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from butlers.core import source_containment
from butlers.core.dashboard_stepup import DashboardStepUpProof, dashboard_stepup_proof
from butlers.core.goal_admission import Evidence, GoalAdmission, SourceRef
from butlers.core.source_containment import STEP_UP_REQUIRED, Containment, contain_source
from tests.polling import poll_until
from tests.switchboard.goal_admission_fixtures import Catalogs, Sources
from tests.switchboard.goal_ledger_fixtures import admit

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

PROOF = DashboardStepUpProof("0" * 64, "A" * 43)


@pytest.fixture
def stepped_up(monkeypatch):
    """A step-up store that admits, then retains, one action (the proof is set per call)."""
    phases = []

    async def consume(connection, phase, binding, result=None):
        phases.append((phase, binding, result))
        return {"state": "admitted" if phase == "begin" else "completed"}

    monkeypatch.setattr(source_containment, "_consume", consume)
    return phases


async def _contain(pool, partition):
    """``contain_source`` under a dashboard step-up proof, as the API would bind it."""
    token = dashboard_stepup_proof.set(PROOF)
    try:
        return await contain_source(pool, partition)
    finally:
        dashboard_stepup_proof.reset(token)


def _sender():
    return Evidence("message", f"sender-{uuid4()}")


async def _waiting_on_advisory_lock(owner) -> bool:
    return bool(
        await owner.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
            "AND wait_event='advisory'"
        )
    )


async def test_a_contained_source_is_recorded_without_a_goal_and_evidence_is_kept(
    ledger, stepped_up
):
    owner, runtime = ledger
    facts = _sender()
    before = SourceRef("message", str(uuid4()))
    earlier = await admit(runtime, facts, source=before)
    assert earlier.result == "admitted"
    contained = await _contain(runtime, facts.source_partition)
    assert isinstance(contained, Containment)
    assert contained.descendant_goals == (earlier.goal_id,)
    assert [phase for phase, _, _ in stepped_up] == ["begin", "finish"]
    assert stepped_up[1][2]["id"] == str(contained.id)
    later = await admit(runtime, facts)
    assert (later.result, later.reason, later.goal_id) == ("refused", "recorded_without_goal", None)
    # Replays return their durable decisions: the old goal is not withdrawn and
    # a record-only envelope never becomes a goal.
    assert await admit(runtime, facts, source=before) == earlier
    goals = await owner.fetchval("SELECT count(*) FROM switchboard.goals")
    assert goals == 1
    assert (await admit(runtime, _sender())).result == "admitted"  # other sources unaffected
    for statement in (
        "UPDATE switchboard.source_containments SET descendant_goals = '{}'",
        "DELETE FROM switchboard.source_containments",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await owner.execute(statement)


async def test_contain_without_a_usable_step_up_writes_nothing(ledger):
    owner, runtime = ledger
    facts = _sender()
    assert await contain_source(runtime, facts.source_partition) == STEP_UP_REQUIRED
    # A proof the trusted store does not hold (or no store at all): no write.
    assert await _contain(runtime, facts.source_partition) == STEP_UP_REQUIRED
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM switchboard.source_containments WHERE source_partition = $1",
            facts.source_partition,
        )
        == 0
    )
    assert (await admit(runtime, facts)).result == "admitted"


async def test_an_admission_holding_the_partition_lock_becomes_a_descendant(ledger, stepped_up):
    owner, runtime = ledger
    facts = _sender()
    entered, release = asyncio.Event(), asyncio.Event()

    class HeldCatalog(Catalogs):
        async def load(self, connection):
            entered.set()
            await release.wait()
            return await super().load(connection)

    admission = asyncio.create_task(
        GoalAdmission(runtime, Sources(facts), HeldCatalog()).admit_submission(
            SourceRef("message", str(uuid4())), uuid4()
        )
    )
    containing = None
    try:
        async with asyncio.timeout(5):
            await entered.wait()
            containing = asyncio.create_task(_contain(runtime, facts.source_partition))
            await poll_until(lambda: _waiting_on_advisory_lock(owner), interval_s=0.02)
        release.set()
        admitted = await asyncio.wait_for(admission, 10)
        contained = await asyncio.wait_for(containing, 10)
        assert admitted.result == "admitted"
        assert contained.descendant_goals == (admitted.goal_id,)
    finally:
        release.set()
        for task in (admission, containing):
            if task is not None and not task.done():
                task.cancel()


async def test_an_envelope_racing_a_containment_is_recorded_without_a_goal(ledger, monkeypatch):
    owner, runtime = ledger
    facts = _sender()
    entered, release = asyncio.Event(), asyncio.Event()

    async def consume(connection, phase, binding, result=None):
        if phase == "begin":
            entered.set()  # contain holds the partition lock here
            await release.wait()
            return {"state": "admitted"}
        return {"state": "completed"}

    monkeypatch.setattr(source_containment, "_consume", consume)
    containing = asyncio.create_task(_contain(runtime, facts.source_partition))
    admission = None
    try:
        async with asyncio.timeout(5):
            await entered.wait()
            admission = asyncio.create_task(admit(runtime, facts))
            await poll_until(lambda: _waiting_on_advisory_lock(owner), interval_s=0.02)
        release.set()
        contained = await asyncio.wait_for(containing, 10)
        recorded = await asyncio.wait_for(admission, 10)
        assert contained.descendant_goals == ()
        assert (recorded.result, recorded.reason) == ("refused", "recorded_without_goal")
        assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 0
        assert await owner.fetchval("SELECT count(*) FROM switchboard.goal_inbox") == 0
    finally:
        release.set()
        for task in (containing, admission):
            if task is not None and not task.done():
                task.cancel()
