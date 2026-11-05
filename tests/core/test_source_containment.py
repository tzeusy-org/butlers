"""D13 Contain admission control without a database (bu-4c7nvp.1.2).

The step-up store is the authority; these show only that no path reaches the
containment write without its admitted consumption, and what it is bound to.
Real-PG ledger behaviour lives in ``tests/switchboard/test_source_containment_postgres.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from butlers.core.dashboard_stepup import DashboardStepUpProof, dashboard_stepup_proof
from butlers.core.source_containment import STEP_UP_REQUIRED, contain_source

PROOF = DashboardStepUpProof("0" * 64, "A" * 43)


class Connection:
    def __init__(self, consume_state):
        self.consume_state, self.sql = consume_state, []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, *args):
        self.sql.append(sql)

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        if "pg_roles" in sql:
            return {"name": "butler_switchboard_rw", "rolsuper": False, "rolbypassrls": False}
        return None  # not contained yet

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        if "dashboard_stepup.consume" in sql:
            self.consume = args
            return {"state": self.consume_state}
        if "current_database" in sql:
            return "db:switchboard"
        return False


class Pool:
    def __init__(self, connection):
        self.connection = connection
        self.acquired = 0

    @asynccontextmanager
    async def acquire(self):
        self.acquired += 1
        yield self.connection


async def test_no_step_up_proof_never_reaches_the_store():
    pool = Pool(AsyncMock())
    assert await contain_source(pool, "sender-1") == STEP_UP_REQUIRED
    assert pool.acquired == 0


@pytest.mark.parametrize("state", ["refused", "unexpected"])
async def test_an_unadmitted_step_up_writes_no_containment(state):
    connection = Connection(state)
    token = dashboard_stepup_proof.set(PROOF)
    try:
        assert await contain_source(Pool(connection), "sender-1") == STEP_UP_REQUIRED
    finally:
        dashboard_stepup_proof.reset(token)
    assert not any("INSERT" in sql for sql in connection.sql)
    phase, nonce, session, kind, target, request, action, _ = connection.consume
    # Bound to this exact action: kind, partition digest and current state.
    assert (phase, nonce, session, kind) == ("begin", PROOF.nonce, PROOF.session_digest, "contain")
    assert "sender-1" not in target and len(target) == 64
    assert request != action
