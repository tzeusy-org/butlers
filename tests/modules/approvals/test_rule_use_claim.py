"""Real-Postgres proof that standing-rule uses are claimed atomically (bu-s11n0s.17).

The gate used to read ``approval_rules`` unlocked and bump ``use_count`` only after
execution, so concurrent calls against a rule with one use left all auto-approved.
These tests drive the real gate wrapper against a real migrated database.  They run
in CI's integration shards only (the author lane has no Docker).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.identity import ResolvedContact
from butlers.modules.approvals.executor import execute_approved_action
from butlers.modules.approvals.gate import _make_gate_wrapper
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.rules import claim_rule_use
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

TOOL = "telegram_send_message"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "approvals"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    db_pool = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=8, init=register_jsonb_codec
    )
    await db_pool.execute("TRUNCATE approval_events, pending_actions, approval_rules CASCADE")
    yield db_pool
    await db_pool.close()


async def _insert_rule(
    pool,
    *,
    max_uses: int | None,
    use_count: int = 0,
    active: bool = True,
    expires_at: datetime | None = None,
) -> uuid.UUID:
    rule_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO approval_rules "
        "(id, tool_name, arg_constraints, description, expires_at, max_uses, use_count, active) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        rule_id,
        TOOL,
        {"chat_id": {"type": "any"}},
        "test rule",
        expires_at,
        max_uses,
        use_count,
        active,
    )
    return rule_id


async def _run_gate_calls(pool, calls: int, *, stale_match: dict | None = None):
    """Run *calls* concurrent gate invocations; return (results, park mock).

    ``stale_match`` makes the gate act on a rule snapshot taken while the rule was
    still usable, regardless of what the row looks like by claim time.
    """
    original_fn = AsyncMock(return_value={"status": "sent"})
    original_fn.__name__ = TOOL
    original_fn.__qualname__ = TOOL
    wrapper = _make_gate_wrapper(
        tool_name=TOOL,
        original_fn=original_fn,
        pool=pool,
        expiry_hours=72,
        risk_tier=MagicMock(value="medium"),
        rule_precedence=("contact_role", "standing_rule"),
        butler_name="home",
    )
    contact = ResolvedContact(entity_id=uuid.uuid4(), name="Friend", roles=["contact"])
    park = AsyncMock()
    stale_snapshot = (
        patch(
            "butlers.modules.approvals.gate.match_standing_rule",
            new=MagicMock(return_value=stale_match),
        )
        if stale_match is not None
        else contextlib.nullcontext()
    )
    with (
        patch(
            "butlers.modules.approvals.gate._resolve_target_contact",
            new=AsyncMock(return_value=contact),
        ),
        patch("butlers.modules.approvals.gate.record_approval_event", new=AsyncMock()),
        patch("butlers.modules.approvals.gate.park_pending_action", new=park),
        patch(
            "butlers.modules.approvals.gate.execute_approved_action",
            new=AsyncMock(return_value=MagicMock(success=True, result={"status": "sent"})),
        ),
        patch("butlers.fleet_events.publish_fleet_event", new=AsyncMock()),
        stale_snapshot,
    ):
        results = await asyncio.gather(
            *(
                wrapper(
                    chat_id=str(i),
                    message="hi",
                    _why="The contact requested this update.",
                    _evidence=[],
                )
                for i in range(calls)
            )
        )
    return results, park


async def _use_count(pool, rule_id) -> int:
    return await pool.fetchval("SELECT use_count FROM approval_rules WHERE id = $1", rule_id)


async def _approved_count(pool, rule_id) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM pending_actions WHERE approval_rule_id = $1 AND status = $2",
        rule_id,
        ActionStatus.APPROVED.value,
    )


@pytest.mark.parametrize("max_uses", [1, 3])
async def test_racing_gate_calls_admit_exactly_max_uses(pool, max_uses: int) -> None:
    rule_id = await _insert_rule(pool, max_uses=max_uses)

    results, park = await _run_gate_calls(pool, calls=8)

    assert sum(1 for r in results if r == {"status": "sent"}) == max_uses
    assert park.await_count == 8 - max_uses
    assert await _approved_count(pool, rule_id) == max_uses
    assert await _use_count(pool, rule_id) == max_uses


@pytest.mark.parametrize(
    "rule_kwargs",
    [
        {"max_uses": 2, "use_count": 2},
        {"max_uses": None, "active": False},
        {"max_uses": None, "expires_at": datetime.now(UTC) - timedelta(hours=1)},
    ],
    ids=["exhausted", "inactive", "expired"],
)
async def test_unusable_rule_parks_without_counting(pool, rule_kwargs) -> None:
    rule_id = await _insert_rule(pool, **rule_kwargs)
    before = await _use_count(pool, rule_id)

    results, park = await _run_gate_calls(pool, calls=1)

    assert results[0] != {"status": "sent"}
    assert park.await_count == 1
    assert await _approved_count(pool, rule_id) == 0
    assert await _use_count(pool, rule_id) == before


async def test_executor_no_longer_counts_the_use(pool) -> None:
    rule_id = await _insert_rule(pool, max_uses=5, use_count=1)  # one use already claimed
    action_id = uuid.uuid4()
    now = datetime.now(UTC)
    await pool.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at, "
        "approval_rule_id, decided_by) VALUES ($1, $2, $3, $4, $5, $6, $7)",
        action_id,
        TOOL,
        {"chat_id": "1"},
        ActionStatus.APPROVED.value,
        now,
        rule_id,
        f"rule:{rule_id}",
    )

    result = await execute_approved_action(
        pool=pool,
        action_id=action_id,
        tool_name=TOOL,
        tool_args={"chat_id": "1"},
        tool_fn=AsyncMock(return_value={"status": "sent"}),
        approval_rule_id=rule_id,
    )

    assert result.success
    assert await _use_count(pool, rule_id) == 1


_UNUSABLE = [
    {"max_uses": 2, "use_count": 2},
    {"max_uses": None, "active": False},
    {"max_uses": None, "expires_at": datetime.now(UTC) - timedelta(hours=1)},
]
_UNUSABLE_IDS = ["exhausted", "inactive", "expired"]


@pytest.mark.parametrize("rule_kwargs", _UNUSABLE, ids=_UNUSABLE_IDS)
async def test_claim_rule_use_refuses_an_unusable_rule_without_counting(pool, rule_kwargs) -> None:
    """The claim statement itself re-checks every usability clause under the row lock."""
    rule_id = await _insert_rule(pool, **rule_kwargs)
    before = await _use_count(pool, rule_id)

    assert await claim_rule_use(pool, rule_id) is False
    assert await _use_count(pool, rule_id) == before


async def test_claim_rule_use_claims_a_usable_rule_once_per_call(pool) -> None:
    """Positive control for the refusals above: the same call succeeds on a usable rule."""
    rule_id = await _insert_rule(
        pool, max_uses=2, expires_at=datetime.now(UTC) + timedelta(hours=1)
    )

    assert [await claim_rule_use(pool, rule_id) for _ in range(3)] == [True, True, False]
    assert await _use_count(pool, rule_id) == 2


@pytest.mark.parametrize("rule_kwargs", _UNUSABLE, ids=_UNUSABLE_IDS)
async def test_gate_parks_when_the_matched_rule_became_unusable_before_the_claim(
    pool, rule_kwargs
) -> None:
    """A rule matched from a usable snapshot but unusable at claim time never auto-approves."""
    rule_id = await _insert_rule(pool, **rule_kwargs)
    snapshot = {
        "id": rule_id,
        "tool_name": TOOL,
        "arg_constraints": {"chat_id": {"type": "any"}},
    }
    before = await _use_count(pool, rule_id)

    results, park = await _run_gate_calls(pool, calls=1, stale_match=snapshot)

    assert results[0] != {"status": "sent"}
    assert park.await_count == 1
    assert await _approved_count(pool, rule_id) == 0
    assert await _use_count(pool, rule_id) == before
