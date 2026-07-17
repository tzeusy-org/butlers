"""Real-Postgres coverage for versioned autonomy promotion fingerprints."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.modules.approvals.autonomy_suggestions import confirm_suggestion
from butlers.modules.approvals.autonomy_tracker import (
    check_promotion_threshold,
    compute_fingerprint,
    get_approval_count,
    record_approval,
)
from butlers.modules.base import ToolMeta
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["approvals"])


@pytest.fixture
async def approvals_pool(migrated_db_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await pool.execute(
        "TRUNCATE autonomy_suggestions, autonomy_approval_history, approval_events, "
        "pending_actions, approval_rules CASCADE"
    )
    yield pool
    await pool.close()


class _Action:
    def __init__(self, *, action_id: uuid.UUID, tool_args: dict[str, str]) -> None:
        self.id = action_id
        self.tool_name = "send_telegram"
        self.tool_args = tool_args
        self.requested_at = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
        self.decided_at = datetime(2026, 7, 17, 12, 0, 2, tzinfo=UTC)


async def _record(pool: asyncpg.Pool, *, tool_args: dict[str, str], meta: ToolMeta) -> _Action:
    action_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at, decided_at) "
        "VALUES ($1, $2, $3, 'approved', $4, $5)",
        action_id,
        "send_telegram",
        tool_args,
        datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
        datetime(2026, 7, 17, 12, 0, 2, tzinfo=UTC),
    )
    action = _Action(action_id=action_id, tool_args=tool_args)
    await record_approval(pool, action, tool_meta=meta)
    return action


async def test_v2_counts_only_v2_rows_and_pins_the_fingerprinted_args(
    approvals_pool: asyncpg.Pool,
) -> None:
    meta = ToolMeta(arg_sensitivities={"chat_id": True, "text": False})
    first_args = {"chat_id": "mom_123", "text": "hello"}
    second_args = {"chat_id": "mom_123", "text": "running late"}
    fingerprint = compute_fingerprint("send_telegram", first_args, tool_meta=meta)

    await _record(approvals_pool, tool_args=first_args, meta=meta)
    await _record(approvals_pool, tool_args=second_args, meta=meta)

    # A legacy row sharing the resulting hash must not count toward v2 promotion.
    await approvals_pool.execute(
        "INSERT INTO autonomy_approval_history "
        "(pattern_fingerprint, tool_name, tool_args, fingerprint_version) "
        "VALUES ($1, $2, $3, $4)",
        fingerprint,
        "send_telegram",
        first_args,
        1,
    )
    assert await get_approval_count(approvals_pool, fingerprint) == 2

    config = type("Config", (), {"promotion_threshold": 2, "suggestion_cooldown_days": 30})()
    await check_promotion_threshold(
        approvals_pool,
        fingerprint,
        "send_telegram",
        second_args,
        config,
        tool_meta=meta,
    )

    suggestion = await approvals_pool.fetchrow(
        "SELECT * FROM autonomy_suggestions WHERE pattern_fingerprint = $1", fingerprint
    )
    assert suggestion is not None
    assert suggestion["fingerprint_version"] == 2
    assert suggestion["representative_args"] == {"chat_id": "mom_123"}
    assert suggestion["approval_count_at_creation"] == 2

    confirmed = await confirm_suggestion(approvals_pool, suggestion["id"], actor="owner")
    rule = await approvals_pool.fetchrow(
        "SELECT arg_constraints FROM approval_rules WHERE id = $1", confirmed["resulting_rule_id"]
    )
    assert rule is not None
    assert rule["arg_constraints"] == {"chat_id": {"type": "exact", "value": "mom_123"}}
