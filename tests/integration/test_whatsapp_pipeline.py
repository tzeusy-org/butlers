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
    """Return (mock_mcp, tools_dict) where tools_dict captures registered tool functions.

    The tools_dict values are proxies that always delegate to the FakeTool's current .fn,
    so that after apply_approval_gates() wraps a tool, calling tools["name"](...) invokes
    the gate wrapper rather than the original function.
    """

    class FakeTool:
        def __init__(self, name: str, fn: Any) -> None:
            self.name = name
            self.fn = fn

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            """Delegate to the current .fn (which may be gate-wrapped)."""
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
    # Return mcp and its _tools dict as the proxy; callers use tools["name"](...) which
    # always dispatches through the FakeTool's current .fn (gate-wrapped or original).
    return mcp, mcp._tools


def _make_bridge_sse_event(
    *,
    message_id: str = "msg-001",
    chat_jid: str = "1234567890@s.whatsapp.net",
    sender_jid: str = "1234567890@s.whatsapp.net",
    text: str = "Hello from WhatsApp",
    msg_type: str = "text",
) -> dict[str, Any]:
    """Return a minimal bridge SSE event dict matching bridge JSON schema."""
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
    """Create a WhatsAppUserClientConnector with minimal config for testing."""
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
    """Verify the full connector → bridge → Switchboard event flow (mocked)."""

    async def test_connector_buffers_sse_event_from_mock_bridge(self):
        """Events from a mocked bridge SSE stream are buffered per-chat JID.

        Simulates the bridge emitting a single SSE message event and verifies
        that the connector's internal chat buffer is populated before flush.
        """
        connector = _make_connector()

        event = _make_bridge_sse_event(
            message_id="msg-1",
            chat_jid="100@s.whatsapp.net",
            text="Test message",
        )

        # Drive _handle_bridge_event directly (simulates SSE delivery)
        await connector._handle_bridge_event(event)

        # Chat buffer should now hold one message
        assert "100@s.whatsapp.net" in connector._chat_buffers
        buf = connector._chat_buffers["100@s.whatsapp.net"]
        assert len(buf.messages) == 1
        assert buf.messages[0]["message_id"] == "msg-1"

    async def test_connector_submits_batch_to_switchboard_on_flush(self):
        """Flushing a buffered chat submits an ingest.v1 envelope to Switchboard.

        Verifies that _flush_chat_buffer builds and submits a valid ingest.v1
        envelope via the Switchboard MCP client.
        """
        connector = _make_connector()

        # Inject a mock MCP client that captures ingest calls
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(return_value={"status": "ok"})
        connector._mcp_client = mock_mcp

        # Stub out discretion, ingestion policy, and checkpoint (no DB)
        connector._ingestion_policy = MagicMock()
        connector._ingestion_policy.evaluate.return_value = MagicMock(allowed=True)
        connector._ingestion_policy.ensure_loaded = AsyncMock()
        connector._global_ingestion_policy = MagicMock()
        connector._global_ingestion_policy.evaluate.return_value = MagicMock(action="pass")
        connector._global_ingestion_policy.ensure_loaded = AsyncMock()
        connector._discretion_dispatcher = None  # disable discretion for this test
        connector._save_checkpoint = AsyncMock()
        connector._flush_and_drain = AsyncMock()

        event = _make_bridge_sse_event(
            message_id="msg-flush-1",
            chat_jid="200@s.whatsapp.net",
            text="Hello world",
        )
        await connector._handle_bridge_event(event)
        await connector._flush_chat_buffer("200@s.whatsapp.net")

        # Verify Switchboard was called with an ingest.v1 envelope
        mock_mcp.call_tool.assert_awaited_once()
        call_args = mock_mcp.call_tool.call_args
        tool_name = call_args.args[0] if call_args.args else call_args.kwargs.get("tool_name")
        assert tool_name == "ingest", f"Expected 'ingest' tool call, got {tool_name!r}"

        # Verify envelope structure
        payload_arg = (
            call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("arguments")
        )
        assert payload_arg is not None
        envelope = payload_arg.get("envelope") if isinstance(payload_arg, dict) else None
        if envelope is None and isinstance(payload_arg, dict):
            # Some MCP client impls pass envelope as top-level
            envelope = payload_arg
        assert "schema_version" in (envelope or payload_arg or {}), (
            "Expected ingest.v1 schema_version in submitted envelope"
        )

    async def test_events_from_multiple_chats_buffered_separately(self):
        """Events from distinct chat JIDs are buffered in separate ChatBuffers."""
        connector = _make_connector()

        events = [
            _make_bridge_sse_event(message_id="m1", chat_jid="aaa@s.whatsapp.net", text="hi"),
            _make_bridge_sse_event(message_id="m2", chat_jid="bbb@s.whatsapp.net", text="hey"),
            _make_bridge_sse_event(
                message_id="m3", chat_jid="aaa@s.whatsapp.net", text="how are you"
            ),
        ]

        for ev in events:
            await connector._handle_bridge_event(ev)

        assert "aaa@s.whatsapp.net" in connector._chat_buffers
        assert "bbb@s.whatsapp.net" in connector._chat_buffers
        assert len(connector._chat_buffers["aaa@s.whatsapp.net"].messages) == 2
        assert len(connector._chat_buffers["bbb@s.whatsapp.net"].messages) == 1

    async def test_connector_last_event_id_tracked(self):
        """last_event_id is updated to the latest message_id from processed events."""
        connector = _make_connector()

        for i, msg_id in enumerate(["msg-a", "msg-b", "msg-c"]):
            event = _make_bridge_sse_event(
                message_id=msg_id,
                chat_jid=f"chat{i}@s.whatsapp.net",
            )
            await connector._handle_bridge_event(event)

        # Each event updates _last_event_id
        assert connector._last_event_id == "msg-c"

    async def test_bridge_binary_not_found_raises_runtime_error(self):
        """BridgeSubprocessManager raises RuntimeError when binary is missing."""
        from butlers.connectors.bridge_manager import BridgeConfig, BridgeSubprocessManager

        cfg = BridgeConfig(
            binary="non-existent-whatsapp-bridge-XYZ",
            bridge_socket="/tmp/no-such.sock",
            startup_timeout_s=1.0,
        )
        mgr = BridgeSubprocessManager(cfg)

        # Override _connected_event so we don't wait for actual startup
        with pytest.raises(RuntimeError, match="whatsapp-bridge binary not found"):
            await mgr.start()


# ---------------------------------------------------------------------------
# 2. Module tool registration modes
# ---------------------------------------------------------------------------


class TestModuleToolRegistration:
    """Verify WhatsApp module tool registration for all send_tools combinations."""

    async def test_no_tools_registered_when_send_tools_false(self):
        """Default configuration (send_tools=false) registers no MCP tools."""
        module = WhatsAppModule()
        mcp, tools = _make_mock_mcp()

        await module.register_tools(mcp=mcp, config=None, db=None)

        assert len(tools) == 0, f"Expected no tools, got: {list(tools)}"

    async def test_send_tools_true_disabled_registers_present_tools(self):
        """send_tools=true, send_enabled=false: tools registered but return disabled error."""
        module = WhatsAppModule()
        mcp, tools = _make_mock_mcp()

        await module.register_tools(
            mcp=mcp,
            config={"send_tools": True, "send_enabled": False},
            db=None,
        )

        assert "whatsapp_send_message" in tools, "Expected whatsapp_send_message to be registered"
        assert "whatsapp_reply_to_message" in tools, (
            "Expected whatsapp_reply_to_message to be registered"
        )

        # Invoking them should return the disabled error dict
        send_result = await tools["whatsapp_send_message"](recipient="+15551234567", text="hello")
        assert "error" in send_result, "Expected error key in disabled send result"
        assert "send_enabled" in send_result["error"], "Expected send_enabled hint in error"
        assert "ban risk" in send_result["error"].lower(), "Expected ban risk warning in error"

        reply_result = await tools["whatsapp_reply_to_message"](
            chat_jid="15551234567@s.whatsapp.net",
            message_id="orig-msg-id",
            text="reply",
        )
        assert "error" in reply_result
        assert "send_enabled" in reply_result["error"]

    async def test_send_tools_true_enabled_registers_functional_tools(self):
        """send_tools=true, send_enabled=true: tools registered and execute via bridge."""
        module = WhatsAppModule()
        mcp, tools = _make_mock_mcp()

        await module.register_tools(
            mcp=mcp,
            config={"send_tools": True, "send_enabled": True},
            db=None,
        )

        assert "whatsapp_send_message" in tools
        assert "whatsapp_reply_to_message" in tools

        # With send_enabled=true, the tool delegates to _send_message
        mock_send = AsyncMock(return_value={"message_id": "wa-msg-123", "status": "sent"})
        module._send_message = mock_send

        result = await tools["whatsapp_send_message"](recipient="+15551234567", text="hello")
        mock_send.assert_awaited_once_with(recipient="+15551234567", text="hello")
        assert result["message_id"] == "wa-msg-123"

    async def test_no_tools_with_explicit_false_send_tools(self):
        """Explicit send_tools=false registers no tools (same as default)."""
        module = WhatsAppModule()
        mcp, tools = _make_mock_mcp()

        await module.register_tools(
            mcp=mcp,
            config={"send_tools": False, "send_enabled": False},
            db=None,
        )

        assert len(tools) == 0


# ---------------------------------------------------------------------------
# 3. Approval gate: owner auto-approve vs. external pend
# ---------------------------------------------------------------------------


class _GateTestPool:
    """Minimal asyncpg pool mock supporting approval gate operations.

    Mirrors the RoleAwareMockPool pattern from tests/modules/test_approval_gate_role_based.py
    but restricted to WhatsApp JID lookups and pending_action tracking.
    """

    def __init__(self, contacts: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self._contacts: dict[tuple[str, str], dict[str, Any]] = contacts or {}
        self.pending_actions: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_rules: list[dict[str, Any]] = []
        self.approval_events: list[dict[str, Any]] = []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        # public.contact_info JOIN public.contacts — channel-based lookup
        if "public.contact_info" in query and len(args) >= 2:
            key = (str(args[0]), str(args[1]))
            return self._contacts.get(key)
        # pending_actions by ID
        if "pending_actions" in query and args:
            row = self.pending_actions.get(args[0])
            return dict(row) if row else None
        # public.contacts by UUID
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
            # Detect columns: owner path (9 args + decided_by), rule path (10 args),
            # pending path (8 args, no decided_by).
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
    """Verify approval gate routes WhatsApp send to owner (auto-approve) vs. external (pend)."""

    async def test_owner_jid_auto_approves_whatsapp_send(self):
        """whatsapp_send_message to owner JID is auto-approved by gate.py role logic."""
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

        # After wrapping, call via the FakeTool proxy (tools["name"] delegates to .fn)
        result = await tools["whatsapp_send_message"](recipient=owner_jid, text="hi owner")

        # Owner sends should be auto-approved and executed immediately (not pending)
        assert result.get("status") not in (None, "pending_approval"), (
            f"Owner message should be auto-approved, got: {result}"
        )

        # Verify a pending action was recorded with owner auto-approve
        assert len(pool.pending_actions) == 1
        action = next(iter(pool.pending_actions.values()))
        assert action["decided_by"] == "role:owner", (
            f"Expected decided_by='role:owner', got {action['decided_by']!r}"
        )
        assert action["status"] in (
            ActionStatus.APPROVED.value,
            ActionStatus.EXECUTED.value,
        )

    async def test_external_jid_pends_for_whatsapp_send(self):
        """whatsapp_send_message to an unknown (external) JID results in pending approval."""
        from butlers.config import ApprovalConfig, GatedToolConfig
        from butlers.modules.approvals.gate import apply_approval_gates

        external_jid = "19998887777@s.whatsapp.net"

        # No contact registered for external JID — unresolvable → pend
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

        # External/unresolvable targets require approval
        assert result.get("status") == "pending_approval", (
            f"Expected pending_approval for external JID, got: {result}"
        )
        assert "action_id" in result

        # Verify a pending action was created with status=pending and no decided_by
        assert len(pool.pending_actions) == 1
        action = next(iter(pool.pending_actions.values()))
        assert action["status"] == "pending", (
            f"Expected status=pending for external contact, got {action['status']!r}"
        )
        assert action["decided_by"] is None, (
            f"External contact should have no decided_by, got {action['decided_by']!r}"
        )

    async def test_reply_to_owner_chat_jid_auto_approves(self):
        """whatsapp_reply_to_message to owner chat_jid is auto-approved."""
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
        async def whatsapp_reply_to_message(chat_jid: str, message_id: str, text: str) -> dict:
            return {"status": "sent", "chat_jid": chat_jid}

        approval_config = ApprovalConfig(
            enabled=True,
            gated_tools={"whatsapp_reply_to_message": GatedToolConfig(risk_tier="medium")},
        )

        await apply_approval_gates(mcp=mcp, approval_config=approval_config, pool=pool)

        result = await tools["whatsapp_reply_to_message"](
            chat_jid=owner_jid, message_id="msg-42", text="reply"
        )

        # Owner reply should be auto-approved (not pending)
        assert result.get("status") not in (None, "pending_approval"), (
            f"Owner reply should be auto-approved, got: {result}"
        )

        assert len(pool.pending_actions) == 1
        action = next(iter(pool.pending_actions.values()))
        assert action["decided_by"] == "role:owner"
        assert action["status"] in (
            ActionStatus.APPROVED.value,
            ActionStatus.EXECUTED.value,
        )


# ---------------------------------------------------------------------------
# 4. WhatsApp JID resolves to existing contact via phone cross-reference
# ---------------------------------------------------------------------------


class TestWhatsAppJIDResolution:
    """Verify JID → contact resolution including phone-number fallback."""

    def _make_pool_with_rows(self, *rows: dict[str, Any] | None) -> Any:
        """Return a mock asyncpg pool whose fetchrow returns each value in sequence."""
        mock_rows = list(rows)

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=mock_rows)
        return pool

    async def test_jid_resolves_via_direct_whatsapp_jid_match(self):
        """Individual JID with a stored whatsapp_jid contact_info entry resolves directly."""
        contact_id = uuid.uuid4()
        pool = self._make_pool_with_rows(
            {"contact_id": contact_id, "name": "Alice", "roles": [], "entity_id": None}
        )

        result = await resolve_contact_by_channel(
            pool, "whatsapp_jid", "15551234567@s.whatsapp.net"
        )

        assert result is not None
        assert result.contact_id == contact_id
        assert result.name == "Alice"
        # Only one DB query needed (direct hit)
        pool.fetchrow.assert_called_once()

    async def test_jid_resolves_via_phone_cross_reference_on_miss(self):
        """When no direct JID entry, phone cross-reference finds the contact."""
        contact_id = uuid.uuid4()
        pool = self._make_pool_with_rows(
            None,  # direct JID lookup miss
            {"contact_id": contact_id, "name": "Bob", "roles": [], "entity_id": None},
        )

        result = await resolve_contact_by_channel(
            pool, "whatsapp_jid", "15551234567@s.whatsapp.net"
        )

        assert result is not None
        assert result.contact_id == contact_id
        assert result.name == "Bob"
        # Two DB queries: direct JID miss, then phone fallback
        assert pool.fetchrow.call_count == 2
        # Second call uses type='phone' with extracted number
        second_call = pool.fetchrow.call_args_list[1]
        assert "phone" in second_call[0][0]
        assert second_call[0][1] == "15551234567"

    async def test_group_jid_returns_none_no_phone_fallback(self):
        """Group JIDs do not trigger phone fallback and return None."""
        pool = self._make_pool_with_rows(None)

        result = await resolve_contact_by_channel(pool, "whatsapp_jid", "120363012345@g.us")

        assert result is None
        # Only one direct lookup attempted (no phone fallback for group JIDs)
        pool.fetchrow.assert_called_once()

    async def test_both_lookups_miss_returns_none(self):
        """Returns None when both direct JID and phone fallback find no contact."""
        pool = self._make_pool_with_rows(None, None)

        result = await resolve_contact_by_channel(
            pool, "whatsapp_jid", "99999999999@s.whatsapp.net"
        )

        assert result is None
        assert pool.fetchrow.call_count == 2

    async def test_owner_resolved_via_jid(self):
        """Owner contact resolved via whatsapp_jid lookup has owner role."""
        owner_id = uuid.uuid4()
        pool = self._make_pool_with_rows(
            {"contact_id": owner_id, "name": "Owner", "roles": ["owner"], "entity_id": None}
        )

        result = await resolve_contact_by_channel(
            pool, "whatsapp_jid", "15550001111@s.whatsapp.net"
        )

        assert result is not None
        assert "owner" in result.roles

    async def test_owner_resolved_via_phone_fallback(self):
        """Owner contact can be resolved via phone fallback from JID."""
        owner_id = uuid.uuid4()
        pool = self._make_pool_with_rows(
            None,
            {"contact_id": owner_id, "name": "Owner", "roles": ["owner"], "entity_id": None},
        )

        result = await resolve_contact_by_channel(
            pool, "whatsapp_jid", "15550001111@s.whatsapp.net"
        )

        assert result is not None
        assert "owner" in result.roles
        assert pool.fetchrow.call_count == 2


# ---------------------------------------------------------------------------
# 5. Dashboard API: /pair/start and /pair/poll
# ---------------------------------------------------------------------------


class TestDashboardPairAPI:
    """Verify dashboard WhatsApp pairing API endpoints return correct responses."""

    @pytest.fixture
    def whatsapp_app(self):
        """Create the FastAPI app with bridge socket overridden to a test path."""
        app = create_app(api_key="")
        app.dependency_overrides[_get_bridge_socket_path] = lambda: "/tmp/test-wa-bridge.sock"
        yield app
        app.dependency_overrides.clear()

    @pytest.fixture
    async def client(self, whatsapp_app):
        """Return an async httpx test client for the app."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=whatsapp_app),
            base_url="http://test",
        ) as c:
            yield c

    async def test_pair_start_returns_qr_data_uri(self, client):
        """POST /pair/start returns QR data URI and expiry when bridge is reachable."""
        expires = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
        bridge_response = {
            "qr_data_uri": "data:image/png;base64,iVBORw0KGgo=",
            "expires_at": expires,
        }

        with patch(
            "butlers.api.routers.whatsapp._bridge_post",
            new=AsyncMock(return_value=bridge_response),
        ):
            response = await client.post("/api/connectors/whatsapp/pair/start")

        assert response.status_code == 200
        data = response.json()
        assert data["qr_data_uri"].startswith("data:image/png;base64,")
        assert data["expires_at"] is not None
        expires_at = datetime.fromisoformat(data["expires_at"])
        assert expires_at > datetime.now(UTC), "Expiry should be in the future"

    async def test_pair_start_bridge_down_returns_503(self, client):
        """POST /pair/start returns 503 when bridge is unreachable."""
        with patch(
            "butlers.api.routers.whatsapp._bridge_post",
            new=AsyncMock(return_value=None),
        ):
            response = await client.post("/api/connectors/whatsapp/pair/start")

        assert response.status_code == 503
        detail = response.json()["detail"].lower()
        assert "bridge" in detail

    async def test_pair_poll_returns_waiting_while_pairing(self, client):
        """GET /pair/poll returns 'waiting' status while QR has not been scanned."""
        with patch(
            "butlers.api.routers.whatsapp._bridge_get",
            new=AsyncMock(return_value={"status": "waiting"}),
        ):
            response = await client.get("/api/connectors/whatsapp/pair/poll")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "waiting"
        assert data["phone"] is None

    async def test_pair_poll_returns_paired_with_masked_phone(self, client):
        """GET /pair/poll returns 'paired' with masked phone number on successful scan."""
        with patch(
            "butlers.api.routers.whatsapp._bridge_get",
            new=AsyncMock(return_value={"status": "paired", "phone": "+12345677890"}),
        ):
            response = await client.get("/api/connectors/whatsapp/pair/poll")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "paired"
        # Phone should be masked (last 4 visible, middle hidden)
        assert data["phone"] is not None
        assert "7890" in data["phone"]
        assert data["phone"] != "+12345677890", "Raw phone must not be returned"

    async def test_pair_poll_returns_expired_on_qr_expiry(self, client):
        """GET /pair/poll returns 'expired' when QR code has expired without being scanned."""
        with patch(
            "butlers.api.routers.whatsapp._bridge_get",
            new=AsyncMock(return_value={"status": "expired"}),
        ):
            response = await client.get("/api/connectors/whatsapp/pair/poll")

        assert response.status_code == 200
        assert response.json()["status"] == "expired"

    async def test_pair_poll_bridge_down_returns_waiting(self, client):
        """GET /pair/poll returns 'waiting' (not an error) when bridge is unreachable."""
        with patch(
            "butlers.api.routers.whatsapp._bridge_get",
            new=AsyncMock(return_value=None),
        ):
            response = await client.get("/api/connectors/whatsapp/pair/poll")

        assert response.status_code == 200
        assert response.json()["status"] == "waiting"

    async def test_pair_start_default_expiry_when_bridge_omits_it(self, client):
        """POST /pair/start provides a sensible default expiry when bridge omits expires_at."""
        bridge_response = {"qr_data_uri": "data:image/png;base64,abc=="}

        with patch(
            "butlers.api.routers.whatsapp._bridge_post",
            new=AsyncMock(return_value=bridge_response),
        ):
            response = await client.post("/api/connectors/whatsapp/pair/start")

        assert response.status_code == 200
        data = response.json()
        expires_at = datetime.fromisoformat(data["expires_at"])
        assert expires_at > datetime.now(UTC)


# ---------------------------------------------------------------------------
# 6. Message normalization (ingest.v1 field mapping)
# ---------------------------------------------------------------------------


class TestMessageNormalization:
    """Verify normalize_message_text maps bridge event types to correct text."""

    def test_text_message_returns_text(self):
        event = {"type": "text", "content": {"text": "Hello there!"}}
        assert normalize_message_text(event) == "Hello there!"

    def test_image_with_caption(self):
        event = {"type": "image", "content": {"caption": "Check this out"}}
        assert normalize_message_text(event) == "Check this out"

    def test_image_without_caption(self):
        event = {"type": "image", "content": {}}
        assert normalize_message_text(event) == "[image]"

    def test_voice_message(self):
        event = {"type": "voice_note", "content": {}}
        assert normalize_message_text(event) == "[voice message]"

    def test_location_with_name(self):
        event = {
            "type": "location",
            "content": {
                "latitude": 37.7749,
                "longitude": -122.4194,
                "name": "San Francisco",
            },
        }
        result = normalize_message_text(event)
        assert "San Francisco" in result
        assert "[location:" in result

    def test_reaction_message(self):
        event = {
            "type": "reaction",
            "content": {
                "emoji": "👍",
                "target_message_id": "orig-msg-id",
            },
        }
        result = normalize_message_text(event)
        assert "👍" in result
        assert "orig-msg-id" in result

    def test_deleted_message(self):
        event = {"type": "message_deleted", "content": {"deleted_message_id": "msg-789"}}
        assert normalize_message_text(event) == "[message deleted: msg-789]"

    def test_unknown_type_falls_back_to_bracketed_type(self):
        event = {"type": "SomeNewType", "content": {}}
        assert normalize_message_text(event) == "[SomeNewType]"
