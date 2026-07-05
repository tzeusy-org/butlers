"""Real-Postgres regression: remaining pending_actions.tool_args writers must
not double-encode (bu-bstqu — sibling sweep to bu-cymc4 / PR #2924).

PR #2924 fixed gate.py's three ``pending_actions`` INSERTs (and sibling
columns in events.py/journal.py/butler_logging.py/catalogue_bootstrap.py):
every asyncpg pool in this codebase registers a JSONB codec
(``register_jsonb_codec``, ``src/butlers/db.py``) whose encoder already calls
``json.dumps()`` once. Pre-serializing a dict with ``json.dumps()`` before
binding it double-encodes the value into a jsonb-typed STRING instead of an
OBJECT. This bead fixes the remaining writer sites that PR #2924 did not
touch:

- ``daemon.py``'s calendar overlap-approval enqueuer
- ``modules/approvals/email_guard.py``'s three park-path INSERTs
  (``check_email_recipient``'s context-mismatch and no-rule paths, and
  ``check_recipient``'s no-rule path)
- ``api/routers/ingestion_connectors.py``'s disconnect/rotate-token endpoints
- ``api/routers/approvals.py``'s edits-UPDATE path (approve endpoint)
- ``core_tools/_notifications.py``'s notify() no-entity-facts park path

The mocked-pool unit tests already covering these call sites (e.g.
tests/daemon/test_notify_entity_id.py) cannot catch this class of bug — they
never round-trip a value through asyncpg's real JSONB codec. These tests
exercise the real production code paths (daemon method, email_guard
functions, ingestion_connectors endpoints, notify() tool) against a real
Postgres instance (testcontainers), with only unrelated external
dependencies (identity/relationship-schema resolution, connector audit
logging) stubbed or left absent (both fail gracefully to None/no-op by
design). The approvals.py edits-UPDATE path is the one exception: its
enclosing endpoint additionally requires full DatabaseManager/MCPClientManager
plumbing unrelated to this bug, so — matching the precedent set by PR #2925
for the same bug class — that site is covered by reproducing the exact
sanitize-and-bind fragment against a real table instead.

Live-data audit (read-only, butlers-dev, 2026-07-05): 31 pre-existing
``pending_actions`` rows have ``jsonb_typeof(tool_args) = 'string'``
(messenger: 26, relationship: 5) — all in terminal states (executed/approved/
rejected), none pending. The defensive ``isinstance(raw_args, str)`` read-side
workarounds in approvals.py/pipeline.py are therefore kept (see bead notes).
"""

from __future__ import annotations

import json
import shutil
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.api.routers.ingestion_connectors import (
    disconnect_connector,
    rotate_connector_token,
)
from butlers.config import ButlerType
from butlers.core_tools._base import ToolContext
from butlers.core_tools._notifications import register_notification_tools
from butlers.daemon import ButlerDaemon
from butlers.modules.approvals.email_guard import check_email_recipient, check_recipient

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def pending_actions_pool(provisioned_postgres_pool):
    """Provision a fresh database with the approvals tables plus a minimal
    ``connector_registry`` stand-in for the ingestion_connectors endpoints.

    WARNING: kept in sync with tests/modules/conftest.py's ``approvals_pool``
    fixture (same underlying migrations) — update both if the schema drifts.
    """
    async with provisioned_postgres_pool() as pool:
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tool_name TEXT NOT NULL,
                tool_args JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                agent_summary TEXT,
                session_id UUID,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ,
                decided_by TEXT,
                decided_at TIMESTAMPTZ,
                execution_result JSONB,
                why TEXT,
                evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
                approval_rule_id UUID,
                CONSTRAINT pending_actions_status_check
                    CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'executed'))
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS approval_rules (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tool_name TEXT NOT NULL,
                arg_constraints JSONB NOT NULL DEFAULT '{}'::jsonb,
                description TEXT NOT NULL,
                created_from UUID,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ,
                max_uses INTEGER,
                use_count INTEGER NOT NULL DEFAULT 0,
                active BOOLEAN NOT NULL DEFAULT true
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS approval_events (
                event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_id UUID REFERENCES pending_actions(id),
                rule_id UUID REFERENCES approval_rules(id),
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT,
                event_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT approval_events_link_check
                    CHECK (action_id IS NOT NULL OR rule_id IS NOT NULL),
                CONSTRAINT approval_events_type_check
                    CHECK (event_type IN (
                        'action_queued',
                        'action_auto_approved',
                        'action_approved',
                        'action_rejected',
                        'action_expired',
                        'action_execution_succeeded',
                        'action_execution_failed',
                        'rule_created',
                        'rule_revoked'
                    ))
            )
        """)
        # Minimal connector_registry stand-in: only the columns the
        # disconnect/rotate-token endpoints actually query.
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS connector_registry (
                connector_type TEXT NOT NULL,
                endpoint_identity TEXT NOT NULL,
                deleted_at TIMESTAMPTZ,
                PRIMARY KEY (connector_type, endpoint_identity)
            )
        """)
        yield pool


async def _fetch_latest_tool_args(pool, tool_name: str) -> Any:
    row = await pool.fetchrow(
        "SELECT tool_args FROM pending_actions WHERE tool_name = $1 "
        "ORDER BY requested_at DESC LIMIT 1",
        tool_name,
    )
    assert row is not None, f"no pending_actions row found for tool_name={tool_name!r}"
    return row["tool_args"]


class _FakeDbManager:
    """Duck-typed stand-in for DatabaseManager exposing only ``.pool(name)``."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def pool(self, _name: str) -> Any:
        return self._pool


class _FakeCalendarModule:
    name = "calendar"

    def __init__(self) -> None:
        self.enqueuer = None

    def set_approval_enqueuer(self, fn: Any) -> None:
        self.enqueuer = fn


class TestDaemonCalendarOverlapEnqueuer:
    """daemon.py's ``_wire_calendar_approval_enqueuer`` closure (~1454)."""

    async def test_enqueue_overlap_action_roundtrips_tool_args_as_dict(
        self, pending_actions_pool
    ) -> None:
        pool = pending_actions_pool
        daemon = ButlerDaemon.__new__(ButlerDaemon)
        daemon.config = SimpleNamespace(
            modules={"approvals": {"enabled": True, "default_expiry_hours": 1}}
        )
        daemon.db = SimpleNamespace(pool=pool)
        fake_cal = _FakeCalendarModule()
        daemon._modules = [fake_cal]
        daemon._module_statuses = {}

        daemon._wire_calendar_approval_enqueuer()
        assert fake_cal.enqueuer is not None, "calendar overlap enqueuer was not wired"

        event_id = uuid.uuid4()
        tool_args = {"event_id": event_id, "overlap_with": ["evt-1", "evt-2"]}
        action_id_str = await fake_cal.enqueuer(
            "calendar_create_event", tool_args, "overlap needs approval"
        )

        stored = await _fetch_latest_tool_args(pool, "calendar_create_event")
        assert isinstance(stored, dict), (
            f"tool_args arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored["overlap_with"] == ["evt-1", "evt-2"]
        assert stored["event_id"] == str(event_id)

        row = await pool.fetchrow(
            "SELECT id FROM pending_actions WHERE tool_name = $1", "calendar_create_event"
        )
        assert str(row["id"]) == action_id_str


class TestEmailGuardParkPaths:
    """modules/approvals/email_guard.py's three park-path INSERTs (~223, 303, 460)."""

    async def test_context_mismatch_park_roundtrips_tool_args(self, pending_actions_pool) -> None:
        pool = pending_actions_pool
        park_args = {
            "channel": "email",
            "message": "hello",
            "recipient": "person@example.com",
            "meta": {"nested": True},
        }
        with patch(
            "butlers.modules.approvals.email_guard._get_email_context",
            new=AsyncMock(return_value="work"),
        ):
            decision = await check_email_recipient(
                pool,
                email_target="person@example.com",
                rule_tool_name="notify",
                rule_match_args={},
                park_tool_name="notify",
                park_tool_args=park_args,
                park_summary="test park (context mismatch)",
                msg_context="personal",
                butler_name="test-butler",
            )

        assert decision.allowed is False
        assert decision.reason == "parked"

        stored = await _fetch_latest_tool_args(pool, "notify")
        assert isinstance(stored, dict), (
            f"tool_args arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == park_args

    async def test_no_rule_park_email_roundtrips_tool_args(self, pending_actions_pool) -> None:
        pool = pending_actions_pool
        park_args = {"channel": "email", "message": "hello", "recipient": "unknown@example.com"}

        decision = await check_email_recipient(
            pool,
            email_target="unknown@example.com",
            rule_tool_name="notify",
            rule_match_args={},
            park_tool_name="notify",
            park_tool_args=park_args,
            park_summary="test park (no rule)",
            butler_name="test-butler",
        )

        assert decision.allowed is False
        assert decision.reason == "parked"

        stored = await _fetch_latest_tool_args(pool, "notify")
        assert isinstance(stored, dict), (
            f"tool_args arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == park_args

    async def test_no_rule_park_check_recipient_roundtrips_tool_args(
        self, pending_actions_pool
    ) -> None:
        pool = pending_actions_pool
        park_args = {"channel": "telegram", "message": "hello", "chat_id": "12345"}

        decision = await check_recipient(
            pool,
            channel="telegram",
            target="12345",
            rule_tool_name="notify",
            rule_match_args={},
            park_tool_name="notify",
            park_tool_args=park_args,
            park_summary="test park (telegram, no rule)",
            butler_name="test-butler",
        )

        assert decision.allowed is False
        assert decision.reason == "parked"

        stored = await _fetch_latest_tool_args(pool, "notify")
        assert isinstance(stored, dict), (
            f"tool_args arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == park_args


class TestIngestionConnectorsWriters:
    """api/routers/ingestion_connectors.py's disconnect/rotate-token endpoints (~637, ~778)."""

    async def test_disconnect_connector_roundtrips_tool_args(self, pending_actions_pool) -> None:
        pool = pending_actions_pool
        await pool.execute(
            "INSERT INTO connector_registry (connector_type, endpoint_identity) VALUES ($1, $2)",
            "gmail",
            "user@example.com",
        )
        db = _FakeDbManager(pool)
        request = SimpleNamespace(client=None)

        response = await disconnect_connector("gmail", "user@example.com", request, db=db)
        assert response.data["status"] == "pending_approval"

        stored = await _fetch_latest_tool_args(pool, "connector_disconnect")
        assert isinstance(stored, dict), (
            f"tool_args arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == {"connector_type": "gmail", "endpoint_identity": "user@example.com"}

    async def test_rotate_connector_token_roundtrips_tool_args(self, pending_actions_pool) -> None:
        pool = pending_actions_pool
        await pool.execute(
            "INSERT INTO connector_registry (connector_type, endpoint_identity) VALUES ($1, $2)",
            "gmail",
            "user2@example.com",
        )
        db = _FakeDbManager(pool)
        request = SimpleNamespace(client=None)

        response = await rotate_connector_token("gmail", "user2@example.com", request, db=db)
        assert response.data["success"] is True

        stored = await _fetch_latest_tool_args(pool, "connector_rotate_token")
        assert isinstance(stored, dict), (
            f"tool_args arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == {
            "connector_type": "gmail",
            "endpoint_identity": "user2@example.com",
            "is_sensitive": True,
        }


class TestNotifyParkPath:
    """core_tools/_notifications.py's notify() no-entity-facts park path (~683)."""

    async def test_missing_entity_identifier_park_roundtrips_tool_args(
        self, pending_actions_pool
    ) -> None:
        pool = pending_actions_pool
        captured: dict[str, Any] = {}

        def _core_tool(_group: str, **_kwargs: Any):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn

            return deco

        fake_daemon = SimpleNamespace(
            db=SimpleNamespace(pool=pool),
            # Truthy so notify()'s "Switchboard is not connected" guard passes;
            # never actually invoked because owner_identifier resolves to None.
            switchboard_client=SimpleNamespace(),
            _resolve_entity_channel_identifier=AsyncMock(return_value=None),
            _CHANNEL_TO_CONTACT_INFO_TYPE={"telegram": "telegram_chat_id", "email": "email"},
            _resolve_default_notify_recipient=AsyncMock(return_value=None),
        )
        ctx = ToolContext(
            daemon=fake_daemon,
            pool=pool,
            spawner=None,
            butler_name="test-butler",
            butler_type=ButlerType.BUTLER,
            is_switchboard=False,
            is_messenger=False,
            route_metrics=None,
        )
        register_notification_tools(ctx, None, _core_tool)
        notify_fn = captured.get("notify")
        assert notify_fn is not None, "notify() was not registered"

        entity_id = uuid.uuid4()
        result = await notify_fn(
            channel="telegram",
            message="hello there",
            intent="send",
            entity_id=entity_id,
        )
        assert result["status"] == "pending_missing_identifier"

        stored = await _fetch_latest_tool_args(pool, "notify")
        assert isinstance(stored, dict), (
            f"tool_args arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored["channel"] == "telegram"
        assert stored["message"] == "hello there"
        assert stored["entity_id"] == str(entity_id)
        assert stored["intent"] == "send"


class TestApprovalsEditsUpdateFragment:
    """api/routers/approvals.py's edits-UPDATE path in the approve endpoint (~2115-2126).

    The enclosing ``approve_approval`` endpoint additionally requires a fully
    wired ``DatabaseManager`` (multi-butler pool fan-out via
    ``_find_action_pool``) and ``MCPClientManager`` dispatch machinery that is
    unrelated to this bug. Matching the precedent set by bu-x92jw/PR #2925 for
    this exact bug class, this test reproduces the precise sanitize-and-bind
    fragment against a real table rather than driving the whole endpoint.
    """

    async def test_edits_are_merged_and_tool_args_roundtrips_as_dict(
        self, pending_actions_pool
    ) -> None:
        pool = pending_actions_pool
        action_id = uuid.uuid4()
        original_args = {"recipient": "old@example.com", "message": "hi"}
        await pool.execute(
            "INSERT INTO pending_actions (id, tool_name, tool_args, status) "
            "VALUES ($1, $2, $3, $4)",
            action_id,
            "notify",
            original_args,
            "pending",
        )

        extra_uuid = uuid.uuid4()
        edits = {"recipient": "new@example.com", "extra_uuid": extra_uuid}

        # Exact fragment from approvals.py's approve_approval (~2114-2126).
        async with pool.acquire() as conn, conn.transaction():
            action_row = await conn.fetchrow(
                "SELECT tool_name, tool_args FROM pending_actions WHERE id = $1", action_id
            )
            raw_args = action_row["tool_args"]
            tool_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            tool_args.update(edits)
            safe_tool_args = json.loads(json.dumps(tool_args, default=str))
            await conn.execute(
                "UPDATE pending_actions SET tool_args = $1 WHERE id = $2",
                safe_tool_args,
                action_id,
            )

        row = await pool.fetchrow("SELECT tool_args FROM pending_actions WHERE id = $1", action_id)
        stored = row["tool_args"]
        assert isinstance(stored, dict), (
            f"tool_args arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored["recipient"] == "new@example.com"
        assert stored["message"] == "hi"
        assert stored["extra_uuid"] == str(extra_uuid)
