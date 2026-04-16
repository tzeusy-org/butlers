"""Integration tests for the WhatsApp pipeline.

Covers the full lifecycle from connector startup to Switchboard ingest submission,
module tool registration modes, approval gate gating, identity resolution, and
dashboard API responses.

Tests are unit-level in terms of dependencies (all external I/O mocked) but
integration-level in that they wire several components together end-to-end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.routers.whatsapp import _get_bridge_socket_path
from butlers.connectors.whatsapp_user_client import (
    WhatsAppUserClientConnector,
    WhatsAppUserClientConnectorConfig,
    normalize_message_text,
)
from butlers.identity import resolve_contact_by_channel
from butlers.modules.whatsapp import WhatsAppModule

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_mock_mcp() -> tuple[Any, dict[str, Any]]:
    """Return (mock_mcp, tools_dict) where tools_dict captures registered tool functions."""

    class FakeTool:
        def __init__(self, name: str, fn: Any) -> None:
            self.name = name
            self.fn = fn

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return self.fn(*args, **kwargs)

    class MockMCP:
        def __init__(self) -> None:
            self._tools: dict[str, FakeTool] = {}

        def tool(self, *_args: Any, **kwargs: Any) -> Any:
            declared_name: str | None = kwargs.get("name")

            def decorator(fn: Any) -> Any:
                tool_name = declared_name or fn.__name__
                fake = FakeTool(tool_name, fn)
                self._tools[tool_name] = fake
                return fn

            return decorator

        async def get_tool(self, name: str) -> FakeTool | None:
            return self._tools.get(name)

    mcp = MockMCP()
    return mcp, mcp._tools


def _make_bridge_sse_event(
    *,
    message_id: str = "msg-001",
    chat_jid: str = "1234567890@s.whatsapp.net",
    sender_jid: str = "1234567890@s.whatsapp.net",
    text: str = "Hello from WhatsApp",
    msg_type: str = "text",
) -> dict[str, Any]:
    return {
        "event_type": "message",
        "message_id": message_id,
        "chat_jid": chat_jid,
        "sender_jid": sender_jid,
        "timestamp": datetime.now(UTC).isoformat(),
        "type": msg_type,
        "content": {"text": text},
    }


def _make_connector(
    switchboard_url: str = "http://switchboard.test/mcp",
    bridge_socket: str = "/tmp/test-wa-bridge.sock",
) -> WhatsAppUserClientConnector:
    config = WhatsAppUserClientConnectorConfig(
        switchboard_mcp_url=switchboard_url,
        provider="whatsapp",
        channel="whatsapp_user_client",
        endpoint_identity="wa:test:+15551234567",
        bridge_socket=bridge_socket,
        flush_interval_s=3600,
        buffer_max_messages=50,
    )
    return WhatsAppUserClientConnector(config=config)


# ---------------------------------------------------------------------------
# 1. Connector starts, bridge connects, events flow to Switchboard ingest
# ---------------------------------------------------------------------------


class TestConnectorBridgeAndIngest:
    async def test_connector_buffers_and_tracks_events(self):
        """Events buffered per-chat JID; last_event_id tracks latest message_id."""
        connector = _make_connector()

        event1 = _make_bridge_sse_event(
            message_id="msg-1", chat_jid="100@s.whatsapp.net", text="Test"
        )
        event2 = _make_bridge_sse_event(
            message_id="msg-2", chat_jid="200@s.whatsapp.net", text="Hey"
        )
        event3 = _make_bridge_sse_event(
            message_id="msg-3", chat_jid="100@s.whatsapp.net", text="More"
        )

        for ev in [event1, event2, event3]:
            await connector._handle_bridge_event(ev)

        assert "100@s.whatsapp.net" in connector._chat_buffers
        assert "200@s.whatsapp.net" in connector._chat_buffers
        assert len(connector._chat_buffers["100@s.whatsapp.net"].messages) == 2
        assert len(connector._chat_buffers["200@s.whatsapp.net"].messages) == 1
        assert connector._last_event_id == "msg-3"

    async def test_connector_submits_batch_to_switchboard_on_flush(self):
        """Flushing a buffered chat submits an ingest.v1 envelope to Switchboard."""
        connector = _make_connector()
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(return_value={"status": "ok"})
        connector._mcp_client = mock_mcp
        connector._ingestion_policy = MagicMock()
        connector._ingestion_policy.evaluate.return_value = MagicMock(allowed=True)
        connector._ingestion_policy.ensure_loaded = AsyncMock()
        connector._global_ingestion_policy = MagicMock()
        connector._global_ingestion_policy.evaluate.return_value = MagicMock(action="pass")
        connector._global_ingestion_policy.ensure_loaded = AsyncMock()
        connector._discretion_dispatcher = None
        connector._save_checkpoint = AsyncMock()
        connector._flush_and_drain = AsyncMock()

        await connector._handle_bridge_event(
            _make_bridge_sse_event(
                message_id="msg-flush-1", chat_jid="200@s.whatsapp.net", text="Hello"
            )
        )
        await connector._flush_chat_buffer("200@s.whatsapp.net")

        mock_mcp.call_tool.assert_awaited_once()
        call_args = mock_mcp.call_tool.call_args
        tool_name = call_args.args[0] if call_args.args else call_args.kwargs.get("tool_name")
        assert tool_name == "ingest"
        payload_arg = (
            call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("arguments")
        )
        envelope = payload_arg.get("envelope") if isinstance(payload_arg, dict) else None
        if envelope is None and isinstance(payload_arg, dict):
            envelope = payload_arg
        assert "schema_version" in (envelope or payload_arg or {})

    async def test_bridge_binary_not_found_raises_runtime_error(self):
        """BridgeSubprocessManager raises RuntimeError when binary is missing."""
        from butlers.connectors.bridge_manager import BridgeConfig, BridgeSubprocessManager

        cfg = BridgeConfig(
            binary="non-existent-whatsapp-bridge-XYZ",
            bridge_socket="/tmp/no-such.sock",
            startup_timeout_s=1.0,
        )
        mgr = BridgeSubprocessManager(cfg)
        with pytest.raises(RuntimeError, match="whatsapp-bridge binary not found"):
            await mgr.start()


# ---------------------------------------------------------------------------
# 2. Module tool registration modes
# ---------------------------------------------------------------------------


class TestModuleToolRegistration:
    async def test_no_tools_when_send_tools_false(self):
        """Default (send_tools=false) and explicit false register no tools."""
        module = WhatsAppModule()
        mcp, tools = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config=None, db=None, butler_name="test-butler")
        assert len(tools) == 0

        await module.register_tools(
            mcp=mcp,
            config={"send_tools": False, "send_enabled": False},
            db=None,
            butler_name="test-butler",
        )
        assert len(tools) == 0

    async def test_tools_registered_when_send_tools_true(self):
        """send_tools=true (disabled/enabled): tools registered; disabled returns error, enabled executes."""
        module = WhatsAppModule()
        mcp, tools = _make_mock_mcp()
        await module.register_tools(
            mcp=mcp,
            config={"send_tools": True, "send_enabled": False},
            db=None,
            butler_name="test-butler",
        )
        assert "whatsapp_send_message" in tools and "whatsapp_reply_to_message" in tools

        send_result = await tools["whatsapp_send_message"](recipient="+15551234567", text="hello")
        assert "error" in send_result and "send_enabled" in send_result["error"]
        assert "ban risk" in send_result["error"].lower()

        # Re-register with send_enabled=true
        module2 = WhatsAppModule()
        mcp2, tools2 = _make_mock_mcp()
        await module2.register_tools(
            mcp=mcp2,
            config={"send_tools": True, "send_enabled": True},
            db=None,
            butler_name="test-butler",
        )
        mock_send = AsyncMock(return_value={"message_id": "wa-msg-123", "status": "sent"})
        module2._send_message = mock_send
        result = await tools2["whatsapp_send_message"](recipient="+15551234567", text="hello")
        mock_send.assert_awaited_once_with(recipient="+15551234567", text="hello")
        assert result["message_id"] == "wa-msg-123"


# ---------------------------------------------------------------------------
# 3. Approval gate: owner auto-approve vs. external pend
# ---------------------------------------------------------------------------


class _GateTestPool:
    def __init__(self, contacts: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self._contacts: dict[tuple[str, str], dict[str, Any]] = contacts or {}
        self.pending_actions: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_rules: list[dict[str, Any]] = []
        self.approval_events: list[dict[str, Any]] = []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "public.contact_info" in query and len(args) >= 2:
            key = (str(args[0]), str(args[1]))
            return self._contacts.get(key)
        if "pending_actions" in query and args:
            row = self.pending_actions.get(args[0])
            return dict(row) if row else None
        if "public.contacts" in query:
            return None
        return None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "approval_rules" in query:
            tool_name = args[0] if args else None
            return [
                dict(r)
                for r in self.approval_rules
                if r.get("active") and (not tool_name or r["tool_name"] == tool_name)
            ]
        return []

    async def execute(self, query: str, *args: Any) -> None:
        if "INSERT INTO pending_actions" in query:
            action_id = args[0]
            if "decided_by" in query and "approval_rule_id" in query:
                status = args[5] if len(args) > 5 else "pending"
                decided_by = args[9] if len(args) > 9 else None
            elif "decided_by" in query:
                status = args[5] if len(args) > 5 else "approved"
                decided_by = args[8] if len(args) > 8 else None
            else:
                status = "pending"
                decided_by = None
            self.pending_actions[action_id] = {
                "id": action_id,
                "tool_name": args[1],
                "tool_args": args[2],
                "status": status,
                "decided_by": decided_by,
            }
        elif "INSERT INTO approval_events" in query:
            self.approval_events.append({"event_type": args[0], "action_id": args[1]})
        elif "UPDATE pending_actions" in query and "status" in query:
            if args:
                action_id = args[-1]
                if action_id in self.pending_actions:
                    self.pending_actions[action_id]["status"] = args[0]
                    if "execution_result" in query and len(args) > 1:
                        self.pending_actions[action_id]["execution_result"] = args[1]


class TestApprovalGateWhatsApp:
    async def test_owner_jid_auto_approves_whatsapp_send(self):
        """whatsapp_send_message to owner JID is auto-approved; external JID pends."""
        from butlers.config import ApprovalConfig, GatedToolConfig
        from butlers.modules.approvals.gate import apply_approval_gates
        from butlers.modules.approvals.models import ActionStatus

        owner_contact_id = uuid.uuid4()
        owner_jid = "15559990000@s.whatsapp.net"

        pool = _GateTestPool(
            contacts={
                ("whatsapp_jid", owner_jid): {
                    "contact_id": owner_contact_id,
                    "name": "Owner",
                    "roles": ["owner"],
                    "entity_id": None,
                }
            }
        )
        mcp, tools = _make_mock_mcp()

        @mcp.tool()
        async def whatsapp_send_message(recipient: str, text: str) -> dict:
            return {"status": "sent", "recipient": recipient}

        approval_config = ApprovalConfig(
            enabled=True,
            gated_tools={"whatsapp_send_message": GatedToolConfig(risk_tier="medium")},
        )
        await apply_approval_gates(mcp=mcp, approval_config=approval_config, pool=pool)
        result = await tools["whatsapp_send_message"](recipient=owner_jid, text="hi owner")
        assert result.get("status") not in (None, "pending_approval")
        action = next(iter(pool.pending_actions.values()))
        assert action["decided_by"] == "role:owner"
        assert action["status"] in (ActionStatus.APPROVED.value, ActionStatus.EXECUTED.value)

    async def test_external_jid_pends_for_whatsapp_send(self):
        """whatsapp_send_message to an unknown (external) JID results in pending approval."""
        from butlers.config import ApprovalConfig, GatedToolConfig
        from butlers.modules.approvals.gate import apply_approval_gates

        external_jid = "19998887777@s.whatsapp.net"
        pool = _GateTestPool()
        mcp, tools = _make_mock_mcp()

        @mcp.tool()
        async def whatsapp_send_message(recipient: str, text: str) -> dict:
            return {"status": "sent", "recipient": recipient}

        approval_config = ApprovalConfig(
            enabled=True,
            gated_tools={"whatsapp_send_message": GatedToolConfig(risk_tier="medium")},
        )
        await apply_approval_gates(mcp=mcp, approval_config=approval_config, pool=pool)
        result = await tools["whatsapp_send_message"](recipient=external_jid, text="hello")
        assert result.get("status") == "pending_approval" and "action_id" in result
        action = next(iter(pool.pending_actions.values()))
        assert action["status"] == "pending" and action["decided_by"] is None


# ---------------------------------------------------------------------------
# 4. WhatsApp JID resolves to existing contact
# ---------------------------------------------------------------------------


class TestWhatsAppJIDResolution:
    def _make_pool_with_rows(self, *rows: dict[str, Any] | None) -> Any:
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=list(rows))
        return pool

    async def test_jid_resolution_paths(self):
        """Direct JID hit, phone fallback on miss, group JID returns None without phone fallback."""
        contact_id = uuid.uuid4()

        # Direct hit
        pool = self._make_pool_with_rows(
            {"contact_id": contact_id, "name": "Alice", "roles": [], "entity_id": None}
        )
        result = await resolve_contact_by_channel(
            pool, "whatsapp_jid", "15551234567@s.whatsapp.net"
        )
        assert result is not None and result.contact_id == contact_id
        pool.fetchrow.assert_called_once()

        # Phone cross-reference fallback
        owner_id = uuid.uuid4()
        pool2 = self._make_pool_with_rows(
            None,
            {"contact_id": owner_id, "name": "Owner", "roles": ["owner"], "entity_id": None},
        )
        result2 = await resolve_contact_by_channel(
            pool2, "whatsapp_jid", "15550001111@s.whatsapp.net"
        )
        assert result2 is not None and "owner" in result2.roles and pool2.fetchrow.call_count == 2

        # Group JID → no phone fallback, returns None
        pool3 = self._make_pool_with_rows(None)
        result3 = await resolve_contact_by_channel(pool3, "whatsapp_jid", "120363012345@g.us")
        assert result3 is None
        pool3.fetchrow.assert_called_once()

    async def test_both_lookups_miss_returns_none(self):
        """Returns None when both direct JID and phone fallback find no contact."""
        pool = self._make_pool_with_rows(None, None)
        result = await resolve_contact_by_channel(
            pool, "whatsapp_jid", "99999999999@s.whatsapp.net"
        )
        assert result is None and pool.fetchrow.call_count == 2


# ---------------------------------------------------------------------------
# 5. Dashboard API: /pair/start and /pair/poll
# ---------------------------------------------------------------------------


class TestDashboardPairAPI:
    @pytest.fixture
    def whatsapp_app(self):
        app = create_app(api_key="")
        app.dependency_overrides[_get_bridge_socket_path] = lambda: "/tmp/test-wa-bridge.sock"
        yield app
        app.dependency_overrides.clear()

    @pytest.fixture
    async def client(self, whatsapp_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=whatsapp_app),
            base_url="http://test",
        ) as c:
            yield c

    async def test_pair_start_success_and_bridge_down(self, client):
        """POST /pair/start returns QR data URI and expiry; 503 when bridge is unreachable."""
        expires = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
        with patch(
            "butlers.api.routers.whatsapp._bridge_post",
            new=AsyncMock(
                return_value={
                    "qr_data_uri": "data:image/png;base64,iVBORw0KGgo=",
                    "expires_at": expires,
                }
            ),
        ):
            response = await client.post("/api/connectors/whatsapp/pair/start")
        assert response.status_code == 200
        data = response.json()
        assert data["qr_data_uri"].startswith("data:image/png;base64,")
        assert datetime.fromisoformat(data["expires_at"]) > datetime.now(UTC)

        with patch("butlers.api.routers.whatsapp._bridge_post", new=AsyncMock(return_value=None)):
            response = await client.post("/api/connectors/whatsapp/pair/start")
        assert response.status_code == 503 and "bridge" in response.json()["detail"].lower()

    async def test_pair_poll_states(self, client):
        """GET /pair/poll returns correct state for waiting/paired/expired/bridge-down."""
        # Waiting
        with patch(
            "butlers.api.routers.whatsapp._bridge_get",
            new=AsyncMock(return_value={"status": "waiting"}),
        ):
            r = await client.get("/api/connectors/whatsapp/pair/poll")
        assert (
            r.status_code == 200 and r.json()["status"] == "waiting" and r.json()["phone"] is None
        )

        # Paired with masked phone
        with patch(
            "butlers.api.routers.whatsapp._bridge_get",
            new=AsyncMock(return_value={"status": "paired", "phone": "+12345677890"}),
        ):
            r = await client.get("/api/connectors/whatsapp/pair/poll")
        data = r.json()
        assert (
            data["status"] == "paired"
            and "7890" in data["phone"]
            and data["phone"] != "+12345677890"
        )

        # Expired
        with patch(
            "butlers.api.routers.whatsapp._bridge_get",
            new=AsyncMock(return_value={"status": "expired"}),
        ):
            r = await client.get("/api/connectors/whatsapp/pair/poll")
        assert r.json()["status"] == "expired"

        # Bridge down → waiting (not error)
        with patch("butlers.api.routers.whatsapp._bridge_get", new=AsyncMock(return_value=None)):
            r = await client.get("/api/connectors/whatsapp/pair/poll")
        assert r.status_code == 200 and r.json()["status"] == "waiting"


# ---------------------------------------------------------------------------
# 6. Message normalization (ingest.v1 field mapping)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event, expected",
    [
        ({"type": "text", "content": {"text": "Hello there!"}}, "Hello there!"),
        ({"type": "image", "content": {"caption": "Check this out"}}, "Check this out"),
        ({"type": "image", "content": {}}, "[image]"),
        ({"type": "voice_note", "content": {}}, "[voice message]"),
        (
            {"type": "message_deleted", "content": {"deleted_message_id": "msg-789"}},
            "[message deleted: msg-789]",
        ),
        ({"type": "SomeNewType", "content": {}}, "[SomeNewType]"),
    ],
)
def test_normalize_message_text(event, expected):
    """normalize_message_text maps bridge event types to correct text."""
    assert normalize_message_text(event) == expected


def test_normalize_message_text_location_and_reaction():
    """Location and reaction messages include expected content in normalized text."""
    location = {
        "type": "location",
        "content": {"latitude": 37.7749, "longitude": -122.4194, "name": "San Francisco"},
    }
    result = normalize_message_text(location)
    assert "San Francisco" in result and "[location:" in result

    reaction = {"type": "reaction", "content": {"emoji": "👍", "target_message_id": "orig-msg-id"}}
    result2 = normalize_message_text(reaction)
    assert "👍" in result2 and "orig-msg-id" in result2
