"""Condensed Telegram module tests — behavioral contract only.

Replaces 54 tests with ~15 focused behavioral tests.

Covers:
- Module ABC compliance
- TelegramConfig validation (required fields, defaults)
- Tool registration (expected tools)
- Markdown to HTML conversion (critical formatting logic)
- Registry integration

[bu-7sd7a]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from butlers.modules.base import Module
from butlers.modules.telegram import TelegramConfig, TelegramModule, _markdown_to_telegram_html
from butlers.testing.mcp import registered_tools

# The retained historical permission-test narratives describe the original enforcement.
# Effects now refuse unavailable state; the real-lookup availability test below covers
# bu-30sah4's effect stage, before bu-svqpzo.3 changes remaining shared callers.
pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _allow_effects_for_provider_tests(request, monkeypatch):
    """Provider-only unit tests assume an available grant; gate tests use real reads."""
    if request.cls is not TestTelegramSendPermissionEnforcement:
        monkeypatch.setattr(
            "butlers.modules.telegram.require_permission", AsyncMock(return_value=None)
        )


EXPECTED_TELEGRAM_TOOLS = {
    "telegram_send_message",
    "telegram_reply_to_message",
    "telegram_react_to_message",
    "telegram_edit_message_text",
}


@pytest.mark.parametrize(
    ("button", "inert", "server_built", "allowed"),
    [
        ({"url": "https://example.test"}, False, False, True),
        ({"url": "https://example.test"}, True, False, False),
        ({"url": "https://example.test"}, True, True, True),
        ({"login_url": {"url": "https://example.test"}}, False, True, False),
        ({"web_app": {"url": "https://example.test"}}, False, True, False),
        ({"callback_data": "review:1"}, True, False, True),
    ],
)
def test_reply_markup_refuses_identity_handoffs_and_inert_caller_links(
    button, inert, server_built, allowed
):
    from butlers.telegram_api import (
        TelegramReplyMarkupValidationError,
        validate_telegram_reply_markup,
    )

    markup = {"inline_keyboard": [[{"text": "Open", **button}]]}
    if allowed:
        assert (
            validate_telegram_reply_markup(
                markup, render_links_inert=inert, server_built=server_built
            )
            is markup
        )
    else:
        with pytest.raises(TelegramReplyMarkupValidationError):
            validate_telegram_reply_markup(
                markup, render_links_inert=inert, server_built=server_built
            )


async def test_inert_delivery_uses_explicit_pre_entities_and_disables_previews():
    module = TelegramModule()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"ok": True, "result": {"message_id": 42}}
    client = AsyncMock()
    client.post.return_value = response
    text = "😀 [click](https://example.test) <a href='https://evil.test'>link</a>"
    with (
        patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
        patch.object(module, "_get_client", return_value=client),
        patch.object(module, "_base_url", return_value="https://telegram.test"),
    ):
        await module._reply_to_message("123", 7, text, render_links_inert=True)
    payload = client.post.await_args.kwargs["json"]
    assert payload["text"] == text
    assert "parse_mode" not in payload
    assert payload["entities"] == [
        {"type": "pre", "offset": 0, "length": len(text.encode("utf-16-le")) // 2}
    ]
    assert payload["link_preview_options"] == {"is_disabled": True}
    assert payload["reply_to_message_id"] == 7


@pytest.fixture
def telegram_module() -> TelegramModule:
    return TelegramModule()


@pytest.fixture
def mock_mcp() -> FastMCP:
    return FastMCP("providers8-telegram-tools")


class TestModuleABCCompliance:
    def test_module_contract(self, telegram_module: TelegramModule) -> None:
        """TelegramModule satisfies Module ABC: name, config_schema, dependencies, registry."""
        from butlers.modules.registry import default_registry

        assert issubclass(TelegramModule, Module)
        assert telegram_module.name == "telegram"
        assert telegram_module.config_schema is TelegramConfig
        assert telegram_module.dependencies == []
        assert "telegram" in default_registry().available_modules


class TestTelegramConfig:
    def test_defaults_and_validation(self) -> None:
        cfg = TelegramConfig()
        assert cfg.webhook_url is None
        assert cfg.bot is not None
        # Extra fields rejected
        with pytest.raises(ValidationError):
            TelegramConfig(unknown="x")


class TestToolRegistration:
    async def test_registers_expected_tools(
        self, telegram_module: TelegramModule, mock_mcp: MagicMock
    ) -> None:
        await telegram_module.register_tools(
            mcp=mock_mcp,
            config={},
            db=None,
            butler_name="test-butler",
        )
        assert set((await registered_tools(mock_mcp)).keys()) == EXPECTED_TELEGRAM_TOOLS


class TestInlineKeyboardSupport:
    async def test_send_passes_reply_markup_to_telegram(self) -> None:
        mod = TelegramModule()
        markup = {
            "inline_keyboard": [
                [
                    {"text": "Approve", "callback_data": "apr1:action:a:0123456789abcdef"},
                    {"text": "Open", "url": "https://example.test/approvals/action"},
                ]
            ]
        }
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True, "result": {"message_id": 42}}

        with (
            patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            client = AsyncMock()
            client.post = AsyncMock(return_value=response)
            mock_get_client.return_value = client

            result = await mod._send_message("123456", "Review this action", reply_markup=markup)

        assert result == {"ok": True, "result": {"message_id": 42}}
        payload = client.post.await_args.kwargs["json"]
        assert payload["reply_markup"] is markup
        assert payload["reply_markup"]["inline_keyboard"][0][0]["text"] == "Approve"

    async def test_reply_passes_reply_markup_to_telegram(self) -> None:
        mod = TelegramModule()
        markup = {
            "inline_keyboard": [
                [{"text": "Reject", "callback_data": "apr1:action:r:0123456789abcdef"}]
            ]
        }
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True, "result": {"message_id": 43}}

        with (
            patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            client = AsyncMock()
            client.post = AsyncMock(return_value=response)
            mock_get_client.return_value = client

            await mod._reply_to_message("123456", 99, "Review this action", reply_markup=markup)

        payload = client.post.await_args.kwargs["json"]
        assert payload["reply_to_message_id"] == 99
        assert payload["reply_markup"] is markup

    async def test_tool_rejects_oversized_callback_data_before_api_call(
        self, telegram_module: TelegramModule, mock_mcp: MagicMock
    ) -> None:
        await telegram_module.register_tools(
            mcp=mock_mcp,
            config={},
            db=None,
            butler_name="test-butler",
        )
        markup = {"inline_keyboard": [[{"text": "Too long", "callback_data": "é" * 33}]]}

        with patch.object(telegram_module, "_get_client") as mock_get_client:
            result = await (await registered_tools(mock_mcp))["telegram_send_message"](
                "123456", "Review this action", reply_markup=markup
            )

        assert result["error_code"] == "validation_error"
        assert result["field"] == "reply_markup.inline_keyboard[0][0].callback_data"
        assert result["max_bytes"] == 64
        assert result["actual_bytes"] == 66
        mock_get_client.assert_not_called()

    async def test_edits_message_then_removes_its_inline_keyboard(self) -> None:
        mod = TelegramModule()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True}

        with (
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            client = AsyncMock()
            client.post = AsyncMock(return_value=response)
            mock_get_client.return_value = client

            await mod._edit_message_text("123456", 99, "**Approved**")
            await mod._remove_inline_keyboard("123456", 99)

        text_call, markup_call = client.post.await_args_list
        assert text_call.args[0].endswith("/editMessageText")
        assert text_call.kwargs["json"] == {
            "chat_id": "123456",
            "message_id": 99,
            "text": "<b>Approved</b>",
            "parse_mode": "HTML",
        }
        assert markup_call.args[0].endswith("/editMessageReplyMarkup")
        assert markup_call.kwargs["json"] == {
            "chat_id": "123456",
            "message_id": 99,
            "reply_markup": None,
        }


class TestEditMessageTextTool:
    """bu-q7vx1q.5: amending a delivered insight edits the stored message in place."""

    @staticmethod
    def _response(status: int, body: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            status, json=body, request=httpx.Request("POST", "https://api.telegram.org/bot/x")
        )

    async def _edit(self, response: httpx.Response) -> tuple[dict[str, Any], AsyncMock]:
        mod = TelegramModule()
        with (
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            client = AsyncMock()
            client.post = AsyncMock(return_value=response)
            mock_get_client.return_value = client
            result = await mod._edit_message_text_or_reject("123456", 77, "~~Due~~\nResolved")
        return result, client.post

    async def test_edits_the_stored_message_id(self) -> None:
        result, post = await self._edit(self._response(200, {"ok": True, "result": {}}))

        assert result["edited"] is True
        assert post.await_args.args[0].endswith("/editMessageText")
        assert post.await_args.kwargs["json"] == {
            "chat_id": "123456",
            "message_id": 77,
            "text": "<s>Due</s>\nResolved",
            "parse_mode": "HTML",
        }

    async def test_a_400_is_a_structured_rejection_so_the_caller_can_fall_back(self) -> None:
        result, _ = await self._edit(
            self._response(
                400, {"ok": False, "description": "Bad Request: message can't be edited"}
            )
        )

        assert result == {
            "edited": False,
            "error_code": 400,
            "description": "Bad Request: message can't be edited",
        }

    async def test_a_non_400_failure_still_raises_so_the_caller_can_retry(self) -> None:
        with pytest.raises(httpx.HTTPStatusError):
            await self._edit(self._response(502, {"ok": False}))


class TestTelegramSendAuditEmit:
    """telegram_send is emitted to dashboard_audit_log on outbound sendMessage."""

    async def test_audit_emitted_on_success(self) -> None:
        mod = TelegramModule()
        mock_pool = MagicMock()
        mod._butler_name = "test-butler"
        mod.wire_audit_pool(mock_pool)

        # Fake successful HTTP response
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"ok": True, "result": {"message_id": 44}}

        with (
            patch(
                "butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock
            ) as mock_emit,
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=fake_resp)
            mock_get_client.return_value = mock_client

            await mod._send_message("123456", "Hello world")

        mock_emit.assert_awaited_once()
        call_args = mock_emit.call_args
        assert call_args.args[2] == "telegram_send"
        assert call_args.args[3]["chat_id"] == "123456"
        assert "text_length" in call_args.args[3]
        assert call_args.args[3]["provider_message_id"] == 44

    @pytest.mark.parametrize(
        "provider_body",
        [
            {"ok": False, "description": "rejected"},
            {"ok": True},
            {"ok": True, "result": {}},
            {"ok": True, "result": {"message_id": None}},
            {"ok": True, "result": {"message_id": "42"}},
            {"ok": True, "result": {"message_id": True}},
            {"ok": True, "result": {"message_id": 0}},
            {"ok": True, "result": {"message_id": -1}},
        ],
    )
    async def test_http_200_without_valid_telegram_receipt_is_failure(
        self, provider_body: dict[str, Any]
    ) -> None:
        mod = TelegramModule()
        mod._butler_name = "test-butler"
        mod.wire_audit_pool(MagicMock())
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = provider_body

        with (
            patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock) as audit,
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            client = AsyncMock()
            client.post = AsyncMock(return_value=response)
            mock_get_client.return_value = client

            with pytest.raises(RuntimeError, match="telegram_provider_response"):
                await mod._send_message("123456", "Hello")

        audit.assert_awaited_once()
        assert audit.await_args.kwargs["result"] == "error"
        assert audit.await_args.kwargs["error"] == "telegram_provider_response_invalid"

    async def test_non_json_http_200_is_failure_and_audited(self) -> None:
        mod = TelegramModule()
        mod._butler_name = "test-butler"
        mod.wire_audit_pool(MagicMock())
        response = MagicMock()
        response.status_code = 200
        response.json.side_effect = ValueError("not json")

        with (
            patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock) as audit,
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            client = AsyncMock()
            client.post = AsyncMock(return_value=response)
            mock_get_client.return_value = client

            with pytest.raises(RuntimeError, match="telegram_provider_response"):
                await mod._send_message("123456", "Hello")

        audit.assert_awaited_once()
        assert audit.await_args.kwargs["result"] == "error"
        assert audit.await_args.kwargs["error"] == "telegram_provider_response_invalid"

    async def test_transport_timeout_is_failure_and_audited(self) -> None:
        mod = TelegramModule()
        mod._butler_name = "test-butler"
        mod.wire_audit_pool(MagicMock())
        timeout = httpx.ReadTimeout(
            "timeout",
            request=httpx.Request("POST", "https://api.telegram.org/bot<token>/sendMessage"),
        )

        with (
            patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock) as audit,
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            client = AsyncMock()
            client.post = AsyncMock(side_effect=timeout)
            mock_get_client.return_value = client

            with pytest.raises(httpx.ReadTimeout):
                await mod._send_message("123456", "Hello")

        audit.assert_awaited_once()
        assert audit.await_args.kwargs["result"] == "error"
        assert audit.await_args.kwargs["error"] == "telegram_transport_timeout"

    async def test_audit_emitted_on_error(self) -> None:
        mod = TelegramModule()
        mock_pool = MagicMock()
        mod._butler_name = "test-butler"
        mod.wire_audit_pool(mock_pool)

        # Fake 400 HTTP response
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 400
        fake_resp.text = "Bad Request"
        fake_resp.json.return_value = {"description": "chat not found"}
        fake_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400", request=MagicMock(), response=fake_resp
        )

        with (
            patch(
                "butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock
            ) as mock_emit,
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=fake_resp)
            mock_get_client.return_value = mock_client

            with pytest.raises(httpx.HTTPStatusError):
                await mod._send_message("bad-id", "Hi")

        mock_emit.assert_awaited_once()
        call_args = mock_emit.call_args
        assert call_args.args[2] == "telegram_send"
        assert call_args.kwargs["result"] == "error"

    def test_wire_audit_pool_stores_pool(self) -> None:
        mod = TelegramModule()
        pool = MagicMock()
        mod.wire_audit_pool(pool)
        assert mod._audit_pool is pool


class TestTelegramSendPermissionEnforcement:
    """The ``notify`` permission gates _send_message (the raw send/reply chokepoint).

    Telegram is an owner-facing notify channel, so a butler with ``notify``
    revoked must not be able to message the owner via the raw
    ``telegram_send_message`` / ``telegram_reply_to_message`` tools either.
    ``_send_message`` is the chokepoint both funnel through; gating it covers
    both. The gate reuses ``notify`` (no new permission key / migration) and
    consults public.permissions via butlers.modules.telegram.require_permission,
    which fails open on DB error.

    [bu-y0wcr]
    """

    async def test_send_blocked_when_notify_revoked(self) -> None:
        """Revoked notify blocks the send before any Telegram HTTP traffic.

        Pre-fix this fails: the raw send path ignored the matrix entirely.
        """
        from butlers.core.permissions import PermissionDenied

        mod = TelegramModule()
        mod._butler_name = "test-butler"
        mod.wire_audit_pool(MagicMock())

        with (
            patch(
                "butlers.modules.telegram.require_permission",
                new_callable=AsyncMock,
                side_effect=PermissionDenied("test-butler", "notify", "revoked by owner"),
            ),
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock()
            mock_get_client.return_value = mock_client

            with pytest.raises(PermissionDenied):
                await mod._send_message("123456", "Hello world")

        mock_client.post.assert_not_called()

    async def test_reply_blocked_when_notify_revoked(self) -> None:
        """Reply funnels through _send_message, so it is gated too."""
        from butlers.core.permissions import PermissionDenied

        mod = TelegramModule()
        mod._butler_name = "test-butler"
        mod.wire_audit_pool(MagicMock())

        with (
            patch(
                "butlers.modules.telegram.require_permission",
                new_callable=AsyncMock,
                side_effect=PermissionDenied("test-butler", "notify", "revoked by owner"),
            ),
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock()
            mock_get_client.return_value = mock_client

            with pytest.raises(PermissionDenied):
                await mod._reply_to_message("123456", 42, "Hi")

        mock_client.post.assert_not_called()

    async def test_send_allowed_when_notify_granted(self) -> None:
        """Granted (require_permission returns None) lets the send proceed."""
        mod = TelegramModule()
        mod._butler_name = "test-butler"
        mod.wire_audit_pool(MagicMock())

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"ok": True, "result": {"message_id": 45}}

        with (
            patch(
                "butlers.modules.telegram.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ) as mock_require,
            patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
            patch.object(mod, "_get_client") as mock_get_client,
            patch.object(mod, "_base_url", return_value="https://api.telegram.org/bot<token>"),
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=fake_resp)
            mock_get_client.return_value = mock_client

            result = await mod._send_message("123456", "Hello world")

        assert result == {"ok": True, "result": {"message_id": 45}}
        mock_client.post.assert_awaited_once()
        # Gate consulted the matrix with the notify capability.
        assert mock_require.await_args.args[2] == "notify"

    async def test_real_permission_failures_block_all_mutations(self, caplog) -> None:
        from butlers.core.permissions import PermissionUnavailable

        mod = TelegramModule()
        mod._butler_name = "test-butler"
        pool = AsyncMock()
        client = AsyncMock()
        response = MagicMock()
        response.json.return_value = {"ok": True, "result": {"message_id": 45}}
        client.post.return_value = response
        with (
            patch.object(mod, "_get_client", return_value=client),
            patch.object(mod, "_base_url", return_value="https://example.com/private-token"),
            patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock) as audit,
        ):
            for failure in ("missing", "query", "malformed"):
                mod._audit_pool = None if failure == "missing" else pool
                pool.fetchrow.side_effect = (
                    OSError("private-database-error") if failure == "query" else None
                )
                pool.fetchrow.return_value = {"granted": "false", "reason": "private-row"}
                calls = (
                    mod._send_message("private-recipient", "private-body"),
                    mod._reply_to_message("private-recipient", 1, "private-body"),
                    mod._edit_message_text("private-recipient", 1, "private-body"),
                    mod._remove_inline_keyboard("private-recipient", 1),
                )
                for call in calls:
                    with pytest.raises(PermissionUnavailable) as caught:
                        await call
                    rendered = repr(caught.value) + caplog.text + repr(audit.await_args_list)
                    for private in (
                        "private-recipient",
                        "private-body",
                        "private-token",
                        "private-row",
                        "private-database-error",
                    ):
                        assert private not in rendered
                client.post.assert_not_awaited()
                audit.assert_not_awaited()
                assert "telegram_permission_unavailable" in caplog.text
            for row in (None, {"granted": True, "reason": "granted"}):
                pool.fetchrow.return_value = row
                await mod._send_message("12345", "recovered")
            assert client.post.await_count == 2

    def test_permission_pool_prefers_audit_then_db(self) -> None:
        """_permission_pool prefers the switchboard audit pool, then the butler pool."""
        mod = TelegramModule()
        # No pools wired → None (unavailable to the effect gate).
        assert mod._permission_pool() is None

        # Butler pool only.
        db = MagicMock()
        db.pool = MagicMock()
        mod._db = db
        assert mod._permission_pool() is db.pool

        # Audit pool wins when both are present.
        audit = MagicMock()
        mod.wire_audit_pool(audit)
        assert mod._permission_pool() is audit


class TestMarkdownToTelegramHtml:
    @pytest.mark.parametrize(
        "md,expected_contains",
        [
            ("**bold text**", "<b>bold text</b>"),
            ("*italic text*", "<i>italic text</i>"),
            ("`code`", "<code>code</code>"),
            ("plain text", "plain text"),
        ],
    )
    def test_conversions(self, md: str, expected_contains: str) -> None:
        result = _markdown_to_telegram_html(md)
        assert expected_contains in result

    def test_empty_string(self) -> None:
        assert _markdown_to_telegram_html("") == ""


async def test_registered_long_reply_keeps_order_receipts_and_keyboard(mock_mcp):
    """The actual registered reply sends all source text and acknowledges the final chunk."""
    module = TelegramModule()
    await module.register_tools(mock_mcp, {}, None, "messenger")
    payloads = []

    async def post(url, *, json):
        payloads.append(json)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"ok": True, "result": {"message_id": len(payloads)}},
        )

    markup = {"inline_keyboard": [[{"text": "Review", "callback_data": "review:1"}]]}
    source = "x" * 10000
    with (
        patch.object(module, "_get_client", return_value=MagicMock(post=post)),
        patch.object(module, "_base_url", return_value="https://telegram.test"),
        patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock) as audit,
    ):
        result = await (await registered_tools(mock_mcp))["telegram_reply_to_message"](
            "123", 7, source, reply_markup=markup
        )
    assert len(payloads) == 3
    assert "".join(p["text"] for p in payloads) == source
    assert all(len(p["text"]) <= 4000 for p in payloads)
    assert payloads[0]["reply_to_message_id"] == 7
    assert all("reply_to_message_id" not in p for p in payloads[1:])
    assert payloads[-1]["reply_markup"] == markup
    assert all("reply_markup" not in p for p in payloads[:-1])
    assert result["result"]["message_id"] == 3
    assert result["result"]["message_ids"] == [1, 2, 3]
    assert audit.await_args.args[3]["provider_message_ids"] == [1, 2, 3]


def test_renderer_boundaries_are_balanced_and_source_is_never_lost():
    from html.parser import HTMLParser

    from butlers.telegram_text import render_markdown, split_telegram_text, text_units

    class Balanced(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = []
            self.visible = ""

        def handle_starttag(self, tag, attrs):
            assert tag in {"b", "i", "s", "code", "pre", "a"}
            self.stack.append(tag)
            if tag == "a":
                assert dict(attrs)["href"].startswith(("http://", "https://"))

        def handle_endtag(self, tag):
            assert self.stack.pop() == tag

        def handle_data(self, data):
            self.visible += data

    corpus = [
        "**a *b** c*",
        "<unknown>& raw",
        "## Plan\n[doc](https://example.test?a=1&b=2)",
        "[unsafe](javascript:alert)",
        "unclosed **bold",
        "*" * 100,
        "```\n" + ("print('line')\n" * 700) + "```",
        "```\n" + "z" * 4500 + "```",
        "😀" * 2100,
        "paragraph\n\n" * 700,
        "# Heading\n" + "word. " * 1000,
        "x" * 4000,
        "x" * 4001,
        "```\n" + "z" * 3996 + "```",
    ]
    for source in corpus:
        chunks = split_telegram_text(source)
        assert "".join(chunk.source for chunk in chunks) == source
        for chunk in chunks:
            parser = Balanced()
            parser.feed(chunk.html)
            assert parser.stack == []
            assert parser.visible
            assert text_units(parser.visible) <= 4000
    assert '<a href="https://example.test">doc</a>' in render_markdown(
        "[doc](https://example.test)"
    )
    assert render_markdown("[unsafe](file:///private)") == "[unsafe](file:///private)"
    assert render_markdown("# Plan") == "<b>Plan</b>"


async def test_registered_partial_reply_is_terminal_and_audits_only_accepted_prefix(mock_mcp):
    from butlers.telegram_api import TelegramPartialDeliveryError

    module = TelegramModule()
    await module.register_tools(mock_mcp, {}, None, "messenger")
    calls = []

    async def post(url, *, json):
        calls.append(json)
        return httpx.Response(
            200 if len(calls) == 1 else 500,
            request=httpx.Request("POST", url),
            json={"ok": True, "result": {"message_id": 42}},
        )

    with (
        patch.object(module, "_get_client", return_value=MagicMock(post=post)),
        patch.object(module, "_base_url", return_value="https://telegram.test"),
        patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock) as audit,
    ):
        with pytest.raises(TelegramPartialDeliveryError) as error:
            await module._send_message("123", "x" * 9000)
        assert error.value.message_ids == (42,)
        assert error.value.retryable is False
        calls.clear()
        result = await (await registered_tools(mock_mcp))["telegram_send_message"](
            "123", "y" * 9000
        )
    assert result["partial_delivery"] is True
    assert result["retryable"] is False
    assert result["message_ids"] == [42]
    assert len(calls) == 2
    assert audit.await_args.kwargs["error"] == "telegram_partial_delivery"
    assert audit.await_args.args[3]["provider_message_ids"] == [42]
    assert module._chat_locks == {}


async def test_parse_fallback_is_once_and_unrelated_errors_and_timeouts_never_replay():
    module = TelegramModule()
    for failure, expected in [("can't parse entities", 2), ("chat not found", 1)]:
        calls = []

        async def post(url, *, json, calls=calls, failure=failure):
            calls.append(dict(json))
            return httpx.Response(
                400, request=httpx.Request("POST", url), json={"description": failure}
            )

        with (
            patch.object(module, "_get_client", return_value=MagicMock(post=post)),
            patch.object(module, "_base_url", return_value="https://telegram.test"),
            patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await module._send_message("123", "**hello**")
        assert len(calls) == expected
        if expected == 2:
            assert calls[1]["text"] == "**hello**"
            assert "parse_mode" not in calls[1]
    client = MagicMock(post=AsyncMock(side_effect=httpx.ReadTimeout("controlled")))
    with (
        patch.object(module, "_get_client", return_value=client),
        patch.object(module, "_base_url", return_value="https://telegram.test"),
        patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
    ):
        with pytest.raises(httpx.ReadTimeout):
            await module._send_message("123", "hello")
    assert client.post.await_count == 1


async def test_rate_rejections_have_two_retries_and_one_thirty_second_budget():
    module = TelegramModule()
    for delay, expected in [(0, 3), (31, 1), (-1, 1), (True, 1), ("1", 1)]:
        client = MagicMock(
            post=AsyncMock(
                return_value=httpx.Response(
                    429,
                    request=httpx.Request("POST", "https://telegram.test"),
                    json={"parameters": {"retry_after": delay}},
                )
            )
        )
        with (
            patch.object(module, "_get_client", return_value=client),
            patch.object(module, "_base_url", return_value="https://telegram.test"),
            patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await module._send_message("123", "hello")
        assert client.post.await_count == expected


async def test_long_inert_chunks_keep_raw_text_utf16_entities_and_preview_policy():
    module = TelegramModule()
    source = "😀 [link](https://example.test) " * 400
    client = MagicMock(
        post=AsyncMock(
            return_value=httpx.Response(
                200,
                request=httpx.Request("POST", "https://telegram.test"),
                json={"ok": True, "result": {"message_id": 1}},
            )
        )
    )
    with (
        patch.object(module, "_get_client", return_value=client),
        patch.object(module, "_base_url", return_value="https://telegram.test"),
        patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
    ):
        await module._send_message("123", source, render_links_inert=True)
        inert = [call.kwargs["json"] for call in client.post.await_args_list]
        client.post.reset_mock()
        await module._send_message("123", "[doc](https://example.test)")
        ordinary = client.post.await_args.kwargs["json"]
    assert "".join(p["text"] for p in inert) == source
    assert len(inert) > 1
    for payload in inert:
        assert "parse_mode" not in payload
        assert payload["entities"] == [
            {"type": "pre", "offset": 0, "length": len(payload["text"].encode("utf-16-le")) // 2}
        ]
        assert payload["entities"][0]["length"] <= 4000
        assert payload["link_preview_options"] == {"is_disabled": True}
    assert "link_preview_options" not in ordinary
    assert ordinary["text"] == '<a href="https://example.test">doc</a>'


async def test_chat_lock_serializes_chunks_allows_other_chats_and_settles_cancel():
    import asyncio

    module = TelegramModule()
    first = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def post(url, *, json):
        calls.append((json["chat_id"], json["text"][0]))
        if len(calls) == 1:
            first.set()
            await release.wait()
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"ok": True, "result": {"message_id": len(calls)}},
        )

    with (
        patch.object(module, "_get_client", return_value=MagicMock(post=post)),
        patch.object(module, "_base_url", return_value="https://telegram.test"),
        patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
    ):
        one = asyncio.create_task(module._send_message("one", "a" * 5000))
        await first.wait()
        two = asyncio.create_task(module._send_message("one", "b" * 5000))
        await asyncio.sleep(0)  # Let the contender reach the real per-chat lock.
        await module._send_message("two", "c")
        assert calls == [("one", "a"), ("two", "c")]
        two.cancel()
        with pytest.raises(asyncio.CancelledError):
            await two
        release.set()
        await one
        await module._send_message("one", "d")
    assert calls == [("one", "a"), ("two", "c"), ("one", "a"), ("one", "d")]
    assert module._chat_locks == {}


async def test_edit_clamps_source_and_parse_fallback_preserves_rejection_contract():
    from butlers.telegram_text import TRUNCATED, text_units

    module = TelegramModule()
    calls = []

    async def post(url, *, json):
        calls.append(dict(json))
        return httpx.Response(
            400 if len(calls) == 1 else 200,
            request=httpx.Request("POST", url),
            json={"description": "can't parse entities", "ok": True, "result": {"message_id": 1}},
        )

    with (
        patch.object(module, "_get_client", return_value=MagicMock(post=post)),
        patch.object(module, "_base_url", return_value="https://telegram.test"),
    ):
        result = await module._edit_message_text_or_reject("123", 1, "x" * 5000)
    assert result["edited"] is True
    assert len(calls) == 2
    assert all(text_units(p["text"]) <= 4000 and p["text"].endswith(TRUNCATED) for p in calls)
    assert calls[0]["parse_mode"] == "HTML"
    assert "parse_mode" not in calls[1]


@pytest.mark.parametrize("terminal", ["deadline", "cancel"])
async def test_logical_deadline_prevents_later_chunks_and_cancel_keeps_confirmed_prefix(terminal):
    import asyncio
    from types import SimpleNamespace

    from butlers.telegram_api import TelegramPartialDeliveryError

    module = TelegramModule()
    calls = []
    clock = [100.0]

    async def post(url, *, json):
        calls.append(dict(json))
        if terminal == "cancel" and len(calls) == 2:
            raise asyncio.CancelledError()
        clock[0] += 31
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"ok": True, "result": {"message_id": 41}},
        )

    with (
        patch.object(module, "_get_client", return_value=MagicMock(post=post)),
        patch.object(module, "_base_url", return_value="https://telegram.test"),
        patch("butlers.modules.telegram.time", SimpleNamespace(monotonic=lambda: clock[0])),
        patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
    ):
        # Cancellation after a confirmed first chunk differs from queued
        # cancellation: it must carry the actual accepted prefix upstream.
        if terminal == "cancel":
            clock[0] = 100.0
            # Keep time stable for this branch so it reaches the second POST.
            with patch("butlers.modules.telegram.time", SimpleNamespace(monotonic=lambda: 100.0)):
                with pytest.raises(TelegramPartialDeliveryError) as error:
                    await module._send_message("123", "x" * 9000)
        else:
            with pytest.raises(TelegramPartialDeliveryError) as error:
                await module._send_message("123", "x" * 9000)
    assert error.value.message_ids == (41,)
    assert len(calls) == (1 if terminal == "deadline" else 2)
    assert module._chat_locks == {}


@pytest.mark.parametrize(
    "line",
    ["x" * 3996, "x" * 3999, "😀" * 1999 + "x", "x" * 4500],
    ids=["closing-fence", "whole-line", "astral-line", "oversized-line"],
)
async def test_fenced_lines_and_plain_fallback_have_distinct_bounded_requests(line):
    from html.parser import HTMLParser

    from butlers.telegram_text import split_telegram_text, text_units

    class Visible(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    source = "```\n" + line + "```"
    chunks = split_telegram_text(source)
    bodies = []
    for chunk in chunks:
        parsed = Visible()
        parsed.feed(chunk.html)
        bodies.append("".join(parsed.parts))
        assert 0 < text_units(bodies[-1]) <= 4000
    assert "".join(chunk.source for chunk in chunks) == source
    assert "".join(bodies) == line
    if text_units(line) <= 4000:
        assert bodies == [line]  # Fence syntax must not prematurely break the code line.
    else:
        assert [text_units(body) for body in bodies] == [4000, 500]

    module = TelegramModule()
    calls, accepted = [], []
    keyboard = {"inline_keyboard": [[{"text": "Approve", "callback_data": "controlled"}]]}

    async def post(url, *, json):
        calls.append(dict(json))
        if "parse_mode" in json:
            return httpx.Response(
                400,
                request=httpx.Request("POST", url),
                json={"description": "can't parse entities"},
            )
        assert 0 < text_units(json["text"]) <= 4000
        accepted.append(dict(json))
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"ok": True, "result": {"message_id": len(accepted)}},
        )

    with (
        patch.object(module, "_get_client", return_value=MagicMock(post=post)),
        patch.object(module, "_base_url", return_value="https://telegram.test"),
        patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
    ):
        result = await module._send_message(
            "123", source, reply_to_message_id=99, reply_markup=keyboard
        )
    assert "".join(payload["text"] for payload in accepted) == source
    assert len([payload for payload in calls if "parse_mode" in payload]) == len(chunks)
    assert accepted[0]["reply_to_message_id"] == 99
    assert all("reply_to_message_id" not in payload for payload in accepted[1:])
    assert accepted[-1]["reply_markup"] == keyboard
    assert all("reply_markup" not in payload for payload in accepted[:-1])
    assert result["result"]["message_id"] == len(accepted)
    assert result["result"]["message_ids"] == list(range(1, len(accepted) + 1))
    assert module._chat_locks == {}


@pytest.mark.parametrize(
    "source",
    [
        "intro\n```py\n" + "x" * 3999 + "\n```\nend",
        "```\nshort\n" + "😀" * 2000 + "\n```",
        "```\n" + "x" * 4000 + "\r\n```\n" + "tail " * 900,
        "```\n" + "<&>" * 1333 + "x``` ",
        "unclosed ```\n" + "x" * 4500,
        "x" * 3999 + "```\ncode```",
        "```\n" + "x" * 3994 + "```\n" + "prose " * 900,
        " \t\r\n" * 1100,
        "x" * 3999 + "😀",
        "``````",
    ],
    ids=[
        "language",
        "second-astral-line",
        "crlf",
        "entities",
        "unclosed",
        "delimiter-edge",
        "closing-edge",
        "whitespace",
        "scalar-edge",
        "syntax-only",
    ],
)
def test_fenced_source_accounting_balanced_visible_and_inert_boundaries(source):
    from html.parser import HTMLParser

    from butlers.telegram_text import render_markdown, split_telegram_text, text_units

    class Visible(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts, self.stack = [], []

        def handle_starttag(self, tag, attrs):
            assert tag in {"b", "i", "s", "code", "pre", "a"}
            self.stack.append(tag)

        def handle_endtag(self, tag):
            assert self.stack.pop() == tag

        def handle_data(self, data):
            self.parts.append(data)

    chunks = split_telegram_text(source)
    visible = []
    for chunk in chunks:
        parsed = Visible()
        parsed.feed(chunk.html)
        assert not parsed.stack
        body = "".join(parsed.parts)
        assert 0 < text_units(body) <= 4000
        visible.append(body)
    whole = Visible()
    whole.feed(render_markdown(source))
    assert "".join(visible) == ("".join(whole.parts) or source)
    assert "".join(chunk.source for chunk in chunks) == source
    for chunk in chunks:
        fallback = split_telegram_text(chunk.source, inert=True)
        assert all(0 < text_units(part.source) <= 4000 for part in fallback)
        assert "".join(part.source for part in fallback) == chunk.source
    if source.startswith("intro"):
        assert any("x" * 3999 in part for part in visible)
    if source.startswith("```\nshort"):
        assert any("😀" * 2000 in part for part in visible)


@pytest.mark.parametrize("failure", ["timeout", "parse400", "badreceipt", "cancel"])
async def test_bounded_plain_fallback_keeps_actual_prefix_and_never_falls_back_again(failure):
    import asyncio

    from butlers.telegram_api import TelegramPartialDeliveryError
    from butlers.telegram_text import text_units

    module = TelegramModule()
    calls = []

    async def post(url, *, json):
        calls.append(dict(json))
        request = httpx.Request("POST", url)
        if len(calls) == 1:
            assert "parse_mode" in json
            return httpx.Response(
                400, request=request, json={"description": "can't parse entities"}
            )
        assert "parse_mode" not in json
        assert 0 < text_units(json["text"]) <= 4000
        if len(calls) == 2:
            return httpx.Response(
                200, request=request, json={"ok": True, "result": {"message_id": 101}}
            )
        if failure == "timeout":
            raise httpx.ReadTimeout("controlled fallback timeout")
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "parse400":
            return httpx.Response(
                400, request=request, json={"description": "can't parse entities"}
            )
        return httpx.Response(
            200, request=request, json={"ok": True, "result": {"message_id": True}}
        )

    with (
        patch.object(module, "_get_client", return_value=MagicMock(post=post)),
        patch.object(module, "_base_url", return_value="https://telegram.test"),
        patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
    ):
        with pytest.raises(TelegramPartialDeliveryError) as error:
            await module._send_message("123", "```\n" + "x" * 3999 + "```", reply_to_message_id=99)
    assert error.value.message_ids == (101,)
    assert error.value.retryable is False
    assert len(calls) == 3
    assert calls[1]["reply_to_message_id"] == 99
    assert "reply_to_message_id" not in calls[2]
    assert module._chat_locks == {}


async def test_plain_fallback_shares_original_chunk_rate_rejection_budget():
    from butlers.telegram_api import TelegramPartialDeliveryError

    module = TelegramModule()
    calls = []

    async def post(url, *, json):
        calls.append(dict(json))
        request = httpx.Request("POST", url)
        if len(calls) in {1, 4, 5}:
            return httpx.Response(429, request=request, json={"parameters": {"retry_after": 0}})
        if len(calls) == 2:
            return httpx.Response(
                400, request=request, json={"description": "can't parse entities"}
            )
        return httpx.Response(
            200, request=request, json={"ok": True, "result": {"message_id": 101}}
        )

    with (
        patch.object(module, "_get_client", return_value=MagicMock(post=post)),
        patch.object(module, "_base_url", return_value="https://telegram.test"),
        patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
    ):
        with pytest.raises(TelegramPartialDeliveryError) as error:
            await module._send_message("123", "```\n" + "x" * 3999 + "```")
    assert error.value.message_ids == (101,)
    assert len(calls) == 5  # Two total rate retries, including the initial HTML attempt.
    assert "parse_mode" in calls[0] and "parse_mode" in calls[1]
    assert all("parse_mode" not in payload for payload in calls[2:])
    assert module._chat_locks == {}
