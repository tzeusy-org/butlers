"""The mode-controls options binding spends on the real ledger (bu-dhloi1.21 PR8).

``loosen_binding`` derives the step-up in one transaction; the writer re-derives
it in another, later one, with the database clock moved on. The real trusted
step-up interface admits the proof only if the two bindings are identical.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from butlers.perception.mode_controls import loosen_binding, read_modes
from butlers.perception.modes import EditHolds, Enter, Exit
from tests.integration.test_dashboard_stepup import authority as authority
from tests.integration.test_dashboard_stepup import signed_binding_proof
from tests.integration.test_owner_mode_entries import (
    _change,
    _entered,
    _now,
)
from tests.integration.test_owner_mode_entries import (
    authority_db_url as authority_db_url,
)
from tests.integration.test_owner_mode_entries import live as live

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _options(pool, change, action, version):
    async with pool.acquire() as connection:
        return await loosen_binding(
            connection, change, owner_action_id=action, expected_version=version
        )


async def test_an_options_binding_is_spent_by_a_later_write(authority, live):
    pool = authority[0]
    await _entered(pool, "travel", hours=72)
    action = uuid4()
    binding = await _options(pool, Exit("travel"), action, 1)
    proof = await signed_binding_proof(authority, binding)
    await pool.execute("SELECT pg_sleep(0.05)")  # the spend runs on a later clock
    receipt = await _change(pool, Exit("travel"), action, 1, proof)
    assert receipt == {"mode": "travel", "version": 2, "verdict": "loosen"}


async def test_a_tightening_takes_no_binding_and_the_read_shows_it(authority, live):
    pool = authority[0]
    until = await _now(pool) + timedelta(hours=4)
    enter = Enter("focus", until=until, cause="owner_declared")
    assert await _options(pool, enter, uuid4(), 0) is None
    assert await _options(pool, EditHolds("focus", frozenset({"proactive"})), uuid4(), 0) is None
    await _change(pool, enter, uuid4(), 0)
    async with pool.acquire() as connection:
        modes = {row["mode"]: row for row in await read_modes(connection)}
    assert modes["focus"]["entry"]["until"] == until
    assert modes["focus"]["entry_version"] == 1
    assert modes["sick"]["profile"]["holds"] == ["proactive"]
