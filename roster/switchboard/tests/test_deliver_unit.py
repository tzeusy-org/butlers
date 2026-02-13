"""Unit tests for deliver() and notification helpers — no Docker/Postgres required.

These tests mock the asyncpg pool and focus on deliver()'s dispatch logic,
error handling, and notification logging behavior. They complement the
integration tests in test_tools.py which use a real Postgres container.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers: mock pool builder
# ---------------------------------------------------------------------------


def _make_mock_pool(
    *,
    registry_rows: list[dict[str, Any]] | None = None,
    fetchrow_side_effect: list | None = None,
) -> AsyncMock:
    """Build an AsyncMock that behaves like an asyncpg.Pool.

    Parameters
    ----------
    registry_rows:
        Rows to return from ``SELECT name FROM butler_registry WHERE ...``.
        First call returns first row, etc. Each dict is converted to a mock
        with item access (``row["name"]``).
    fetchrow_side_effect:
        If given, overrides fetchrow return values entirely.  Each element
        is returned in sequence across calls.
    """
    pool = AsyncMock()

    if fetchrow_side_effect is not None:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    elif registry_rows is not None:
        row_mocks = []
        for row in registry_rows:
            m = MagicMock()
            m.__getitem__ = lambda self, key, r=row: r[key]
            m.get = lambda key, default=None, r=row: r.get(key, default)
            row_mocks.append(m)

        # fetchrow is called multiple times:
        #  1. butler_registry lookup (from deliver)
        #  2. butler_registry endpoint lookup (from route)
        #  3. notifications INSERT RETURNING id
        pool.fetchrow = AsyncMock(side_effect=row_mocks)
    else:
        pool.fetchrow = AsyncMock(return_value=None)

    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _notif_id_row() -> MagicMock:
    """Return a mock row for ``INSERT INTO notifications ... RETURNING id``."""
    notif_id = str(uuid.uuid4())
    m = MagicMock()
    m.__getitem__ = lambda self, key, nid=notif_id: nid if key == "id" else None
    return m


def _registry_row(name: str, endpoint: str = "http://localhost:9100/sse") -> MagicMock:
    """Return a mock row for butler_registry lookups."""
    data = {"name": name, "endpoint_url": endpoint}
    m = MagicMock()
    m.__getitem__ = lambda self, key, d=data: d[key]
    m.get = lambda key, default=None, d=data: d.get(key, default)
    return m


# ---------------------------------------------------------------------------
# Tests: deliver() — channel validation
# ---------------------------------------------------------------------------


class TestDeliverChannelValidation:
    """deliver() should reject unsupported channels without touching the DB."""

    async def test_unsupported_channel_returns_failed(self) -> None:
        """deliver() returns status=failed for an unsupported channel like 'sms'."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool()

        result = await deliver(pool, channel="sms", message="Hello", recipient="12345")

        assert result["status"] == "failed"
        assert "Unsupported channel" in result["error"]
        assert "'sms'" in result["error"]

    async def test_unsupported_channel_lists_valid_options(self) -> None:
        """Error message should list the supported channels."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool()
        result = await deliver(pool, channel="webhook", message="Hello", recipient="x")

        assert "email" in result["error"]
        assert "telegram" in result["error"]

    async def test_empty_channel_returns_failed(self) -> None:
        """deliver() returns status=failed for an empty channel string."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool()
        result = await deliver(pool, channel="", message="Hello", recipient="x")

        assert result["status"] == "failed"
        assert "Unsupported channel" in result["error"]


# ---------------------------------------------------------------------------
# Tests: deliver() — recipient validation
# ---------------------------------------------------------------------------


class TestDeliverRecipientValidation:
    """deliver() should require a non-empty recipient."""

    async def test_none_recipient_returns_failed(self) -> None:
        """deliver() returns error when recipient is None."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool()
        result = await deliver(pool, channel="telegram", message="Hello", recipient=None)

        assert result["status"] == "failed"
        assert "Recipient is required" in result["error"]

    async def test_empty_string_recipient_returns_failed(self) -> None:
        """deliver() returns error when recipient is empty string."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool()
        result = await deliver(pool, channel="telegram", message="Hello", recipient="")

        assert result["status"] == "failed"
        assert "Recipient is required" in result["error"]


# ---------------------------------------------------------------------------
# Tests: deliver() — module not available (no butler with required module)
# ---------------------------------------------------------------------------


class TestDeliverModuleNotAvailable:
    """deliver() should fail gracefully when no butler has the required module."""

    async def test_no_butler_with_telegram_module(self) -> None:
        """deliver() returns error and logs notification when no butler has telegram."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                # 1. butler_registry module lookup → no match
                None,
                # 2. notifications INSERT RETURNING id
                _notif_id_row(),
            ],
        )

        result = await deliver(
            pool,
            channel="telegram",
            message="Hello",
            recipient="123456",
            source_butler="health",
        )

        assert result["status"] == "failed"
        assert "No butler with 'telegram' module" in result["error"]
        assert "notification_id" in result

    async def test_no_butler_with_email_module(self) -> None:
        """deliver() returns error when no butler has email module."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                None,  # registry lookup
                _notif_id_row(),  # notification insert
            ],
        )

        result = await deliver(
            pool,
            channel="email",
            message="Report",
            recipient="user@example.com",
        )

        assert result["status"] == "failed"
        assert "No butler with 'email' module" in result["error"]


# ---------------------------------------------------------------------------
# Tests: deliver() — successful telegram delivery
# ---------------------------------------------------------------------------


class TestDeliverTelegramSuccess:
    """deliver() should route telegram messages correctly."""

    async def test_telegram_success_returns_sent(self) -> None:
        """deliver() returns status=sent on successful telegram delivery."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                # 1. butler_registry module lookup (deliver)
                _registry_row("chatter", "http://localhost:8103/sse"),
                # 2. butler_registry endpoint lookup (route)
                _registry_row("chatter", "http://localhost:8103/sse"),
                # 3. notifications INSERT RETURNING id
                _notif_id_row(),
            ],
        )

        async def mock_call(endpoint_url, tool_name, args):
            return {"ok": True, "message_id": 42}

        result = await deliver(
            pool,
            channel="telegram",
            message="Hello from health butler!",
            recipient="123456",
            source_butler="health",
            call_fn=mock_call,
        )

        assert result["status"] == "sent"
        assert "notification_id" in result

    async def test_telegram_routes_to_correct_tool(self) -> None:
        """deliver() should call the 'send_message' tool for telegram."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("chatter"),
                _registry_row("chatter"),
                _notif_id_row(),
            ],
        )

        captured: list[dict] = []

        async def mock_call(endpoint_url, tool_name, args):
            captured.append({"tool": tool_name, "args": args, "url": endpoint_url})
            return {"ok": True}

        await deliver(
            pool,
            channel="telegram",
            message="Hi there",
            recipient="99999",
            call_fn=mock_call,
        )

        assert len(captured) == 1
        assert captured[0]["tool"] == "send_message"
        assert captured[0]["args"]["chat_id"] == "99999"
        assert captured[0]["args"]["text"] == "Hi there"

    async def test_telegram_routes_to_correct_endpoint(self) -> None:
        """deliver() should connect to the endpoint of the butler with the module."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("tg-butler", "http://tg-host:9200/sse"),
                _registry_row("tg-butler", "http://tg-host:9200/sse"),
                _notif_id_row(),
            ],
        )

        captured_urls: list[str] = []

        async def mock_call(endpoint_url, tool_name, args):
            captured_urls.append(endpoint_url)
            return {"ok": True}

        await deliver(pool, channel="telegram", message="Test", recipient="123", call_fn=mock_call)

        assert captured_urls == ["http://tg-host:9200/sse"]


# ---------------------------------------------------------------------------
# Tests: deliver() — successful email delivery
# ---------------------------------------------------------------------------


class TestDeliverEmailSuccess:
    """deliver() should route email messages correctly."""

    async def test_email_success_returns_sent(self) -> None:
        """deliver() returns status=sent on successful email delivery."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("mailer"),
                _registry_row("mailer"),
                _notif_id_row(),
            ],
        )

        async def mock_call(endpoint_url, tool_name, args):
            return {"status": "sent"}

        result = await deliver(
            pool,
            channel="email",
            message="Your report is ready.",
            recipient="user@example.com",
            source_butler="health",
            call_fn=mock_call,
        )

        assert result["status"] == "sent"
        assert "notification_id" in result

    async def test_email_routes_to_send_email_tool(self) -> None:
        """deliver() should call the 'send_email' tool for email."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("mailer"),
                _registry_row("mailer"),
                _notif_id_row(),
            ],
        )

        captured: list[dict] = []

        async def mock_call(endpoint_url, tool_name, args):
            captured.append({"tool": tool_name, "args": args})
            return {"status": "sent"}

        await deliver(
            pool,
            channel="email",
            message="Body text",
            recipient="user@example.com",
            metadata={"subject": "Health Report"},
            call_fn=mock_call,
        )

        assert len(captured) == 1
        assert captured[0]["tool"] == "send_email"
        assert captured[0]["args"]["to"] == "user@example.com"
        assert captured[0]["args"]["subject"] == "Health Report"
        assert captured[0]["args"]["body"] == "Body text"

    async def test_email_uses_default_subject_without_metadata(self) -> None:
        """deliver() should use 'Notification' as default email subject."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("mailer"),
                _registry_row("mailer"),
                _notif_id_row(),
            ],
        )

        captured: list[dict] = []

        async def mock_call(endpoint_url, tool_name, args):
            captured.append(args)
            return {"status": "sent"}

        await deliver(
            pool,
            channel="email",
            message="Body",
            recipient="user@example.com",
            call_fn=mock_call,
        )

        assert captured[0]["subject"] == "Notification"

    async def test_email_uses_default_subject_with_empty_metadata(self) -> None:
        """deliver() uses default subject when metadata lacks 'subject' key."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("mailer"),
                _registry_row("mailer"),
                _notif_id_row(),
            ],
        )

        captured: list[dict] = []

        async def mock_call(endpoint_url, tool_name, args):
            captured.append(args)
            return {"status": "sent"}

        await deliver(
            pool,
            channel="email",
            message="Body",
            recipient="user@example.com",
            metadata={"priority": "high"},
            call_fn=mock_call,
        )

        assert captured[0]["subject"] == "Notification"


# ---------------------------------------------------------------------------
# Tests: deliver() — module failure (route raises exception)
# ---------------------------------------------------------------------------


class TestDeliverModuleFailure:
    """deliver() should handle failures during the actual tool call."""

    async def test_route_exception_returns_failed(self) -> None:
        """deliver() returns status=failed when call_fn raises."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("chatter"),
                _registry_row("chatter"),
                # route() logs the failure
                _notif_id_row(),  # notification insert (from deliver error path)
            ],
        )

        async def failing_call(endpoint_url, tool_name, args):
            raise ConnectionError("Telegram API unavailable")

        result = await deliver(
            pool,
            channel="telegram",
            message="Hello",
            recipient="123456",
            source_butler="health",
            call_fn=failing_call,
        )

        assert result["status"] == "failed"
        assert "ConnectionError" in result["error"]
        assert "notification_id" in result

    async def test_timeout_exception_returns_failed(self) -> None:
        """deliver() returns status=failed when call_fn times out."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("chatter"),
                _registry_row("chatter"),
                _notif_id_row(),
            ],
        )

        async def timeout_call(endpoint_url, tool_name, args):
            raise TimeoutError("Request timed out")

        result = await deliver(
            pool,
            channel="telegram",
            message="Hello",
            recipient="123456",
            call_fn=timeout_call,
        )

        assert result["status"] == "failed"
        assert "TimeoutError" in result["error"]

    async def test_generic_exception_returns_failed(self) -> None:
        """deliver() returns status=failed for any unexpected exception."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("chatter"),
                _registry_row("chatter"),
                _notif_id_row(),
            ],
        )

        async def broken_call(endpoint_url, tool_name, args):
            raise RuntimeError("Module crashed unexpectedly")

        result = await deliver(
            pool,
            channel="telegram",
            message="Hello",
            recipient="123456",
            call_fn=broken_call,
        )

        assert result["status"] == "failed"
        assert "RuntimeError" in result["error"]
        assert "Module crashed unexpectedly" in result["error"]


# ---------------------------------------------------------------------------
# Tests: deliver() — notification logging
# ---------------------------------------------------------------------------


class TestDeliverNotificationLogging:
    """deliver() should log all deliveries to the notifications table."""

    async def test_success_logs_sent_notification(self) -> None:
        """On success, deliver() calls log_notification with status='sent'."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("chatter"),
                _registry_row("chatter"),
                _notif_id_row(),
            ],
        )

        async def mock_call(endpoint_url, tool_name, args):
            return {"ok": True}

        with patch(
            "butlers.tools.switchboard.notification.deliver.log_notification",
            new_callable=AsyncMock,
        ) as mock_log:
            mock_log.return_value = str(uuid.uuid4())

            result = await deliver(
                pool,
                channel="telegram",
                message="Hello",
                recipient="123456",
                source_butler="health",
                call_fn=mock_call,
            )

        assert result["status"] == "sent"
        mock_log.assert_awaited_once()
        call_kwargs = mock_log.call_args
        # log_notification is called with positional or keyword args
        # Check the key parameters
        args, kwargs = call_kwargs
        # First positional arg is pool
        assert kwargs.get("status", None) == "sent" or (len(args) > 0 and "sent" in str(args))

    async def test_failure_logs_failed_notification(self) -> None:
        """On route failure, deliver() logs with status='failed' and error."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("chatter"),
                _registry_row("chatter"),
                _notif_id_row(),
            ],
        )

        async def failing_call(endpoint_url, tool_name, args):
            raise ConnectionError("API down")

        with patch(
            "butlers.tools.switchboard.notification.deliver.log_notification",
            new_callable=AsyncMock,
        ) as mock_log:
            mock_log.return_value = str(uuid.uuid4())

            result = await deliver(
                pool,
                channel="telegram",
                message="Hello",
                recipient="123456",
                call_fn=failing_call,
            )

        assert result["status"] == "failed"
        mock_log.assert_awaited_once()
        call_kwargs = mock_log.call_args
        args, kwargs = call_kwargs
        assert kwargs.get("status") == "failed" or "failed" in str(args)
        # Error should be captured
        assert kwargs.get("error") is not None or any("API down" in str(a) for a in args)

    async def test_no_module_failure_logs_notification(self) -> None:
        """When no butler has the module, deliver() still logs a notification."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                None,  # No butler found
                _notif_id_row(),  # notification insert
            ],
        )

        result = await deliver(
            pool,
            channel="telegram",
            message="Hello",
            recipient="123456",
        )

        assert result["status"] == "failed"
        assert "notification_id" in result


# ---------------------------------------------------------------------------
# Tests: deliver() — source_butler parameter
# ---------------------------------------------------------------------------


class TestDeliverSourceButler:
    """deliver() should correctly pass through the source_butler parameter."""

    async def test_default_source_butler_is_switchboard(self) -> None:
        """deliver() defaults source_butler to 'switchboard'."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("chatter"),
                _registry_row("chatter"),
                _notif_id_row(),
            ],
        )

        async def mock_call(endpoint_url, tool_name, args):
            return {"ok": True}

        with patch(
            "butlers.tools.switchboard.notification.deliver.log_notification",
            new_callable=AsyncMock,
        ) as mock_log:
            mock_log.return_value = str(uuid.uuid4())

            await deliver(
                pool,
                channel="telegram",
                message="Test",
                recipient="123",
                call_fn=mock_call,
            )

        mock_log.assert_awaited_once()
        _, kwargs = mock_log.call_args
        assert kwargs.get("source_butler") == "switchboard"

    async def test_custom_source_butler_forwarded(self) -> None:
        """deliver() forwards the custom source_butler to log_notification."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("chatter"),
                _registry_row("chatter"),
                _notif_id_row(),
            ],
        )

        async def mock_call(endpoint_url, tool_name, args):
            return {"ok": True}

        with patch(
            "butlers.tools.switchboard.notification.deliver.log_notification",
            new_callable=AsyncMock,
        ) as mock_log:
            mock_log.return_value = str(uuid.uuid4())

            await deliver(
                pool,
                channel="telegram",
                message="Test",
                recipient="123",
                source_butler="health",
                call_fn=mock_call,
            )

        _, kwargs = mock_log.call_args
        assert kwargs.get("source_butler") == "health"


# ---------------------------------------------------------------------------
# Tests: deliver() — metadata forwarding
# ---------------------------------------------------------------------------


class TestDeliverMetadata:
    """deliver() should forward metadata correctly."""

    async def test_metadata_passed_to_log_notification(self) -> None:
        """deliver() forwards metadata dict to log_notification."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("chatter"),
                _registry_row("chatter"),
                _notif_id_row(),
            ],
        )

        async def mock_call(endpoint_url, tool_name, args):
            return {"ok": True}

        test_metadata = {"priority": "high", "category": "alert"}

        with patch(
            "butlers.tools.switchboard.notification.deliver.log_notification",
            new_callable=AsyncMock,
        ) as mock_log:
            mock_log.return_value = str(uuid.uuid4())

            await deliver(
                pool,
                channel="telegram",
                message="Test",
                recipient="123",
                metadata=test_metadata,
                call_fn=mock_call,
            )

        _, kwargs = mock_log.call_args
        assert kwargs.get("metadata") == test_metadata

    async def test_none_metadata_accepted(self) -> None:
        """deliver() works fine with metadata=None (the default)."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("chatter"),
                _registry_row("chatter"),
                _notif_id_row(),
            ],
        )

        async def mock_call(endpoint_url, tool_name, args):
            return {"ok": True}

        result = await deliver(
            pool,
            channel="telegram",
            message="Test",
            recipient="123",
            metadata=None,
            call_fn=mock_call,
        )

        assert result["status"] == "sent"


# ---------------------------------------------------------------------------
# Tests: _build_channel_args (unit tests, no DB needed)
# ---------------------------------------------------------------------------


class TestBuildChannelArgs:
    """Unit tests for the _build_channel_args helper."""

    def test_telegram_args(self) -> None:
        """telegram channel produces chat_id + text args."""
        from butlers.tools.switchboard import _build_channel_args

        result = _build_channel_args("telegram", "Hello!", "123456")
        assert result == {"chat_id": "123456", "text": "Hello!"}

    def test_email_args_default_subject(self) -> None:
        """email channel with no metadata uses 'Notification' subject."""
        from butlers.tools.switchboard import _build_channel_args

        result = _build_channel_args("email", "Body text", "user@example.com")
        assert result == {"to": "user@example.com", "subject": "Notification", "body": "Body text"}

    def test_email_args_custom_subject(self) -> None:
        """email channel uses subject from metadata."""
        from butlers.tools.switchboard import _build_channel_args

        result = _build_channel_args(
            "email", "Body", "user@example.com", metadata={"subject": "Custom"}
        )
        assert result["subject"] == "Custom"

    def test_unsupported_channel_raises(self) -> None:
        """Unsupported channel raises ValueError."""
        from butlers.tools.switchboard import _build_channel_args

        with pytest.raises(ValueError, match="Unsupported channel"):
            _build_channel_args("sms", "Hello", "12345")

    def test_email_empty_metadata(self) -> None:
        """email channel with empty metadata dict uses default subject."""
        from butlers.tools.switchboard import _build_channel_args

        result = _build_channel_args("email", "Body", "user@example.com", metadata={})
        assert result["subject"] == "Notification"


# ---------------------------------------------------------------------------
# Tests: deliver() — result data forwarding
# ---------------------------------------------------------------------------


class TestDeliverResultData:
    """deliver() should forward the route result data on success."""

    async def test_success_includes_route_result(self) -> None:
        """On success, deliver() includes the route result under 'result' key."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                _registry_row("chatter"),
                _registry_row("chatter"),
                _notif_id_row(),
            ],
        )

        route_data = {"ok": True, "message_id": 42, "chat_id": "123456"}

        async def mock_call(endpoint_url, tool_name, args):
            return route_data

        result = await deliver(
            pool,
            channel="telegram",
            message="Hello",
            recipient="123456",
            call_fn=mock_call,
        )

        assert result["status"] == "sent"
        assert result["result"] == route_data

    async def test_failure_does_not_include_result(self) -> None:
        """On failure, deliver() includes 'error' but not 'result'."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                None,  # No butler found
                _notif_id_row(),
            ],
        )

        result = await deliver(pool, channel="telegram", message="Hello", recipient="123456")

        assert result["status"] == "failed"
        assert "error" in result
        assert "result" not in result


# ---------------------------------------------------------------------------
# Tests: _call_butler_tool (unit tests, no DB needed)
# ---------------------------------------------------------------------------


class TestCallButlerTool:
    """Unit tests for direct MCP calls in routing._call_butler_tool."""

    @pytest.fixture(autouse=True)
    async def _reset_router_client_cache(self):
        from butlers.tools.switchboard.routing.route import _reset_router_client_cache_for_tests

        await _reset_router_client_cache_for_tests()
        yield
        await _reset_router_client_cache_for_tests()

    async def test_uses_mcp_client_and_returns_data(self) -> None:
        """_call_butler_tool should call FastMCP and return result.data."""
        from butlers.tools.switchboard import _call_butler_tool

        mock_result = SimpleNamespace(is_error=False, data={"ok": True}, content=[])
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)

        mock_ctor = MagicMock()
        mock_ctx = mock_ctor.return_value
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("butlers.tools.switchboard.routing.route.MCPClient", mock_ctor):
            result = await _call_butler_tool("http://localhost:8101/sse", "handle_message", {})

        assert result == {"ok": True}
        mock_ctor.assert_called_once_with("http://localhost:8101/sse", name="switchboard-router")
        mock_client.call_tool.assert_awaited_once_with("handle_message", {}, raise_on_error=False)

    async def test_reuses_cached_client_for_same_endpoint(self) -> None:
        """Consecutive calls should reuse a healthy cached router client."""
        from butlers.tools.switchboard import _call_butler_tool

        result_one = SimpleNamespace(is_error=False, data={"n": 1}, content=[])
        result_two = SimpleNamespace(is_error=False, data={"n": 2}, content=[])

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=[result_one, result_two])

        mock_ctor = MagicMock()
        mock_ctx = mock_ctor.return_value
        mock_ctx.is_connected = MagicMock(return_value=True)
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("butlers.tools.switchboard.routing.route.MCPClient", mock_ctor):
            first = await _call_butler_tool("http://localhost:8101/sse", "handle_message", {})
            second = await _call_butler_tool("http://localhost:8101/sse", "handle_message", {})

        assert first == {"n": 1}
        assert second == {"n": 2}
        mock_ctor.assert_called_once_with("http://localhost:8101/sse", name="switchboard-router")
        assert mock_client.call_tool.await_count == 2

    async def test_reconnects_when_cached_client_disconnected(self) -> None:
        """A disconnected cached client should be replaced on the next call."""
        from butlers.tools.switchboard import _call_butler_tool

        first_client = AsyncMock()
        first_client.call_tool = AsyncMock(
            return_value=SimpleNamespace(is_error=False, data={"step": 1}, content=[])
        )
        second_client = AsyncMock()
        second_client.call_tool = AsyncMock(
            return_value=SimpleNamespace(is_error=False, data={"step": 2}, content=[])
        )

        first_ctx = MagicMock()
        first_ctx.is_connected = MagicMock(return_value=True)
        first_ctx.__aenter__ = AsyncMock(return_value=first_client)
        first_ctx.__aexit__ = AsyncMock(return_value=False)
        second_ctx = MagicMock()
        second_ctx.is_connected = MagicMock(return_value=True)
        second_ctx.__aenter__ = AsyncMock(return_value=second_client)
        second_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "butlers.tools.switchboard.routing.route.MCPClient",
            MagicMock(side_effect=[first_ctx, second_ctx]),
        ) as mock_ctor:
            first = await _call_butler_tool("http://localhost:8101/sse", "handle_message", {})
            first_ctx.is_connected.return_value = False
            second = await _call_butler_tool("http://localhost:8101/sse", "handle_message", {})

        assert first == {"step": 1}
        assert second == {"step": 2}
        assert mock_ctor.call_count == 2

    async def test_wraps_client_failure_as_connection_error(self) -> None:
        """_call_butler_tool should preserve failed-call context in the exception."""
        from butlers.tools.switchboard import _call_butler_tool

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=RuntimeError("connection refused"))

        mock_ctor = MagicMock()
        mock_ctx = mock_ctor.return_value
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("butlers.tools.switchboard.routing.route.MCPClient", mock_ctor):
            with pytest.raises(ConnectionError, match="Failed to call tool handle_message"):
                await _call_butler_tool(
                    "http://localhost:8101/sse",
                    "handle_message",
                    {"prompt": "Hello"},
                )

    async def test_handle_message_falls_back_to_trigger(self) -> None:
        """Unknown handle_message should retry using trigger with mapped args."""
        from butlers.tools.switchboard import _call_butler_tool

        first_result = SimpleNamespace(
            is_error=True,
            data=None,
            content=[SimpleNamespace(text="Unknown tool: handle_message")],
        )
        second_result = SimpleNamespace(is_error=False, data={"output": "ok"}, content=[])

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=[first_result, second_result])

        mock_ctor = MagicMock()
        mock_ctx = mock_ctor.return_value
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("butlers.tools.switchboard.routing.route.MCPClient", mock_ctor):
            result = await _call_butler_tool(
                "http://localhost:8101/sse",
                "handle_message",
                {"message": "hello from telegram", "source": "telegram"},
            )

        assert result == {"output": "ok"}
        assert mock_client.call_tool.await_count == 2
        assert mock_client.call_tool.await_args_list[0].args == (
            "handle_message",
            {"message": "hello from telegram", "source": "telegram"},
        )
        fallback_tool, fallback_args = mock_client.call_tool.await_args_list[1].args
        assert fallback_tool == "trigger"
        assert fallback_args["prompt"] == "hello from telegram"
        assert "Source metadata" in fallback_args["context"]
        assert "telegram" in fallback_args["context"]
        assert mock_client.call_tool.await_args_list[0].kwargs == {"raise_on_error": False}
        assert mock_client.call_tool.await_args_list[1].kwargs == {"raise_on_error": False}

    async def test_prefixed_tool_falls_back_to_trigger(self) -> None:
        """Unknown identity-prefixed tools should retry via trigger."""
        from butlers.tools.switchboard import _call_butler_tool

        first_result = SimpleNamespace(
            is_error=True,
            data=None,
            content=[SimpleNamespace(text="Unknown tool: bot_telegram_handle_message")],
        )
        second_result = SimpleNamespace(is_error=False, data={"output": "ok"}, content=[])

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(side_effect=[first_result, second_result])

        mock_ctor = MagicMock()
        mock_ctx = mock_ctor.return_value
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("butlers.tools.switchboard.routing.route.MCPClient", mock_ctor):
            result = await _call_butler_tool(
                "http://localhost:8101/sse",
                "bot_telegram_handle_message",
                {
                    "prompt": "hello from telegram",
                    "source_channel": "telegram",
                    "source_identity": "bot",
                    "source_tool": "bot_telegram_get_updates",
                    "source_id": "msg-12",
                },
            )

        assert result == {"output": "ok"}
        first_tool, first_args = mock_client.call_tool.await_args_list[0].args
        assert first_tool == "bot_telegram_handle_message"
        assert first_args["prompt"] == "hello from telegram"

        fallback_tool, fallback_args = mock_client.call_tool.await_args_list[1].args
        assert fallback_tool == "trigger"
        assert fallback_args["prompt"] == "hello from telegram"
        assert "Source metadata" in fallback_args["context"]
        assert "bot_telegram_get_updates" in fallback_args["context"]
        assert "msg-12" in fallback_args["context"]
