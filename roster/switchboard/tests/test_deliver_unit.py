"""Unit tests for deliver() and notification helpers — no Docker/Postgres required.

These tests mock the asyncpg pool and focus on deliver()'s dispatch logic,
error handling, and notification logging behavior. They complement the
integration tests in test_tools.py which use a real Postgres container.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
        side_effect = list(fetchrow_side_effect)
        registry_lookup = side_effect.pop(0) if side_effect else None
        if isinstance(registry_lookup, list):
            pool.fetch = AsyncMock(return_value=registry_lookup)
        elif registry_lookup is None:
            pool.fetch = AsyncMock(return_value=[])
        else:
            pool.fetch = AsyncMock(return_value=[registry_lookup])
        pool.fetchrow = AsyncMock(side_effect=side_effect)
    elif registry_rows is not None:
        row_mocks = []
        for row in registry_rows:
            m = MagicMock()
            # Fix lambda capture: explicitly capture row value in default argument
            m.__getitem__ = lambda self, key, r=row: r[key]
            m.get = lambda key, default=None, r=row: r.get(key, default)
            # Support dict() conversion for asyncpg.Record compatibility
            m.__iter__ = lambda self, r=row: iter(r)
            m.keys = lambda self, r=row: r.keys()
            m.values = lambda self, r=row: r.values()
            m.items = lambda self, r=row: r.items()
            row_mocks.append(m)

        pool.fetch = AsyncMock(return_value=row_mocks)
        # fetchrow is called for route target lookup + notification insert.
        pool.fetchrow = AsyncMock(side_effect=row_mocks)
    else:
        pool.fetch = AsyncMock(return_value=[])
        pool.fetchrow = AsyncMock(return_value=None)

    pool.execute = AsyncMock()
    return pool


def _notif_id_row() -> dict[str, Any]:
    """Return a dict row for ``INSERT INTO notifications ... RETURNING id``."""
    return {"id": str(uuid.uuid4())}


def _registry_row(name: str, endpoint: str = "http://localhost:9100/sse") -> dict[str, Any]:
    """Return a dict row for butler_registry lookups."""
    # Use a fixed timestamp to avoid TTL expiry during test execution
    fixed_now = datetime.now(UTC)
    return {
        "name": name,
        "endpoint_url": endpoint,
        "description": None,
        "modules": [],
        "last_seen_at": fixed_now,
        "registered_at": fixed_now,
        "eligibility_state": "active",
        "liveness_ttl_seconds": 300,
        "quarantined_at": None,
        "quarantine_reason": None,
        "route_contract_min": 1,
        "route_contract_max": 1,
        "capabilities": ["trigger", "telegram", "email"],
        "eligibility_updated_at": fixed_now,
        "id": uuid.uuid4(),  # Add id field for completeness
    }


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
                _registry_row("chatter", "http://localhost:40103/sse"),
                # 2. butler_registry endpoint lookup (route)
                _registry_row("chatter", "http://localhost:40103/sse"),
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
            call_fn=mock_call,
        )

        assert result["status"] == "sent"
        assert "notification_id" in result

    async def test_telegram_routes_to_correct_tool(self) -> None:
        """deliver() should call the prefixed bot telegram send tool."""
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
        assert captured[0]["tool"] == "bot_telegram_send_message"
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
            call_fn=mock_call,
        )

        assert result["status"] == "sent"
        assert "notification_id" in result

    async def test_email_routes_to_send_email_tool(self) -> None:
        """deliver() should call the prefixed bot email send tool."""
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
        assert captured[0]["tool"] == "bot_email_send_message"
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


class TestDeliverNotifyRouting:
    """notify.v1 requests should terminate at Switchboard and route to messenger."""

    async def test_notify_request_routes_to_messenger_with_metadata(self) -> None:
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                [],  # Unused fetch return (notify path skips module lookup)
                _registry_row("messenger", "http://localhost:9200/sse"),
                _notif_id_row(),
            ],
        )
        captured: list[dict[str, Any]] = []

        async def mock_call(endpoint_url, tool_name, args):
            captured.append({"endpoint_url": endpoint_url, "tool_name": tool_name, "args": args})
            return {"status": "ok", "result": {"notify_response": {"status": "ok"}}}

        notify_request = {
            "schema_version": "notify.v1",
            "origin_butler": "health",
            "delivery": {
                "intent": "reply",
                "channel": "telegram",
                "message": "Got it.",
                "recipient": "chat-123",
            },
            "request_context": {
                "request_id": "018f52f3-9d8a-7ef2-8f2d-9fb6b32f12aa",
                "received_at": "2026-02-13T12:00:00+00:00",
                "source_channel": "telegram",
                "source_endpoint_identity": "switchboard-bot",
                "source_sender_identity": "user-123",
                "source_thread_identity": "chat-123",
            },
        }
        result = await deliver(
            pool,
            source_butler="health",
            notify_request=notify_request,
            call_fn=mock_call,
        )

        assert result["status"] == "sent"
        assert len(captured) == 1
        assert captured[0]["tool_name"] == "route.execute"
        routed_payload = captured[0]["args"]
        assert routed_payload["target"]["butler"] == "messenger"
        assert routed_payload["input"]["context"]["notify_request"]["origin_butler"] == "health"
        assert (
            routed_payload["input"]["context"]["notify_request"]["request_context"]["request_id"]
            == "018f52f3-9d8a-7ef2-8f2d-9fb6b32f12aa"
        )
        # Route envelope uses switchboard-scoped context for messenger authz
        assert routed_payload["request_context"]["source_endpoint_identity"] == "switchboard"
        assert routed_payload["request_context"]["source_sender_identity"] == "health"
        assert routed_payload["request_context"]["source_channel"] == "mcp"
        # Original context is preserved inside notify_request for reply targeting
        inner_ctx = routed_payload["input"]["context"]["notify_request"]["request_context"]
        assert inner_ctx["source_endpoint_identity"] == "switchboard-bot"
        assert inner_ctx["source_sender_identity"] == "user-123"
        assert inner_ctx["source_thread_identity"] == "chat-123"

    async def test_notify_route_context_preserves_request_id_lineage(self) -> None:
        """Route envelope request_id matches the original notify request_context request_id."""
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                [],
                _registry_row("messenger", "http://localhost:9200/sse"),
                _notif_id_row(),
            ],
        )
        captured: list[dict[str, Any]] = []

        async def mock_call(endpoint_url, tool_name, args):
            captured.append({"endpoint_url": endpoint_url, "tool_name": tool_name, "args": args})
            return {"status": "ok", "result": {"notify_response": {"status": "ok"}}}

        original_request_id = "018f52f3-9d8a-7ef2-8f2d-9fb6b32f12aa"
        notify_request = {
            "schema_version": "notify.v1",
            "origin_butler": "health",
            "delivery": {
                "intent": "reply",
                "channel": "telegram",
                "message": "Got it.",
                "recipient": "chat-123",
            },
            "request_context": {
                "request_id": original_request_id,
                "received_at": "2026-02-13T12:00:00+00:00",
                "source_channel": "telegram",
                "source_endpoint_identity": "telegram:BigButlerBot",
                "source_sender_identity": "user-123",
                "source_thread_identity": "chat-123",
            },
        }
        result = await deliver(
            pool,
            source_butler="health",
            notify_request=notify_request,
            call_fn=mock_call,
        )

        assert result["status"] == "sent"
        routed_payload = captured[0]["args"]
        # Route-level request_id preserves lineage from the original request
        assert routed_payload["request_context"]["request_id"] == original_request_id
        # Switchboard-scoped identity for messenger authz
        assert routed_payload["request_context"]["source_endpoint_identity"] == "switchboard"
        # Inner notify_request preserves original context for reply targeting
        inner_ctx = routed_payload["input"]["context"]["notify_request"]["request_context"]
        assert inner_ctx["request_id"] == original_request_id
        assert inner_ctx["source_endpoint_identity"] == "telegram:BigButlerBot"

    async def test_invalid_notify_request_fails_validation(self) -> None:
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool()
        result = await deliver(
            pool,
            source_butler="health",
            notify_request={
                "schema_version": "notify.v1",
                "origin_butler": "health",
                "delivery": {"intent": "send", "channel": "telegram"},
            },
        )

        assert result["status"] == "failed"
        assert "Invalid notify.v1 envelope" in result["error"]

    async def test_legacy_specialist_delivery_is_normalized_to_notify_v1(self) -> None:
        from butlers.tools.switchboard import deliver

        pool = _make_mock_pool(
            fetchrow_side_effect=[
                [],  # Unused fetch return (notify path skips module lookup)
                _registry_row("messenger", "http://localhost:9200/sse"),
                _notif_id_row(),
            ],
        )
        captured: list[dict[str, Any]] = []

        async def mock_call(endpoint_url, tool_name, args):
            captured.append({"endpoint_url": endpoint_url, "tool_name": tool_name, "args": args})
            return {"status": "ok"}

        result = await deliver(
            pool,
            channel="email",
            message="Daily summary",
            recipient="user@example.com",
            metadata={"subject": "Summary"},
            source_butler="health",
            call_fn=mock_call,
        )

        assert result["status"] == "sent"
        assert len(captured) == 1
        notify_payload = captured[0]["args"]["input"]["context"]["notify_request"]
        assert notify_payload["origin_butler"] == "health"
        assert notify_payload["delivery"]["channel"] == "email"
        assert notify_payload["delivery"]["subject"] == "Summary"
        assert notify_payload["delivery"]["recipient"] == "user@example.com"


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
        """_call_butler_tool should call FastMCP and return result.data.

        Identity-prefixed tools are now routed directly to trigger upfront.
        """
        from butlers.tools.switchboard import _call_butler_tool

        mock_result = SimpleNamespace(is_error=False, data={"ok": True}, content=[])
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)

        mock_ctor = MagicMock()
        mock_ctx = mock_ctor.return_value
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("butlers.tools.switchboard.routing.route.MCPClient", mock_ctor):
            result = await _call_butler_tool(
                "http://localhost:40101/sse",
                "bot_switchboard_handle_message",
                {},
            )

        assert result == {"ok": True}
        mock_ctor.assert_called_once_with("http://localhost:40101/sse", name="switchboard-router")
        mock_client.call_tool.assert_awaited_once_with(
            "bot_switchboard_handle_message",
            {},
            raise_on_error=False,
        )

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
            first = await _call_butler_tool(
                "http://localhost:40101/sse",
                "bot_switchboard_handle_message",
                {},
            )
            second = await _call_butler_tool(
                "http://localhost:40101/sse",
                "bot_switchboard_handle_message",
                {},
            )

        assert first == {"n": 1}
        assert second == {"n": 2}
        mock_ctor.assert_called_once_with("http://localhost:40101/sse", name="switchboard-router")
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
            first = await _call_butler_tool(
                "http://localhost:40101/sse",
                "bot_switchboard_handle_message",
                {},
            )
            first_ctx.is_connected.return_value = False
            second = await _call_butler_tool(
                "http://localhost:40101/sse",
                "bot_switchboard_handle_message",
                {},
            )

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
            with pytest.raises(
                ConnectionError,
                match="Failed to call tool bot_switchboard_handle_message",
            ):
                await _call_butler_tool(
                    "http://localhost:40101/sse",
                    "bot_switchboard_handle_message",
                    {"prompt": "Hello"},
                )

    async def test_unprefixed_unknown_tool_does_not_fallback_to_trigger(self) -> None:
        """Legacy unprefixed tool names should fail without trigger fallback."""
        from butlers.tools.switchboard import _call_butler_tool

        legacy_tool = "handle" + "_message"
        failed_result = SimpleNamespace(
            is_error=True,
            data=None,
            content=[SimpleNamespace(text=f"Unknown tool: {legacy_tool}")],
        )

        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=failed_result)

        mock_ctor = MagicMock()
        mock_ctx = mock_ctor.return_value
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("butlers.tools.switchboard.routing.route.MCPClient", mock_ctor):
            with pytest.raises(RuntimeError, match=rf"Unknown tool: {legacy_tool}"):
                await _call_butler_tool(
                    "http://localhost:40101/sse",
                    legacy_tool,
                    {"message": "legacy"},
                )

        mock_client.call_tool.assert_awaited_once_with(
            legacy_tool,
            {"message": "legacy"},
            raise_on_error=False,
        )


# ---------------------------------------------------------------------------
# _write_outbound_message_inbox
# ---------------------------------------------------------------------------


def _make_uuid7() -> str:
    """Generate a valid UUID v7 string for tests."""
    import secrets
    from datetime import UTC, datetime

    timestamp_ms = int(datetime.now(UTC).timestamp() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    import uuid as _uuid_mod

    return str(_uuid_mod.UUID(int=value))


class TestWriteOutboundMessageInbox:
    """Unit tests for _write_outbound_message_inbox helper."""

    async def test_skips_when_no_request_context(self) -> None:
        """Skips write when notify_request has no request_context."""
        from butlers.tools.switchboard import _write_outbound_message_inbox
        from butlers.tools.switchboard.routing.contracts import (
            NotifyRequestV1,
        )

        pool = AsyncMock()
        notify_request = NotifyRequestV1.model_validate(
            {
                "schema_version": "notify.v1",
                "origin_butler": "relationship",
                "delivery": {
                    "intent": "send",
                    "channel": "telegram",
                    "message": "Got it!",
                    "recipient": "user123",
                },
                # No request_context
            }
        )

        await _write_outbound_message_inbox(
            pool,
            notify_request=notify_request,
            delivered_at=datetime.now(UTC),
        )

        pool.execute.assert_not_awaited()

    async def test_skips_when_no_thread_identity(self) -> None:
        """Skips write when request_context has no source_thread_identity."""
        from butlers.tools.switchboard import _write_outbound_message_inbox
        from butlers.tools.switchboard.routing.contracts import NotifyRequestV1

        pool = AsyncMock()
        notify_request = NotifyRequestV1.model_validate(
            {
                "schema_version": "notify.v1",
                "origin_butler": "health",
                "delivery": {
                    "intent": "send",
                    "channel": "telegram",
                    "message": "Medication recorded.",
                    "recipient": "user456",
                },
                "request_context": {
                    "request_id": _make_uuid7(),
                    "source_channel": "telegram",
                    "source_endpoint_identity": "telegram:bot",
                    "source_sender_identity": "user456",
                    # No source_thread_identity
                },
            }
        )

        await _write_outbound_message_inbox(
            pool,
            notify_request=notify_request,
            delivered_at=datetime.now(UTC),
        )

        pool.execute.assert_not_awaited()

    async def test_writes_outbound_row_when_thread_identity_present(self) -> None:
        """Writes outbound row to message_inbox when source_thread_identity is available."""
        from butlers.tools.switchboard import _write_outbound_message_inbox
        from butlers.tools.switchboard.routing.contracts import NotifyRequestV1

        pool = AsyncMock()
        delivered_at = datetime(2026, 2, 18, 10, 5, 0, tzinfo=UTC)

        notify_request = NotifyRequestV1.model_validate(
            {
                "schema_version": "notify.v1",
                "origin_butler": "relationship",
                "delivery": {
                    "intent": "reply",
                    "channel": "telegram",
                    "message": "Got it! I've stored Dua um's address as 71 nim road 804975.",
                },
                "request_context": {
                    "request_id": _make_uuid7(),
                    "source_channel": "telegram",
                    "source_endpoint_identity": "telegram:bot",
                    "source_sender_identity": "user123",
                    "source_thread_identity": "12345678:999",
                },
            }
        )

        await _write_outbound_message_inbox(
            pool,
            notify_request=notify_request,
            delivered_at=delivered_at,
        )

        pool.execute.assert_awaited_once()
        call_args = pool.execute.call_args
        sql = call_args[0][0]

        assert "INSERT INTO message_inbox" in sql
        assert "direction" in sql
        assert "'outbound'" in sql

        # Verify the positional arguments
        pos_args = call_args[0]
        assert pos_args[1] == delivered_at  # received_at
        # request_context JSON should include thread identity and origin_butler
        import json as _json

        req_ctx = _json.loads(pos_args[2])
        assert req_ctx["source_thread_identity"] == "12345678:999"
        assert req_ctx["source_sender_identity"] == "relationship"

        raw_payload = _json.loads(pos_args[3])
        expected_text = "Got it! I've stored Dua um's address as 71 nim road 804975."
        assert raw_payload["content"] == expected_text
        assert raw_payload["metadata"]["origin_butler"] == "relationship"

        # Normalized text
        assert pos_args[4] == "Got it! I've stored Dua um's address as 71 nim road 804975."

    async def test_swallows_db_error_without_propagating(self) -> None:
        """DB error during outbound write is logged but never propagates."""
        from butlers.tools.switchboard import _write_outbound_message_inbox
        from butlers.tools.switchboard.routing.contracts import NotifyRequestV1

        pool = AsyncMock()
        pool.execute.side_effect = Exception("DB connection lost")

        notify_request = NotifyRequestV1.model_validate(
            {
                "schema_version": "notify.v1",
                "origin_butler": "general",
                "delivery": {
                    "intent": "reply",
                    "channel": "telegram",
                    "message": "Sure, let me look into that.",
                },
                "request_context": {
                    "request_id": _make_uuid7(),
                    "source_channel": "telegram",
                    "source_endpoint_identity": "telegram:bot",
                    "source_sender_identity": "user789",
                    "source_thread_identity": "99999:1234",
                },
            }
        )

        # Should not raise even though pool.execute raises
        await _write_outbound_message_inbox(
            pool,
            notify_request=notify_request,
            delivered_at=datetime.now(UTC),
        )
