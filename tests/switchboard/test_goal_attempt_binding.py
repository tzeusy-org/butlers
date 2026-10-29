"""Returned attempts remain bound even when source dedupe reuses another decision.

This protocol-double regression executes GoalLedger; migrated PostgreSQL owns
constraint, isolation, rollback and concurrency proof in test_goal_ledger_postgres.
"""

from uuid import uuid4

import pytest

from butlers.core.goal_admission import AdmissionUnavailable
from butlers.core.goal_ledger import GoalLedger

pytestmark = pytest.mark.unit


async def test_returned_retry_cannot_retarget_a_durable_source():
    decision = dict(
        id=uuid4(),
        attempt_id=uuid4(),
        source_key="S",
        goal_id=uuid4(),
        result="admitted",
        reason="accepted",
    )
    attempts = {}

    class Connection:
        async def fetchrow(self, sql, identity):
            if "admission_attempt_bindings" in sql:
                return decision if attempts.get(identity) == decision["id"] else None
            if "WHERE attempt_id=" in sql:
                return decision if identity == decision["attempt_id"] else None
            return decision if identity == decision["source_key"] else None

        async def execute(self, sql, *args):
            assert "INSERT INTO switchboard.admission_attempt_bindings" in sql
            attempt, source, canonical = args
            assert source == decision["source_key"] and canonical == decision["id"]
            attempts[attempt] = canonical

    fresh_attempt = uuid4()
    first = await GoalLedger(Connection()).existing("S", decision["attempt_id"])
    retry = await GoalLedger(Connection()).existing("S", fresh_attempt)
    assert retry == first and retry.decision_id == decision["id"]
    # A new ledger/connection must consult durable state, not an instance cache.
    with pytest.raises(AdmissionUnavailable):
        await GoalLedger(Connection()).existing("T", fresh_attempt)
    assert await GoalLedger(Connection()).existing("S", fresh_attempt) == first
