"""The mode_loosen step-up kind and the owner mode overlay, end to end (bu-dhloi1.21 PR4b).

Real migrated Switchboard ledger, the real trusted step-up interface and a real
synthetic WebAuthn factor (``authority``). An owner edit to a mode's holds,
break-through senders or duration that tightens is written with no step-up. A
loosening is written only under a fresh step-up bound to that exact resulting
state at that version, spent once in the same transaction as the write. The
finish rule binds the retained result to the mode and its new version.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest

from butlers.core.dashboard_stepup import StepUpBinding, consume_stepup, dashboard_stepup_proof
from butlers.perception import flags
from butlers.perception.mode_overlay import (
    ModeConflict,
    ModeOverlayWriter,
    overlay_binding,
    read_profile,
)
from butlers.perception.modes import (
    MODE_LOOSEN_KIND,
    EditBreakThrough,
    EditDuration,
    EditHolds,
    ModeControlsDark,
    ModeStepUpRequired,
)
from tests.integration.test_dashboard_stepup import authority as authority
from tests.integration.test_dashboard_stepup import signed_binding_proof

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_SWITCHBOARD = {"core": "switchboard", "switchboard": "switchboard"}


@pytest.fixture
def authority_db_url(fresh_db) -> str:
    """``authority`` on the Switchboard ledger, where the overlay lives."""
    return fresh_db("core", "switchboard", schemas=_SWITCHBOARD)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(flags, "MODE_CONTROLS_LIVE", True)


async def _proof(authority, change, action, version):
    async with authority[0].acquire() as connection:
        binding = await overlay_binding(
            connection, change, owner_action_id=action, expected_version=version
        )
    return await signed_binding_proof(authority, binding)


async def _edit(pool, change, action, version, proof=None):
    token = dashboard_stepup_proof.set(proof)
    try:
        async with pool.acquire() as connection:
            return await ModeOverlayWriter(connection).edit(action, change, version)
    finally:
        dashboard_stepup_proof.reset(token)


async def _profile(pool, mode):
    async with pool.acquire() as connection:
        return await read_profile(connection, mode)


async def _bindings(pool):
    return await pool.fetchval(
        "SELECT count(*) FROM dashboard_stepup.bindings WHERE kind = $1", MODE_LOOSEN_KIND
    )


async def test_the_overlay_writer_is_dark(authority):
    assert flags.MODE_CONTROLS_LIVE is False
    with pytest.raises(ModeControlsDark):
        await _edit(authority[0], EditHolds("focus", frozenset({"proactive"})), uuid4(), 0)
    assert (await _profile(authority[0], "focus"))[1] == 0


async def test_a_tightening_is_written_with_no_step_up(authority, live):
    pool = authority[0]
    receipt = await _edit(pool, EditHolds("focus", frozenset({"proactive"})), uuid4(), 0)
    assert receipt == {"mode": "focus", "version": 1, "verdict": "tighten"}
    profile, version = await _profile(pool, "focus")
    assert (profile.holds, version) == (frozenset({"proactive"}), 1)
    longer = await _edit(pool, EditDuration("focus", None), uuid4(), 1)
    assert longer["verdict"] == "none" and (await _profile(pool, "focus"))[1] == 1
    assert await _bindings(pool) == 0


async def test_a_loosening_without_a_step_up_writes_nothing(authority, live):
    pool = authority[0]
    with pytest.raises(ModeStepUpRequired):
        await _edit(pool, EditHolds("sick", frozenset()), uuid4(), 0)
    profile, version = await _profile(pool, "sick")
    assert (profile.holds, version) == (frozenset({"proactive"}), 0)


async def test_a_step_up_loosens_once_and_binds_its_result_to_mode_and_version(authority, live):
    pool = authority[0]
    change = EditBreakThrough("asleep", frozenset({"contact:partner"}))
    action = uuid4()
    proof = await _proof(authority, change, action, 0)
    receipt = await _edit(pool, change, action, 0, proof)
    assert receipt == {"mode": "asleep", "version": 1, "verdict": "loosen"}
    profile, version = await _profile(pool, "asleep")
    assert (profile.break_through, version) == (frozenset({"contact:partner"}), 1)
    stored = await pool.fetchrow(
        "SELECT target, result FROM dashboard_stepup.bindings WHERE kind = $1", MODE_LOOSEN_KIND
    )
    assert stored["target"].startswith("asleep:1:")
    assert stored["result"] == {
        "version": 1,
        "kind": MODE_LOOSEN_KIND,
        "id": str(action),
        "rule_id": "asleep:1",
    }
    # The spent proof cannot loosen again, not even the same mode at its new version.
    more = EditBreakThrough("asleep", frozenset({"contact:a", "contact:partner"}))
    with pytest.raises(ModeStepUpRequired):
        await _edit(pool, more, uuid4(), 1, proof)
    assert (await _profile(pool, "asleep"))[1] == 1


async def test_a_step_up_for_one_edit_cannot_apply_another(authority, live):
    pool = authority[0]
    action = uuid4()
    exact = EditBreakThrough("focus", frozenset({"contact:a"}))
    proof = await _proof(authority, exact, action, 0)
    for other in (
        EditBreakThrough("focus", frozenset({"contact:b"})),
        EditBreakThrough("hosting", frozenset({"contact:a"})),
        EditDuration("focus", timedelta(hours=1)),
    ):
        with pytest.raises(ModeStepUpRequired):
            await _edit(pool, other, action, 0, proof)
    assert (await _profile(pool, "focus"))[1] == 0
    assert (await _profile(pool, "hosting"))[1] == 0
    # The refused attempts did not spend it: the exact loosening still applies.
    assert (await _edit(pool, exact, action, 0, proof))["version"] == 1


async def test_a_stale_step_up_refuses_after_the_mode_changed(authority, live):
    pool = authority[0]
    loosen = EditDuration("focus", timedelta(hours=1))
    proof = await _proof(authority, loosen, uuid4(), 0)
    await _edit(pool, EditHolds("focus", frozenset({"proactive"})), uuid4(), 0)
    with pytest.raises(ModeConflict):
        await _edit(pool, loosen, uuid4(), 0, proof)
    # At the new version the confirmation binds an older state and version.
    with pytest.raises(ModeStepUpRequired):
        await _edit(pool, loosen, uuid4(), 1, proof)
    profile, version = await _profile(pool, "focus")
    assert (profile.duration, version) == (None, 1)


async def test_the_database_refuses_a_loosening_its_step_up_did_not_bind(authority):
    pool = authority[0]
    change = EditHolds("sick", frozenset())
    action = uuid4()
    proof = await _proof(authority, change, action, 0)
    async with pool.acquire() as connection:
        binding = await overlay_binding(
            connection, change, owner_action_id=action, expected_version=0
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                token = dashboard_stepup_proof.set(proof)
                try:
                    assert await consume_stepup(connection, "begin", binding) == {
                        "state": "admitted"
                    }
                finally:
                    dashboard_stepup_proof.reset(token)
                # Owner SQL outside the writer, loosening something the step-up did not bind.
                await connection.execute(
                    "INSERT INTO switchboard.owner_mode_overlay (mode, version, holds, "
                    "break_through, owner_action_id) VALUES ('sick', 1, '{}', "
                    "'{contact:a}', $1)",
                    action,
                )
    assert (await _profile(pool, "sick"))[1] == 0


# Literal values only: every xdist worker must collect the same test IDs.
@pytest.mark.parametrize(
    "rule_id", ["sick:2", "focus:1", "sick", "0e9c3f8a-5b1d-4c2e-9f7a-3d6b8e1c2a40"]
)
async def test_the_finish_rule_binds_the_result_to_the_overlay_target(authority, rule_id):
    pool = authority[0]
    change = EditHolds("sick", frozenset())
    action = uuid4()
    proof = await _proof(authority, change, action, 0)
    token = dashboard_stepup_proof.set(proof)
    try:
        async with pool.acquire() as connection, connection.transaction():
            binding = await overlay_binding(
                connection, change, owner_action_id=action, expected_version=0
            )
            assert await consume_stepup(connection, "begin", binding) == {"state": "admitted"}
            result = {"version": 1, "kind": MODE_LOOSEN_KIND, "id": str(action)}
            refused = await consume_stepup(
                connection, "finish", binding, {**result, "rule_id": rule_id}
            )
            assert refused == {"state": "refused"}
            done = await consume_stepup(
                connection, "finish", binding, {**result, "rule_id": "sick:1"}
            )
            assert done == {"state": "completed"}
    finally:
        dashboard_stepup_proof.reset(token)


# -- the trigger's own checks, with owner SQL that bypasses the writer ---------------

# The exact resulting state ``EditHolds("sick", frozenset())`` binds at version 1.
_EXACT_SICK = (
    "INSERT INTO switchboard.owner_mode_overlay (mode, version, holds, break_through, "
    "owner_action_id) VALUES ('sick', 1, '{}', '{}', $1)"
)


async def _admitted(authority, change, action):
    """A proof and its binding for ``change`` at version 0."""
    proof = await _proof(authority, change, action, 0)
    async with authority[0].acquire() as connection:
        binding = await overlay_binding(
            connection, change, owner_action_id=action, expected_version=0
        )
    return proof, binding


async def test_a_step_up_admitted_in_another_transaction_binds_nothing(authority):
    pool = authority[0]
    action = uuid4()
    proof, binding = await _admitted(authority, EditHolds("sick", frozenset()), action)
    token = dashboard_stepup_proof.set(proof)
    try:
        async with pool.acquire() as connection:
            # Admitted and committed, never finished.
            async with connection.transaction():
                assert await consume_stepup(connection, "begin", binding) == {"state": "admitted"}
            # A later transaction writing exactly the bound state is still refused.
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with connection.transaction():
                    await connection.execute(_EXACT_SICK, action)
    finally:
        dashboard_stepup_proof.reset(token)
    assert await pool.fetchval(
        "SELECT consumed_tx IS NOT NULL AND result IS NULL FROM dashboard_stepup.bindings "
        "WHERE kind = $1",
        MODE_LOOSEN_KIND,
    )
    assert (await _profile(pool, "sick"))[1] == 0


async def test_a_finished_step_up_binds_no_further_write(authority):
    pool = authority[0]
    action = uuid4()
    proof, binding = await _admitted(authority, EditHolds("sick", frozenset()), action)
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
                        "rule_id": "sick:1",
                    }
                    assert await consume_stepup(connection, "finish", binding, result) == {
                        "state": "completed"
                    }
                    # The receipt is retained: the same transaction can no longer spend it.
                    await connection.execute(_EXACT_SICK, action)
    finally:
        dashboard_stepup_proof.reset(token)
    assert (await _profile(pool, "sick"))[1] == 0


_APPROVAL_ID = "5b7e2c1a-8d4f-4a6b-9c3e-1f2a3b4c5d6e"
_RULE_ID = "7c8d9e0f-1a2b-4c3d-8e4f-5a6b7c8d9e0f"


@pytest.mark.parametrize(
    ("kind", "target", "rule_id"),
    [
        # Only the UUID check stops this one: approve_with_rule binds id, not rule_id.
        ("approve_with_rule", _APPROVAL_ID, "x:1"),
        # rule_id equals the target's first field, so again only the UUID check stops it.
        ("policy_loosen", "x:1:never:none", "x"),
        ("policy_loosen", "x:1:never:none", "x:1"),
    ],
)
async def test_the_rule_id_exemption_is_mode_loosen_alone(authority, kind, target, rule_id):
    pool = authority[0]
    namespace = await pool.fetchval("SELECT current_database() || ':' || current_schema()")
    binding = StepUpBinding(kind, target, "a" * 64, "b" * 64, namespace)
    proof = await signed_binding_proof(authority, binding)
    token = dashboard_stepup_proof.set(proof)
    result = {"version": 1, "kind": kind, "id": _APPROVAL_ID}
    try:
        async with pool.acquire() as connection, connection.transaction():
            assert await consume_stepup(connection, "begin", binding) == {"state": "admitted"}
            refused = await consume_stepup(
                connection, "finish", binding, {**result, "rule_id": rule_id}
            )
            assert refused == {"state": "refused"}
            if kind == "approve_with_rule":
                # Positive control: the same finish with a UUID rule_id completes.
                done = await consume_stepup(
                    connection, "finish", binding, {**result, "rule_id": _RULE_ID}
                )
                assert done == {"state": "completed"}
    finally:
        dashboard_stepup_proof.reset(token)
