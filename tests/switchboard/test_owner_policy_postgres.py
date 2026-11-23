"""Owner tighten-only policy storage on the migrated ledger (sw_059, bu-dhloi1.10).

REQ-capability-authorization-020: expected-version CAS, one receipt per owner
action, tighten-only changes, durable history, a daemon role that can only read
the law, and the generation fence that serialises a change with an in-flight call.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from butlers.core.owner_policy import (
    OwnerPolicyWriter,
    PolicyConflict,
    StepUpRequired,
    list_terms,
    read_policy,
)

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_TABLES = (
    "switchboard.owner_policy_terms, switchboard.owner_policy_receipts, "
    "switchboard.owner_policy_state, switchboard.owner_policy_holds"
)


async def _create(owner, **kwargs):
    """An owner change, on the dashboard's owner connection."""
    values = {"selector_kind": "region", "selector": "messenger", "constraint": "always_ask"}
    values.update(kwargs)
    action = values.pop("owner_action_id", uuid4())
    async with owner.acquire() as connection:
        return action, await OwnerPolicyWriter(connection).create(action, **values)


async def test_a_change_is_one_receipt_and_a_retry_returns_it(ledger):
    owner, runtime = ledger
    before = (await read_policy(runtime)).generation
    action, receipt = await _create(owner)
    assert (receipt["action"], receipt["from_version"], receipt["to_version"]) == (
        "create",
        None,
        1,
    )
    assert receipt["generation"] == before + 1
    _, again = await _create(owner, owner_action_id=action)
    assert again == receipt  # the same action: one change, one receipt
    assert (await read_policy(runtime)).generation == before + 1
    with pytest.raises(PolicyConflict):
        await _create(owner, owner_action_id=action, constraint="never")
    assert await owner.fetchval("SELECT count(*) FROM switchboard.owner_policy_terms") == 1


async def test_changes_only_tighten_under_expected_version_cas(ledger):
    owner, runtime = ledger
    soon = datetime.now(UTC) + timedelta(hours=1)
    _, created = await _create(owner, expires_at=soon)
    term = created["term_id"]
    async with owner.acquire() as connection:
        writer = OwnerPolicyWriter(connection)
        tightened = await writer.tighten(
            uuid4(), term, 1, constraint="never", expires_at=soon + timedelta(hours=1)
        )
        assert (tightened["from_version"], tightened["to_version"]) == (1, 2)
        with pytest.raises(PolicyConflict):  # a stale client
            await writer.tighten(uuid4(), term, 1, constraint="never", expires_at=None)
        for constraint, expires_at in (("always_ask", None), ("never", soon)):
            with pytest.raises(StepUpRequired):  # loosening needs dashboard step-up
                await writer.tighten(uuid4(), term, 2, constraint=constraint, expires_at=expires_at)
        await writer.tighten(uuid4(), term, 2, constraint="never", expires_at=None)
    listed = await list_terms(runtime)
    assert [(t["id"], t["version"], t["term"], t["expires_at"]) for t in listed["terms"]] == [
        (str(term), 3, "never", None)
    ]


async def test_the_daemon_role_reads_the_law_but_never_writes_it(ledger):
    """Model tools run under the daemon role: RLS, not the grant, keeps them out."""
    owner, runtime = ledger
    _, created = await _create(owner, constraint="always_ask")
    await owner.execute(f"GRANT ALL ON {_TABLES} TO butler_switchboard_rw")  # as core_001 does
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await _create(runtime, constraint="never")
        tighten = (
            "UPDATE switchboard.owner_policy_terms SET term = 'never', version = version + 1, "
            "owner_action_id = gen_random_uuid()"
        )
        assert await runtime.execute(tighten) == "UPDATE 0"  # no term is visible to write
        for statement in (
            "UPDATE switchboard.owner_policy_state SET generation = generation + 1",
            "UPDATE switchboard.owner_policy_receipts SET to_version = 9",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.execute(statement)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.execute(
                "INSERT INTO switchboard.owner_policy_receipts (owner_action_id, term_id, action, "
                "request_digest, from_version, to_version, generation) VALUES "
                f"(gen_random_uuid(), '{created['term_id']}', 'create', repeat('0', 64), "
                "NULL, 1, 99)"
            )
    finally:
        await owner.execute(f"REVOKE ALL ON {_TABLES} FROM butler_switchboard_rw")
        await owner.execute(
            "GRANT SELECT, UPDATE ON switchboard.owner_policy_state TO butler_switchboard_rw"
        )
        await owner.execute(
            "GRANT SELECT ON switchboard.owner_policy_terms TO butler_switchboard_rw"
        )
        await owner.execute(
            "GRANT SELECT, INSERT ON switchboard.owner_policy_holds TO butler_switchboard_rw"
        )
    assert [t["term"] for t in (await list_terms(runtime))["terms"]] == ["always_ask"]


async def test_the_store_refuses_loosening_deletion_and_rewrites_for_the_owner_too(ledger):
    owner, _ = ledger
    _, created = await _create(owner, constraint="never")
    term = created["term_id"]
    for statement in (
        "UPDATE switchboard.owner_policy_terms SET term = 'always_ask', "
        "version = version + 1, owner_action_id = gen_random_uuid()",
        "UPDATE switchboard.owner_policy_terms SET version = version + 2, "
        "owner_action_id = gen_random_uuid()",
        "UPDATE switchboard.owner_policy_terms SET version = version + 1",  # no owner action
        "UPDATE switchboard.owner_policy_terms SET selector = 'email', version = version + 1, "
        "owner_action_id = gen_random_uuid()",
        "DELETE FROM switchboard.owner_policy_terms",
        "TRUNCATE switchboard.owner_policy_terms CASCADE",
        "UPDATE switchboard.owner_policy_receipts SET to_version = 9",
        "DELETE FROM switchboard.owner_policy_receipts",
        "DELETE FROM switchboard.owner_policy_state",
        "UPDATE switchboard.owner_policy_state SET generation = 0",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await owner.execute(statement)
    row = await owner.fetchrow("SELECT * FROM switchboard.owner_policy_terms WHERE id = $1", term)
    assert (row["term"], row["version"]) == ("never", 1)


async def test_an_in_flight_call_holds_the_generation_until_it_commits(ledger):
    """A tightening lands before a call or after its effect fence, never between."""
    owner, runtime = ledger
    async with runtime.acquire() as call:
        fence = call.transaction()
        await fence.start()
        seen = await read_policy(call)  # FOR SHARE, as the broker reads it
        change = asyncio.create_task(_create(owner, constraint="never"))
        await asyncio.sleep(0.3)
        assert not change.done()  # waits for the call's transaction
        await fence.commit()
    await change
    assert (await read_policy(runtime)).generation == seen.generation + 1


async def test_an_expired_term_is_listed_but_inactive(ledger):
    owner, runtime = ledger
    past = datetime.now(UTC) - timedelta(seconds=1)
    await _create(owner, constraint="never", expires_at=past)
    assert (await read_policy(runtime)).terms == ()
    assert [t["active"] for t in (await list_terms(runtime))["terms"]] == [False]
