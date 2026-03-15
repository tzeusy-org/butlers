"""Integration tests for email outbound safety — the approval layer between
the butler ecosystem and external non-owner communications.

Simulates the three real-world failure modes that led to unauthorized emails:

1. Non-messenger butlers MUST NOT have email send/reply tools registered.
2. notify(channel="email") MUST reject unknown recipients (hallucination guard).
3. Messenger approval gates MUST block non-owner email targets without a standing rule.

Each test simulates the actual attack surface: a butler LLM attempting to send
email to an address that is NOT the owner.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import (
    ApprovalConfig,
    ApprovalRiskTier,
    GatedToolConfig,
)
from butlers.daemon import ButlerDaemon
from butlers.identity import ResolvedContact
from butlers.modules.approvals.gate import apply_approval_gates
from butlers.modules.email import EmailConfig, EmailModule

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

OWNER_CONTACT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OWNER_EMAIL = "owner@real.com"

KNOWN_NON_OWNER_CONTACT_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
KNOWN_NON_OWNER_EMAIL = "friend@known.com"

HALLUCINATED_EMAIL = "jo@reallylesson.com"
UNKNOWN_EMAIL = "unknown@evil.com"


def _owner_contact() -> ResolvedContact:
    return ResolvedContact(
        contact_id=OWNER_CONTACT_ID,
        name="Owner",
        roles=["owner"],
        entity_id=None,
    )


def _non_owner_contact() -> ResolvedContact:
    return ResolvedContact(
        contact_id=KNOWN_NON_OWNER_CONTACT_ID,
        name="Friend",
        roles=["friend"],
        entity_id=None,
    )


# ---------------------------------------------------------------------------
# RoleAwareMockPool (copied pattern from test_approval_gate_role_based.py)
# ---------------------------------------------------------------------------


class _MockPool:
    """Minimal mock asyncpg pool supporting contact resolution + INSERT capture."""

    def __init__(self) -> None:
        self.pending_actions: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_rules: list[dict[str, Any]] = []
        self.approval_events: list[dict[str, Any]] = []
        self._contact_info: dict[tuple[str, str], ResolvedContact] = {}
        self._contacts_by_id: dict[uuid.UUID, ResolvedContact] = {}

    def register_contact(
        self, channel_type: str, channel_value: str, contact: ResolvedContact
    ) -> None:
        self._contact_info[(channel_type, channel_value)] = contact
        self._contacts_by_id[contact.contact_id] = contact

    async def execute(self, query: str, *args: Any) -> None:
        if "INSERT INTO pending_actions" in query:
            action_id = args[0]
            self.pending_actions[action_id] = {
                "id": action_id,
                "tool_name": args[1],
                "tool_args": args[2],
                "status": args[5] if len(args) > 5 else "pending",
            }
        elif "INSERT INTO approval_events" in query:
            self.approval_events.append(
                {
                    "event_type": args[0],
                    "action_id": args[1],
                    "actor": args[3],
                }
            )
        elif "UPDATE pending_actions" in query and "status" in query:
            if "AND status = $5" in query:
                action_id = args[3]
            else:
                action_id = args[-1]
            if action_id in self.pending_actions:
                self.pending_actions[action_id]["status"] = args[0]

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "approval_rules" in query:
            tool_name = args[0] if args else None
            return [r for r in self.approval_rules if r["tool_name"] == tool_name and r["active"]]
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        # pending_actions lookup (used by executor to check idempotency)
        if "pending_actions" in query and args:
            action_id = args[0]
            row = self.pending_actions.get(action_id)
            return dict(row) if row else None

        if "shared.contact_info" in query and args and len(args) >= 2:
            contact = self._contact_info.get((str(args[0]), str(args[1])))
            if contact is None:
                return None
            return {
                "contact_id": contact.contact_id,
                "name": contact.name,
                "roles": contact.roles,
                "entity_id": contact.entity_id,
            }
        if "shared.contacts" in query and "WHERE id" in query and args:
            try:
                cid = uuid.UUID(str(args[0]))
            except (ValueError, AttributeError):
                return None
            contact = self._contacts_by_id.get(cid)
            if contact is None:
                return None
            return {
                "contact_id": contact.contact_id,
                "name": contact.name,
                "roles": contact.roles,
                "entity_id": contact.entity_id,
            }
        return None

    async def fetchval(self, query: str, *args: Any) -> Any:
        return None


# ---------------------------------------------------------------------------
# Layer 1: Email module tool surface restriction
# ---------------------------------------------------------------------------


class TestEmailToolSurfaceRestriction:
    """Non-messenger butlers MUST NOT have email send/reply tools."""

    async def test_default_config_suppresses_send_tools(self):
        """EmailModule with default config registers only read/search tools."""
        mod = EmailModule()
        mcp = MagicMock()
        registered: dict[str, Any] = {}

        def capture():
            def dec(fn):
                registered[fn.__name__] = fn
                return fn

            return dec

        mcp.tool = capture
        await mod.register_tools(mcp=mcp, config=None, db=None)

        assert "email_send_message" not in registered, (
            "email_send_message MUST NOT be registered without send_tools=true"
        )
        assert "email_reply_to_thread" not in registered, (
            "email_reply_to_thread MUST NOT be registered without send_tools=true"
        )
        assert "email_search_inbox" in registered
        assert "email_read_message" in registered

    async def test_send_tools_true_registers_all_tools(self):
        """EmailModule with send_tools=true registers all tools (messenger only)."""
        mod = EmailModule()
        mcp = MagicMock()
        registered: dict[str, Any] = {}

        def capture():
            def dec(fn):
                registered[fn.__name__] = fn
                return fn

            return dec

        mcp.tool = capture
        await mod.register_tools(mcp=mcp, config={"send_tools": True}, db=None)

        assert "email_send_message" in registered
        assert "email_reply_to_thread" in registered
        assert "email_search_inbox" in registered
        assert "email_read_message" in registered

    def test_send_tools_defaults_to_false(self):
        """EmailConfig.send_tools defaults to False."""
        cfg = EmailConfig()
        assert cfg.send_tools is False


# ---------------------------------------------------------------------------
# Layer 2: notify() email recipient validation
# ---------------------------------------------------------------------------


@pytest.fixture
def butler_dir(tmp_path: Path) -> Path:
    d = tmp_path / "test-butler"
    d.mkdir()
    (d / "butler.toml").write_text(
        '[butler]\nname = "test-butler"\nport = 9100\n'
        'description = "Test"\n\n'
        '[butler.db]\nname = "butlers"\nschema = "test_butler"\n\n'
        "[[butler.schedule]]\n"
        'name = "daily"\ncron = "0 9 * * *"\n'
        'prompt = "Check"\n'
    )
    (d / "MANIFESTO.md").write_text("# Test")
    (d / "CLAUDE.md").write_text("Test.")
    return d


def _make_daemon_patches() -> dict[str, Any]:
    """Create the standard patch set for daemon testing (proven pattern)."""
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.execute = AsyncMock()

    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butlers"

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.daemon.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "configure_logging": patch("butlers.core.logging.configure_logging"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "create_audit_pool": patch.object(
            ButlerDaemon, "_create_audit_pool", new_callable=AsyncMock, return_value=None
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
    }


async def _boot_daemon_with_notify(butler_dir: Path) -> tuple[Any, Any]:
    """Boot a daemon and extract the notify tool function."""
    patches = _make_daemon_patches()
    notify_fn = None
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **_decorator_kwargs):
        def decorator(fn):
            nonlocal notify_fn
            if fn.__name__ == "notify":
                notify_fn = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["configure_logging"],
        patches["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp),
        patches["Spawner"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
        patches["get_adapter"],
        patches["shutil_which"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()
        return daemon, notify_fn


def _mock_switchboard_client() -> Any:
    result = MagicMock()
    result.is_error = False
    result.data = {"status": "sent"}
    result.content = [MagicMock(text='{"status":"sent"}')]
    client = AsyncMock()
    client.call_tool = AsyncMock(return_value=result)
    return client


@pytest.mark.asyncio
class TestNotifyRecipientValidation:
    """notify(channel='email') MUST validate recipients against shared.contact_info."""

    async def test_hallucinated_email_is_parked(self, butler_dir: Path) -> None:
        """Simulates: LLM hallucinates jo@reallylesson.com → MUST be rejected."""
        daemon, notify_fn = await _boot_daemon_with_notify(butler_dir)
        assert notify_fn is not None

        daemon.switchboard_client = _mock_switchboard_client()

        # resolve_contact_by_channel returns None → unknown address
        with patch(
            "butlers.identity.resolve_contact_by_channel",
            new=AsyncMock(return_value=None),
        ):
            result = await notify_fn(
                channel="email",
                message="Your Google AI Pro plan has ended.",
                recipient=HALLUCINATED_EMAIL,
            )

        assert result["status"] == "pending_approval", (
            f"Hallucinated email '{HALLUCINATED_EMAIL}' MUST be parked, "
            f"got status={result.get('status')}"
        )
        assert "pending_action_id" in result
        # Switchboard must NOT have been called
        daemon.switchboard_client.call_tool.assert_not_awaited()

    async def test_unknown_email_is_parked(self, butler_dir: Path) -> None:
        """Any email not in shared.contact_info MUST be parked."""
        daemon, notify_fn = await _boot_daemon_with_notify(butler_dir)
        assert notify_fn is not None

        daemon.switchboard_client = _mock_switchboard_client()

        with patch(
            "butlers.identity.resolve_contact_by_channel",
            new=AsyncMock(return_value=None),
        ):
            result = await notify_fn(
                channel="email",
                message="Follow up on Nutrition Kitchen subscription",
                recipient=UNKNOWN_EMAIL,
            )

        assert result["status"] == "pending_approval"
        daemon.switchboard_client.call_tool.assert_not_awaited()

    async def test_owner_email_is_allowed(self, butler_dir: Path) -> None:
        """Owner's email MUST pass through without parking."""
        daemon, notify_fn = await _boot_daemon_with_notify(butler_dir)
        assert notify_fn is not None

        daemon.switchboard_client = _mock_switchboard_client()

        with patch(
            "butlers.identity.resolve_contact_by_channel",
            new=AsyncMock(return_value=_owner_contact()),
        ):
            result = await notify_fn(
                channel="email",
                message="Your weekly report",
                recipient=OWNER_EMAIL,
            )

        assert result["status"] == "ok", (
            f"Owner email '{OWNER_EMAIL}' MUST be allowed through, "
            f"got status={result.get('status')}"
        )
        daemon.switchboard_client.call_tool.assert_awaited_once()

    async def test_known_non_owner_email_is_allowed(self, butler_dir: Path) -> None:
        """A known non-owner contact MUST also be allowed (they're in the DB)."""
        daemon, notify_fn = await _boot_daemon_with_notify(butler_dir)
        assert notify_fn is not None

        daemon.switchboard_client = _mock_switchboard_client()

        with patch(
            "butlers.identity.resolve_contact_by_channel",
            new=AsyncMock(return_value=_non_owner_contact()),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello friend",
                recipient=KNOWN_NON_OWNER_EMAIL,
            )

        assert result["status"] == "ok"

    async def test_standing_rule_permits_unknown_email(self, butler_dir: Path) -> None:
        """Unknown email WITH a matching standing rule MUST be allowed through."""
        from butlers.modules.approvals.models import ApprovalRule

        daemon, notify_fn = await _boot_daemon_with_notify(butler_dir)
        assert notify_fn is not None

        daemon.switchboard_client = _mock_switchboard_client()

        rule = ApprovalRule(
            id=uuid.uuid4(),
            tool_name="notify",
            arg_constraints={
                "recipient": {"type": "exact", "value": UNKNOWN_EMAIL},
                "channel": {"type": "any"},
                "message": {"type": "any"},
                "intent": {"type": "any"},
            },
            description="Allow emails to unknown@evil.com",
            created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=rule),
            ),
        ):
            result = await notify_fn(
                channel="email",
                message="Permitted message",
                recipient=UNKNOWN_EMAIL,
            )

        assert result["status"] == "ok", (
            f"Unknown email WITH a standing rule MUST be allowed, got status={result.get('status')}"
        )
        daemon.switchboard_client.call_tool.assert_awaited_once()

    async def test_no_standing_rule_still_parks(self, butler_dir: Path) -> None:
        """Unknown email WITHOUT a standing rule MUST still be parked."""
        daemon, notify_fn = await _boot_daemon_with_notify(butler_dir)
        assert notify_fn is not None

        daemon.switchboard_client = _mock_switchboard_client()

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await notify_fn(
                channel="email",
                message="Should be blocked",
                recipient=UNKNOWN_EMAIL,
            )

        assert result["status"] == "pending_approval"
        daemon.switchboard_client.call_tool.assert_not_awaited()

    async def test_telegram_channel_not_affected(self, butler_dir: Path) -> None:
        """Telegram recipients MUST NOT be subject to email validation."""
        daemon, notify_fn = await _boot_daemon_with_notify(butler_dir)
        assert notify_fn is not None

        daemon.switchboard_client = _mock_switchboard_client()

        # Even with resolve returning None, telegram should pass
        mock_resolve = AsyncMock(return_value=None)
        with patch("butlers.identity.resolve_contact_by_channel", new=mock_resolve):
            result = await notify_fn(
                channel="telegram",
                message="Hello",
                recipient="12345",
            )

        assert result["status"] == "ok"
        mock_resolve.assert_not_awaited()


# ---------------------------------------------------------------------------
# Layer 3: Messenger approval gate on email tools
# ---------------------------------------------------------------------------


def _make_mock_mcp() -> MagicMock:
    """Create a mock FastMCP server with tool registration."""
    mock_mcp = MagicMock()
    _tools: dict[str, Any] = {}

    class FakeTool:
        def __init__(self, name: str, fn: Any):
            self.name = name
            self.fn = fn

    async def get_tool(name: str) -> Any:
        return _tools.get(name)

    mock_mcp.get_tool = get_tool

    def tool_decorator(*_a, **_kw):
        def dec(fn):
            _tools[fn.__name__] = FakeTool(fn.__name__, fn)
            return fn

        return dec

    mock_mcp.tool = tool_decorator
    return mock_mcp


@pytest.mark.asyncio
class TestMessengerApprovalGate:
    """Messenger approval gates MUST block non-owner email targets."""

    async def test_email_send_to_unknown_is_parked(self) -> None:
        """email_send_message(to='unknown@evil.com') → pending approval."""
        pool = _MockPool()
        pool.register_contact("email", OWNER_EMAIL, _owner_contact())
        # UNKNOWN_EMAIL is NOT registered

        mcp = _make_mock_mcp()

        # Register the tool on the mock MCP
        @mcp.tool()
        async def email_send_message(to: str, subject: str, body: str) -> dict:
            return {"status": "sent", "to": to}

        config = ApprovalConfig(
            enabled=True,
            gated_tools={
                "email_send_message": GatedToolConfig(risk_tier=ApprovalRiskTier.MEDIUM),
            },
        )

        await apply_approval_gates(mcp, config, pool)

        # Now call the gated tool with an unknown email
        tool = await mcp.get_tool("email_send_message")
        result = await tool.fn(to=UNKNOWN_EMAIL, subject="Test", body="Hello")

        assert result["status"] == "pending_approval", (
            f"email_send_message to unknown address MUST be parked, got: {result}"
        )

    async def test_email_send_to_owner_is_auto_approved(self) -> None:
        """email_send_message(to='owner@real.com') → auto-approved and executed."""
        pool = _MockPool()
        pool.register_contact("email", OWNER_EMAIL, _owner_contact())

        mcp = _make_mock_mcp()

        @mcp.tool()
        async def email_send_message(to: str, subject: str, body: str) -> dict:
            return {"status": "sent", "to": to}

        config = ApprovalConfig(
            enabled=True,
            gated_tools={
                "email_send_message": GatedToolConfig(risk_tier=ApprovalRiskTier.MEDIUM),
            },
        )

        await apply_approval_gates(mcp, config, pool)

        tool = await mcp.get_tool("email_send_message")
        result = await tool.fn(to=OWNER_EMAIL, subject="Report", body="Weekly summary")

        assert result.get("status") == "sent", (
            f"email_send_message to owner MUST be auto-approved, got: {result}"
        )
        assert result.get("to") == OWNER_EMAIL

    async def test_email_reply_to_thread_is_also_gated(self) -> None:
        """email_reply_to_thread MUST be gated (was previously missing)."""
        pool = _MockPool()
        # Register only owner, leave unknown addresses unregistered

        mcp = _make_mock_mcp()

        @mcp.tool()
        async def email_reply_to_thread(
            to: str, thread_id: str, body: str, subject: str | None = None
        ) -> dict:
            return {"status": "sent", "to": to, "thread_id": thread_id}

        config = ApprovalConfig(
            enabled=True,
            gated_tools={
                "email_reply_to_thread": GatedToolConfig(risk_tier=ApprovalRiskTier.MEDIUM),
            },
        )

        await apply_approval_gates(mcp, config, pool)

        tool = await mcp.get_tool("email_reply_to_thread")
        result = await tool.fn(
            to="notification-thealbatrossfile@nlb.gov.sg",
            thread_id="thread-abc",
            body="I've recorded your booking",
        )

        assert result["status"] == "pending_approval", (
            "email_reply_to_thread to unknown address MUST be parked. "
            "This is the exact scenario that caused the Albatross File incident."
        )

    async def test_email_send_to_known_non_owner_without_rule_is_parked(self) -> None:
        """Known non-owner with no standing rule → pending approval."""
        pool = _MockPool()
        pool.register_contact("email", OWNER_EMAIL, _owner_contact())
        pool.register_contact("email", KNOWN_NON_OWNER_EMAIL, _non_owner_contact())

        mcp = _make_mock_mcp()

        @mcp.tool()
        async def email_send_message(to: str, subject: str, body: str) -> dict:
            return {"status": "sent", "to": to}

        config = ApprovalConfig(
            enabled=True,
            gated_tools={
                "email_send_message": GatedToolConfig(risk_tier=ApprovalRiskTier.MEDIUM),
            },
        )

        await apply_approval_gates(mcp, config, pool)

        tool = await mcp.get_tool("email_send_message")
        result = await tool.fn(to=KNOWN_NON_OWNER_EMAIL, subject="Hi", body="Hello friend")

        assert result["status"] == "pending_approval", (
            "Known non-owner WITHOUT a standing rule MUST be parked for approval"
        )


# ---------------------------------------------------------------------------
# Combined scenario: the exact incident replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestIncidentReplay:
    """Replay the exact failure scenarios that occurred on 2026-03-12/14."""

    async def test_travel_butler_cannot_reply_to_albatross_email(self) -> None:
        """Travel butler (no send_tools) tries to reply to Albatross File email.

        Expected: email_reply_to_thread is NOT available as a tool.
        Even if the LLM somehow calls it, the tool doesn't exist.
        """
        mod = EmailModule()
        mcp = MagicMock()
        registered: dict[str, Any] = {}

        def capture():
            def dec(fn):
                registered[fn.__name__] = fn
                return fn

            return dec

        mcp.tool = capture

        # Travel butler config: [modules.email] with NO send_tools
        await mod.register_tools(mcp=mcp, config=None, db=None)

        assert "email_reply_to_thread" not in registered, (
            "Travel butler MUST NOT have email_reply_to_thread. "
            "This is the tool used in the Albatross File Exhibition incident."
        )
        assert "email_send_message" not in registered

    async def test_finance_butler_notify_rejects_hallucinated_jo_email(
        self, butler_dir: Path
    ) -> None:
        """Finance butler notify() with hallucinated jo@reallylesson.com.

        Expected: parked as pending_approval, email never sent.
        """
        daemon, notify_fn = await _boot_daemon_with_notify(butler_dir)
        assert notify_fn is not None

        daemon.switchboard_client = _mock_switchboard_client()

        # jo@reallylesson.com is NOT in shared.contact_info
        with patch(
            "butlers.identity.resolve_contact_by_channel",
            new=AsyncMock(return_value=None),
        ):
            result = await notify_fn(
                channel="email",
                message=(
                    "I've recorded your Albatross File Exhibition booking "
                    "for March 14, 2026 at 6:30 PM SGT."
                ),
                recipient="jo@reallylesson.com",
            )

        assert result["status"] == "pending_approval", (
            "jo@reallylesson.com is NOT a known contact and MUST be rejected. "
            "This is the exact email that was hallucinated in the Google AI Pro incident."
        )
        daemon.switchboard_client.call_tool.assert_not_awaited()

    async def test_messenger_gate_blocks_albatross_reply(self) -> None:
        """Messenger gate blocks reply to notification-thealbatrossfile@nlb.gov.sg.

        Even if the message somehow reaches Messenger, the approval gate on
        email_reply_to_thread MUST park it (defense-in-depth).
        """
        pool = _MockPool()
        pool.register_contact("email", OWNER_EMAIL, _owner_contact())

        mcp = _make_mock_mcp()

        @mcp.tool()
        async def email_reply_to_thread(
            to: str, thread_id: str, body: str, subject: str | None = None
        ) -> dict:
            return {"status": "sent", "to": to, "thread_id": thread_id}

        config = ApprovalConfig(
            enabled=True,
            gated_tools={
                "email_reply_to_thread": GatedToolConfig(risk_tier=ApprovalRiskTier.MEDIUM),
            },
        )

        await apply_approval_gates(mcp, config, pool)

        tool = await mcp.get_tool("email_reply_to_thread")
        result = await tool.fn(
            to="notification-thealbatrossfile@nlb.gov.sg",
            thread_id="thread-albatross",
            body=(
                "I've recorded your Albatross File Exhibition booking "
                "for March 14, 2026 at 6:30 PM SGT."
            ),
        )

        assert result["status"] == "pending_approval", (
            "notification-thealbatrossfile@nlb.gov.sg MUST be blocked. "
            "This is the exact target from the original incident."
        )
