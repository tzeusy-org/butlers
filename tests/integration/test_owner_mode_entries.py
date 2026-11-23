"""Owner mode entries and exits under the mode_loosen step-up, end to end (bu-dhloi1.21 PR5).

Real migrated Switchboard ledger, the real trusted step-up interface and a real
synthetic WebAuthn factor (``authority``). Entering a mode, or extending an
active entry, is written with no step-up. Exiting, or ending an active entry
sooner, is written only under a fresh step-up bound to that exact resulting
entry at that version, spent once in the same transaction as the write. Entry
and overlay targets share the ``mode_loosen`` kind and the ``mode:version``
shape but never each other's digest, so neither can be spent on the other.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest

from butlers.core.dashboard_stepup import consume_stepup, dashboard_stepup_proof
from butlers.perception import flags
from butlers.perception.mode_entries import ModeEntryWriter, entry_binding, read_entry
from butlers.perception.mode_overlay import ModeConflict, ModeOverlayWriter, overlay_binding
from butlers.perception.modes import (
    MODE_LOOSEN_KIND,
    EditBreakThrough,
    EditHolds,
    Enter,
    Exit,
    ModeControlsDark,
    ModeStepUpRequired,
)
from tests.integration.test_dashboard_stepup import authority as authority
from tests.integration.test_dashboard_stepup import signed_binding_proof

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_SWITCHBOARD = {"core": "switchboard", "switchboard": "switchboard"}


@pytest.fixture
def authority_db_url(fresh_db) -> str:
    """``authority`` on the Switchboard ledger, where the entries live."""
    return fresh_db("core", "switchboard", schemas=_SWITCHBOARD)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(flags, "MODE_CONTROLS_LIVE", True)


async def _now(pool):
    return await pool.fetchval("SELECT now()")


async def _proof(authority, change, action, version):
    async with authority[0].acquire() as connection:
        binding = await entry_binding(
            connection, change, owner_action_id=action, expected_version=version
        )
    return binding, await signed_binding_proof(authority, binding)


async def _change(pool, change, action, version, proof=None):
    token = dashboard_stepup_proof.set(proof)
    try:
        async with pool.acquire() as connection:
            return await ModeEntryWriter(connection).change(action, change, version)
    finally:
        dashboard_stepup_proof.reset(token)


async def _entry(pool, mode):
    async with pool.acquire() as connection:
        return await read_entry(connection, mode)


async def _entered(pool, mode, hours=8):
    """``mode`` entered now for ``hours``, at version 1."""
    until = await _now(pool) + timedelta(hours=hours)
    await _change(pool, Enter(mode, until=until, cause="owner_declared"), uuid4(), 0)
    return until


async def test_the_entry_writer_is_dark(authority):
    assert flags.MODE_CONTROLS_LIVE is False
    with pytest.raises(ModeControlsDark):
        await _change(authority[0], Enter("focus", until=None, cause="owner_declared"), uuid4(), 0)
    assert (await _entry(authority[0], "focus"))[1] == 0


async def test_entering_and_extending_need_no_step_up(authority, live):
    pool = authority[0]
    until = await _entered(pool, "focus")
    entered, version = await _entry(pool, "focus")
    assert version == 1 and entered is not None and entered.until == until
    later = Enter("focus", until=None, cause="owner_confirmed")
    assert (await _change(pool, later, uuid4(), 1))["verdict"] == "tighten"
    assert (await _change(pool, later, uuid4(), 2))["verdict"] == "none"
    active, version = await _entry(pool, "focus")
    assert active is not None and version == 2
    # Extended in place: the same start, open-ended now.
    assert (active.since, active.until, active.cause) == (entered.since, None, "owner_confirmed")
    assert await pool.fetchval("SELECT count(*) FROM dashboard_stepup.bindings") == 0


async def test_an_exit_without_a_step_up_writes_nothing(authority, live):
    pool = authority[0]
    await _entered(pool, "sick")
    for change in (
        Exit("sick"),
        Enter("sick", until=await _now(pool) + timedelta(hours=1), cause="owner_declared"),
    ):
        with pytest.raises(ModeStepUpRequired):
            await _change(pool, change, uuid4(), 1)
    active, version = await _entry(pool, "sick")
    assert active is not None and version == 1


async def test_a_step_up_exits_once_and_binds_its_result_to_mode_and_version(authority, live):
    pool = authority[0]
    await _entered(pool, "asleep")
    action = uuid4()
    _, proof = await _proof(authority, Exit("asleep"), action, 1)
    receipt = await _change(pool, Exit("asleep"), action, 1, proof)
    assert receipt == {"mode": "asleep", "version": 2, "verdict": "loosen"}
    active, version = await _entry(pool, "asleep")
    assert (active, version) == (None, 2)
    stored = await pool.fetchrow(
        "SELECT target, result FROM dashboard_stepup.bindings WHERE kind = $1", MODE_LOOSEN_KIND
    )
    assert stored["target"].startswith("asleep:2:")
    assert stored["result"] == {
        "version": 1,
        "kind": MODE_LOOSEN_KIND,
        "id": str(action),
        "rule_id": "asleep:2",
    }
    # Re-entered, the spent proof cannot exit it again.
    await _change(pool, Enter("asleep", until=None, cause="owner_declared"), uuid4(), 2)
    with pytest.raises(ModeStepUpRequired):
        await _change(pool, Exit("asleep"), uuid4(), 3, proof)
    assert (await _entry(pool, "asleep"))[1] == 3


async def test_a_step_up_ends_an_entry_early(authority, live):
    pool = authority[0]
    await _entered(pool, "travel", hours=72)
    sooner = Enter("travel", until=await _now(pool) + timedelta(hours=1), cause="owner_declared")
    action = uuid4()
    _, proof = await _proof(authority, sooner, action, 1)
    assert (await _change(pool, sooner, action, 1, proof))["verdict"] == "loosen"
    active, version = await _entry(pool, "travel")
    assert version == 2 and active is not None and active.until == sooner.until


async def test_a_step_up_for_one_change_cannot_apply_another(authority, live):
    pool = authority[0]
    await _entered(pool, "focus")
    await _entered(pool, "hosting")
    action = uuid4()
    _, proof = await _proof(authority, Exit("focus"), action, 1)
    sooner = Enter("focus", until=await _now(pool) + timedelta(hours=1), cause="owner_declared")
    for mode, other in (("focus", sooner), ("hosting", Exit("hosting"))):
        with pytest.raises(ModeStepUpRequired):
            await _change(pool, other, action, 1, proof)
        assert (await _entry(pool, mode))[1] == 1
    # The refused attempts did not spend it: the exact exit still applies.
    assert (await _change(pool, Exit("focus"), action, 1, proof))["version"] == 2


async def test_a_stale_step_up_refuses_after_the_entry_changed(authority, live):
    pool = authority[0]
    until = await _entered(pool, "focus")
    _, proof = await _proof(authority, Exit("focus"), uuid4(), 1)
    longer = Enter("focus", until=until + timedelta(hours=1), cause="owner_declared")
    await _change(pool, longer, uuid4(), 1)
    with pytest.raises(ModeConflict):
        await _change(pool, Exit("focus"), uuid4(), 1, proof)
    with pytest.raises(ModeStepUpRequired):
        await _change(pool, Exit("focus"), uuid4(), 2, proof)
    active, version = await _entry(pool, "focus")
    assert active is not None and version == 2


# -- the trigger's own checks, with owner SQL that bypasses the writer ---------------

# Exit ``focus`` exactly as an Exit binds it at version 2: stamp now, keep the rest.
_EXACT_EXIT = (
    "UPDATE switchboard.owner_mode_entries SET version = 2, exited_at = now(), "
    "owner_action_id = $1 WHERE mode = 'focus'"
)


async def test_the_database_refuses_an_exit_its_step_up_did_not_bind(authority, live):
    pool = authority[0]
    await _entered(pool, "focus")
    action = uuid4()
    binding, proof = await _proof(authority, Exit("focus"), action, 1)
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                token = dashboard_stepup_proof.set(proof)
                try:
                    assert await consume_stepup(connection, "begin", binding) == {
                        "state": "admitted"
                    }
                finally:
                    dashboard_stepup_proof.reset(token)
                # Owner SQL ending it sooner instead of exiting it.
                await connection.execute(
                    "UPDATE switchboard.owner_mode_entries SET version = 2, "
                    "until = now() + interval '1 hour', owner_action_id = $1 "
                    "WHERE mode = 'focus'",
                    action,
                )
    assert (await _entry(pool, "focus"))[1] == 1


async def test_a_step_up_admitted_in_another_transaction_binds_no_exit(authority, live):
    pool = authority[0]
    await _entered(pool, "focus")
    action = uuid4()
    binding, proof = await _proof(authority, Exit("focus"), action, 1)
    token = dashboard_stepup_proof.set(proof)
    try:
        async with pool.acquire() as connection:
            async with connection.transaction():
                assert await consume_stepup(connection, "begin", binding) == {"state": "admitted"}
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with connection.transaction():
                    await connection.execute(_EXACT_EXIT, action)
    finally:
        dashboard_stepup_proof.reset(token)
    assert (await _entry(pool, "focus"))[1] == 1


async def test_a_finished_step_up_binds_no_further_exit(authority, live):
    pool = authority[0]
    await _entered(pool, "focus")
    action = uuid4()
    binding, proof = await _proof(authority, Exit("focus"), action, 1)
    token = dashboard_stepup_proof.set(proof)
    try:
        async with pool.acquire() as connection:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with connection.transaction():
                    assert await consume_stepup(connection, "begin", binding) == {
                        "state": "admitted"
                    }
                    result = {
                        "version": 1,
                        "kind": MODE_LOOSEN_KIND,
                        "id": str(action),
                        "rule_id": "focus:2",
                    }
                    assert await consume_stepup(connection, "finish", binding, result) == {
                        "state": "completed"
                    }
                    await connection.execute(_EXACT_EXIT, action)
    finally:
        dashboard_stepup_proof.reset(token)
    assert (await _entry(pool, "focus"))[1] == 1


# -- neither target spends on the other ------------------------------------------------


async def _overlay_at_one(pool):
    """Overlay ``focus`` at version 1, so its next loosening targets ``focus:2``."""
    async with pool.acquire() as connection:
        await ModeOverlayWriter(connection).edit(
            uuid4(), EditHolds("focus", frozenset({"proactive"})), 0
        )


async def test_an_overlay_step_up_cannot_exit_an_entry(authority, live):
    pool = authority[0]
    await _overlay_at_one(pool)
    await _entered(pool, "focus")
    action = uuid4()
    loosen = EditHolds("focus", frozenset())
    async with pool.acquire() as connection:
        binding = await overlay_binding(
            connection, loosen, owner_action_id=action, expected_version=1
        )
    assert binding.target.startswith("focus:2:")
    proof = await signed_binding_proof(authority, binding)
    token = dashboard_stepup_proof.set(proof)
    try:
        async with pool.acquire() as connection:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with connection.transaction():
                    assert await consume_stepup(connection, "begin", binding) == {
                        "state": "admitted"
                    }
                    await connection.execute(_EXACT_EXIT, action)
    finally:
        dashboard_stepup_proof.reset(token)
    assert (await _entry(pool, "focus"))[1] == 1
    # Positive control: the same proof still loosens the overlay it was bound to.
    token = dashboard_stepup_proof.set(proof)
    try:
        async with pool.acquire() as connection:
            receipt = await ModeOverlayWriter(connection).edit(action, loosen, 1)
    finally:
        dashboard_stepup_proof.reset(token)
    assert receipt == {"mode": "focus", "version": 2, "verdict": "loosen"}


async def test_an_entry_step_up_cannot_loosen_the_overlay(authority, live):
    pool = authority[0]
    await _overlay_at_one(pool)
    await _entered(pool, "focus")
    action = uuid4()
    binding, proof = await _proof(authority, Exit("focus"), action, 1)
    assert binding.target.startswith("focus:2:")
    token = dashboard_stepup_proof.set(proof)
    try:
        async with pool.acquire() as connection:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with connection.transaction():
                    assert await consume_stepup(connection, "begin", binding) == {
                        "state": "admitted"
                    }
                    # Owner SQL loosening the overlay to version 2 under the entry's step-up.
                    await connection.execute(
                        "UPDATE switchboard.owner_mode_overlay SET version = 2, holds = '{}', "
                        "owner_action_id = $1 WHERE mode = 'focus'",
                        action,
                    )
    finally:
        dashboard_stepup_proof.reset(token)
    async with pool.acquire() as connection:
        assert (
            await connection.fetchval(
                "SELECT version FROM switchboard.owner_mode_overlay WHERE mode = 'focus'"
            )
            == 1
        )
    # Positive control: the same proof still exits the entry it was bound to.
    assert (await _change(pool, Exit("focus"), action, 1, proof))["verdict"] == "loosen"


async def test_a_break_through_overlay_proof_cannot_end_an_entry_early(authority, live):
    """The writer too: an overlay binding is never the binding an entry change re-derives."""
    pool = authority[0]
    await _overlay_at_one(pool)
    await _entered(pool, "focus")
    action = uuid4()
    async with pool.acquire() as connection:
        binding = await overlay_binding(
            connection,
            EditBreakThrough("focus", frozenset({"contact:a"})),
            owner_action_id=action,
            expected_version=1,
        )
    proof = await signed_binding_proof(authority, binding)
    with pytest.raises(ModeStepUpRequired):
        await _change(pool, Exit("focus"), action, 1, proof)
    assert (await _entry(pool, "focus"))[1] == 1
