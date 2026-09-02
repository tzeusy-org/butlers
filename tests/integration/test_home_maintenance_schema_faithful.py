"""Real-Postgres proof that ``run_maintenance_schedule_check`` reads
``home.maintenance_items`` schema-qualified, not via search_path (bu-4rif7).

Closes a mock-only coverage gap: ``tests/jobs/test_home.py`` exercises this
read through a hand-rolled mock pool only, so an accidental un-qualification of
``FROM home.maintenance_items`` would pass unit tests but break in production
(the #2598 mocked-green / integration-red class). This test provisions the home
chain into a real ``home`` schema and drives the job through a pool whose
``search_path`` is ``public`` only (the home tables are NOT on the path), so the
read resolves *solely* because it is qualified. Un-qualify it and this test
raises ``UndefinedTableError``.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import create_engine, inspect

from alembic import command
from butlers.core.tool_call_capture import (
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
)
from butlers.db import register_jsonb_codec
from butlers.jobs.home import run_maintenance_schedule_check
from butlers.migrations import _build_alembic_config
from butlers.modules._roster_home import HomeAssistantModule
from butlers.modules._roster_home.actuation import ActuationRisk
from butlers.modules.approvals.execution_context import get_approval_execution_context
from butlers.modules.approvals.module import ApprovalsModule
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def home_db_url(postgres_container) -> str:
    # Faithful topology: core into public, the home domain chain into its own
    # ``home`` schema (mirrors lifecycle.py provisioning the home butler).
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "approvals", "home"],
        schemas={"core": "public", "approvals": "home", "home": "home"},
    )


@pytest.fixture
async def public_pool(home_db_url: str) -> asyncpg.Pool:
    """Pool scoped to ``public`` only — the ``home`` schema is NOT on the path,
    so ``home.maintenance_items`` resolves only via its qualification."""
    p = await asyncpg.create_pool(
        home_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "public"},
    )
    yield p
    await p.close()


@pytest.fixture
async def home_pool(home_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(
        home_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "home,public"},
    )
    yield p
    await p.close()


class _Db:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    def __getattr__(self, name: str):
        return getattr(self.pool, name)


class _Response:
    def __init__(
        self,
        payload: object,
        *,
        error: Exception | None = None,
        json_error: Exception | None = None,
    ) -> None:
        self._payload = payload
        self._error = error
        self._json_error = json_error
        self.content = b"json"

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _HaClient:
    def __init__(self) -> None:
        self.post_calls = 0
        self.post_error: Exception | None = None
        self.post_exception: Exception | None = None
        self.json_error: Exception | None = None
        self.observed_state = "off"

    async def post(self, path: str, *, json: object) -> _Response:
        del path, json
        self.post_calls += 1
        if self.post_exception is not None:
            raise self.post_exception
        return _Response([], error=self.post_error, json_error=self.json_error)

    async def get(self, path: str) -> _Response:
        entity_id = path.removeprefix("/api/states/")
        return _Response(
            {
                "entity_id": entity_id,
                "state": self.observed_state,
                "attributes": {},
                "last_updated": "2026-09-03T00:00:00Z",
            }
        )


async def _seed_item(
    pool: asyncpg.Pool,
    *,
    name: str,
    category: str = "filter",
    interval_days: int = 90,
    next_due_at: datetime | None,
    last_completed_at: datetime | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO home.maintenance_items
            (name, category, interval_days, next_due_at, last_completed_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        name,
        category,
        interval_days,
        next_due_at,
        last_completed_at,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_maintenance_check_reads_home_schema_under_public_search_path(
    public_pool: asyncpg.Pool,
) -> None:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)

    # Sanity: the public-only pool genuinely cannot see the table unqualified.
    with pytest.raises(asyncpg.UndefinedTableError):
        await public_pool.fetch("SELECT id FROM maintenance_items")

    # One overdue, one upcoming, one never-completed -> all three are selected.
    await _seed_item(public_pool, name="overdue filter", next_due_at=now - timedelta(days=10))
    await _seed_item(
        public_pool, name="upcoming hvac", category="hvac", next_due_at=now + timedelta(days=3)
    )
    await _seed_item(public_pool, name="never started", next_due_at=None)

    captured: list[str] = []

    async def notify(text: str) -> None:
        captured.append(text)

    result = await run_maintenance_schedule_check(public_pool, None, notify_fn=notify, _now=now)

    # teeth: the read resolves only because it is schema-qualified to home.*
    assert result["items_checked"] == 3
    assert result["reminders_sent"] == 1
    assert captured, "a reminder should have been produced for the due/overdue items"


@pytest.mark.asyncio(loop_scope="session")
async def test_protected_actuation_requires_approval_and_persists_verified_lineage(
    home_pool: asyncpg.Pool,
) -> None:
    db = _Db(home_pool)
    approvals = ApprovalsModule()
    await approvals.on_startup({}, db)
    home = HomeAssistantModule()
    client = _HaClient()
    client.observed_state = "unlocked"
    home._db = db
    home._client = client

    async def execute(tool_name: str, args: dict[str, object]) -> dict[str, object]:
        assert tool_name == "ha_call_service"
        return await home._call_service(**args)

    approvals.set_tool_executor(execute)
    session_id = uuid.uuid4()
    session_token = set_current_runtime_session_id(str(session_id))
    try:
        parked = await home._call_service("lock", "unlock", target={"entity_id": "lock.front_door"})
    finally:
        reset_current_runtime_session_id(session_token)

    assert parked["status"] == "pending_approval"
    assert client.post_calls == 0

    approved = await approvals._approve_action(
        parked["action_id"],
        actor={"type": "human", "id": "owner", "authenticated": True},
    )
    assert approved["status"] == "executed"
    assert client.post_calls == 1

    receipt = await home_pool.fetchrow(
        "SELECT * FROM ha_command_log WHERE approval_id = $1",
        uuid.UUID(parked["action_id"]),
    )
    assert receipt is not None
    assert receipt["status"] == "succeeded"
    assert receipt["risk"] == "protected"
    assert receipt["actor"] == "human:owner"
    assert receipt["session_id"] == session_id
    assert receipt["approval_id"] == uuid.UUID(parked["action_id"])
    assert receipt["requested_state"]["target"] == {"entity_id": "lock.front_door"}
    assert receipt["observed_state"]["lock.front_door"]["state"] == "unlocked"
    assert get_approval_execution_context() is None
    event = await home_pool.fetchrow(
        "SELECT event_type, payload FROM public.domain_events "
        "WHERE event_type = 'home.actuation_executed' ORDER BY occurred_at DESC LIMIT 1"
    )
    assert event is not None
    assert event["event_type"] == "home.actuation_executed"
    event_payload = event["payload"]
    if isinstance(event_payload, str):
        event_payload = json.loads(event_payload)
    assert event_payload == {
        "attempt_id": str(receipt["attempt_id"]),
        "domain": "lock",
        "service": "unlock",
        "risk": "protected",
        "status": "succeeded",
        "attention_required": False,
    }
    await approvals.on_shutdown()


@pytest.mark.asyncio(loop_scope="session")
async def test_ha_error_and_post_condition_mismatch_never_create_success_receipts(
    home_pool: asyncpg.Pool,
) -> None:
    home = HomeAssistantModule()
    home._db = _Db(home_pool)
    client = _HaClient()
    home._client = client

    client.post_error = RuntimeError("HA refused service")
    with pytest.raises(RuntimeError, match="HA refused service"):
        await home._call_service("light", "turn_on", target={"entity_id": "light.kitchen"})

    failed = await home_pool.fetchrow(
        "SELECT * FROM ha_command_log ORDER BY issued_at DESC LIMIT 1"
    )
    assert failed is not None
    assert failed["status"] == "failed"

    client.post_error = None
    client.observed_state = "off"
    unverified = await home._call_service("light", "turn_on", target={"entity_id": "light.kitchen"})
    assert unverified["status"] == "unverified"
    assert unverified["attention_required"] is True

    mismatch = await home_pool.fetchrow(
        "SELECT * FROM ha_command_log WHERE attempt_id = $1",
        uuid.UUID(unverified["attempt_id"]),
    )
    assert mismatch is not None
    assert mismatch["status"] == "unverified"
    assert mismatch["rollback_hint"]["service"] == "turn_off"
    assert "mismatch" in mismatch["failure_reason"]
    assert (
        await home_pool.fetchval(
            "SELECT count(*) FROM ha_command_log WHERE attempt_id = ANY($1::uuid[]) "
            "AND status = 'succeeded'",
            [failed["attempt_id"], mismatch["attempt_id"]],
        )
        == 0
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_each_reversible_retry_gets_its_own_receipt(home_pool: asyncpg.Pool) -> None:
    home = HomeAssistantModule()
    home._db = _Db(home_pool)
    client = _HaClient()
    client.observed_state = "off"
    home._client = client

    first = await home._call_service("light", "turn_off", target={"entity_id": "light.kitchen"})
    second = await home._call_service("light", "turn_off", target={"entity_id": "light.kitchen"})

    assert first["status"] == second["status"] == "succeeded"
    assert first["attempt_id"] != second["attempt_id"]
    assert client.post_calls == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_accepted_unparseable_response_is_unverified_not_failed(
    home_pool: asyncpg.Pool,
) -> None:
    home = HomeAssistantModule()
    home._db = _Db(home_pool)
    client = _HaClient()
    client.observed_state = "on"
    client.json_error = ValueError("invalid response JSON")
    home._client = client

    outcome = await home._call_service("light", "turn_on", target={"entity_id": "light.kitchen"})

    assert outcome["status"] == "unverified"
    assert outcome["attention_required"] is True
    receipt = await home_pool.fetchrow(
        "SELECT status, observed_state, failure_reason FROM ha_command_log WHERE attempt_id = $1",
        uuid.UUID(outcome["attempt_id"]),
    )
    assert receipt["status"] == "unverified"
    assert receipt["observed_state"]["light.kitchen"]["state"] == "on"
    assert "live post-condition matched" in receipt["failure_reason"]


@pytest.mark.asyncio(loop_scope="session")
async def test_post_send_timeout_is_unverified_and_same_approval_cannot_retry(
    home_pool: asyncpg.Pool,
) -> None:
    db = _Db(home_pool)
    approvals = ApprovalsModule()
    await approvals.on_startup({}, db)
    home = HomeAssistantModule()
    client = _HaClient()
    client.observed_state = "unlocked"
    client.post_exception = TimeoutError("response timed out after send")
    home._db = db
    home._client = client

    async def execute(tool_name: str, args: dict[str, object]) -> dict[str, object]:
        assert tool_name == "ha_call_service"
        return await home._call_service(**args)

    approvals.set_tool_executor(execute)
    parked = await home._call_service("lock", "unlock", target={"entity_id": "lock.front_door"})
    approved = await approvals._approve_action(
        parked["action_id"],
        actor={"type": "human", "id": "owner", "authenticated": True},
    )

    assert approved["status"] == "approved"
    assert client.post_calls == 1
    receipt = await home_pool.fetchrow(
        "SELECT status, observed_state, failure_reason FROM ha_command_log WHERE approval_id = $1",
        uuid.UUID(parked["action_id"]),
    )
    assert receipt["status"] == "unverified"
    assert receipt["observed_state"]["lock.front_door"]["state"] == "unlocked"
    assert "ambiguous Home Assistant outcome" in receipt["failure_reason"]

    retried = await approvals._dispatch_approved_action_by_id(parked["action_id"])
    assert "ambiguous physical outcome" in retried["error"]
    assert client.post_calls == 1
    assert (
        await home_pool.fetchval(
            "SELECT count(*) FROM ha_command_log WHERE approval_id = $1",
            uuid.UUID(parked["action_id"]),
        )
        == 1
    )
    await approvals.on_shutdown()


@pytest.mark.asyncio(loop_scope="session")
async def test_abandoned_attempting_receipt_fences_same_approval_after_crash(
    home_pool: asyncpg.Pool,
) -> None:
    db = _Db(home_pool)
    approvals = ApprovalsModule()
    await approvals.on_startup({}, db)
    home = HomeAssistantModule()
    client = _HaClient()
    client.observed_state = "unlocked"
    home._db = db
    home._client = client

    async def execute(tool_name: str, args: dict[str, object]) -> dict[str, object]:
        assert tool_name == "ha_call_service"
        return await home._call_service(**args)

    approvals.set_tool_executor(execute)
    target = {"entity_id": "lock.front_door"}
    parked = await home._call_service("lock", "unlock", target=target)
    action_id = uuid.UUID(parked["action_id"])
    await home_pool.execute(
        "UPDATE pending_actions SET status = 'approved', decided_by = 'human:owner' WHERE id = $1",
        action_id,
    )

    attempt_id = uuid.uuid4()
    await home._start_actuation_receipt(
        pool=home_pool,
        attempt_id=attempt_id,
        domain="lock",
        service="unlock",
        target=target,
        data=None,
        risk=ActuationRisk.PROTECTED,
        actor="human:owner",
        session_id=None,
        approval_id=action_id,
        requested_state={
            "domain": "lock",
            "service": "unlock",
            "target": target,
            "data": None,
        },
        rollback=None,
    )
    # Simulate the only evidence left after a process dies between the POST and
    # receipt settlement: one request left, while the durable row stays attempting.
    await client.post("/api/services/lock/unlock", json={"target": target})
    assert client.post_calls == 1

    retried = await approvals._dispatch_approved_action_by_id(str(action_id))

    assert "ambiguous physical outcome" in retried["error"]
    assert client.post_calls == 1
    receipt = await home_pool.fetchrow(
        "SELECT status FROM ha_command_log WHERE attempt_id = $1", attempt_id
    )
    assert receipt["status"] == "attempting"
    assert (
        await home_pool.fetchval(
            "SELECT count(*) FROM ha_command_log WHERE approval_id = $1", action_id
        )
        == 1
    )
    await approvals.on_shutdown()


@pytest.mark.asyncio(loop_scope="session")
async def test_succeeded_receipt_replay_finalizes_approval_without_second_post(
    home_pool: asyncpg.Pool,
) -> None:
    db = _Db(home_pool)
    approvals = ApprovalsModule()
    await approvals.on_startup({}, db)
    home = HomeAssistantModule()
    client = _HaClient()
    client.observed_state = "unlocked"
    home._db = db
    home._client = client

    async def execute(tool_name: str, args: dict[str, object]) -> dict[str, object]:
        assert tool_name == "ha_call_service"
        return await home._call_service(**args)

    approvals.set_tool_executor(execute)
    target = {"entity_id": "lock.front_door"}
    parked = await home._call_service("lock", "unlock", target=target)
    action_id = uuid.UUID(parked["action_id"])
    await home_pool.execute(
        "UPDATE pending_actions SET status = 'approved', decided_by = 'human:owner' WHERE id = $1",
        action_id,
    )

    attempt_id = uuid.uuid4()
    requested_state = {
        "domain": "lock",
        "service": "unlock",
        "target": target,
        "data": None,
    }
    stored_result = {"value": [{"context": {"id": "ha-context-1"}}]}
    stored_observed_state = {
        "lock.front_door": {
            "state": "unlocked",
            "attributes": {"friendly_name": "Front Door"},
            "last_updated": "2026-09-03T00:00:00Z",
        }
    }
    await home._start_actuation_receipt(
        pool=home_pool,
        attempt_id=attempt_id,
        domain="lock",
        service="unlock",
        target=target,
        data=None,
        risk=ActuationRisk.PROTECTED,
        actor="human:owner",
        session_id=None,
        approval_id=action_id,
        requested_state=requested_state,
        rollback=None,
    )
    await client.post("/api/services/lock/unlock", json={"target": target})
    await home._settle_actuation_receipt(
        pool=home_pool,
        attempt_id=attempt_id,
        status="succeeded",
        observed_state=stored_observed_state,
        result=stored_result,
        context_id="ha-context-1",
        failure_reason=None,
    )
    assert client.post_calls == 1

    replayed = await approvals._dispatch_approved_action_by_id(str(action_id))

    assert client.post_calls == 1
    assert replayed["status"] == "executed"
    replay_result = replayed["execution_result"]
    assert replay_result["success"] is True
    assert replay_result["result"]["receipt_replay"] is True
    assert replay_result["result"]["attempt_id"] == str(attempt_id)
    assert replay_result["result"]["result"] == stored_result
    assert replay_result["result"]["observed_state"] == stored_observed_state

    receipt = await home_pool.fetchrow(
        "SELECT status, result, observed_state FROM ha_command_log WHERE attempt_id = $1",
        attempt_id,
    )
    assert receipt["status"] == "succeeded"
    assert receipt["result"] == stored_result
    assert receipt["observed_state"] == stored_observed_state
    assert (
        await home_pool.fetchval(
            "SELECT count(*) FROM ha_command_log WHERE approval_id = $1", action_id
        )
        == 1
    )

    persisted_action = await home_pool.fetchrow(
        "SELECT status, execution_result FROM pending_actions WHERE id = $1", action_id
    )
    assert persisted_action["status"] == "executed"
    assert persisted_action["execution_result"] == replay_result
    await approvals.on_shutdown()


def test_home_actuation_receipt_migration_up_down(postgres_container) -> None:
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "home"],
        schemas={"core": "public", "home": "home"},
        revisions={"home": "home_001"},
    )
    config = _build_alembic_config(db_url, ["home"], target_schema="home")

    command.upgrade(config, "home@home_002")
    engine = create_engine(db_url)
    try:
        added = {column["name"] for column in inspect(engine).get_columns("ha_command_log", "home")}
        assert {"attempt_id", "risk", "actor", "approval_id", "status"} <= added

        command.downgrade(config, "home_001")
        downgraded = {
            column["name"] for column in inspect(engine).get_columns("ha_command_log", "home")
        }
        assert "attempt_id" not in downgraded
        assert "status" not in downgraded
    finally:
        engine.dispose()
