"""Tests for notify react intent functionality."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from butlers.core_tools._routing import _extract_delivery_id
from butlers.testing.daemon import write_butler_config
from butlers.tools.switchboard.contracts import parse_notify_request

pytestmark = pytest.mark.unit


def test_confirmed_telegram_reaction_keeps_an_operation_receipt() -> None:
    request_id = "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b"

    assert (
        _extract_delivery_id(
            channel="telegram",
            intent="react",
            adapter_result={"ok": True, "result": True},
            fallback_request_id=request_id,
        )
        == f"telegram-reaction:{request_id}"
    )
    assert (
        _extract_delivery_id(
            channel="telegram",
            intent="react",
            adapter_result={"ok": False, "result": False},
            fallback_request_id=request_id,
        )
        is None
    )


# A runtime passes notify() the request context its session was given. Over MCP the
# schema requires the identity fields, so partial contexts never reach the tool.
_CTX_WITHOUT_THREAD = {
    "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
    "source_channel": "telegram_bot",
    "source_endpoint_identity": "switchboard",
    "source_sender_identity": "123",
}
_CTX = {**_CTX_WITHOUT_THREAD, "source_thread_identity": "123:456"}


def _booted(test: Callable[..., Any]) -> Callable[..., Any]:
    """Run *test* in the integration lane, on the session loop the kit's fixtures use."""
    return pytest.mark.integration(pytest.mark.asyncio(loop_scope="session")(test))


def _ok_client() -> AsyncMock:
    """A Switchboard client whose deliver() succeeds."""
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(
        return_value=MagicMock(
            is_error=False,
            data={"status": "ok"},
            content=[MagicMock(text='{"status":"ok"}')],
        )
    )
    return mock_client


async def _booted_with_switchboard(booted, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Give the booted butler a Switchboard that accepts every delivery."""
    client = _ok_client()
    monkeypatch.setattr(booted.daemon, "switchboard_client", client)
    return client


async def _fresh_butler(booted_daemon, tmp_path: Path):
    """A butler of its own: these tests deliver, so they leave attention-ledger rows."""
    return await booted_daemon(write_butler_config(tmp_path, "general"))


def _notify(booted) -> Callable[..., Any]:
    """notify() as a runtime session calls it: over the daemon's MCP endpoint."""

    async def notify(**arguments: Any) -> dict[str, Any]:
        return await booted.call_tool("notify", arguments)

    return notify


class TestNotifyReactIntent:
    """Test suite for notify react intent."""

    @_booted
    async def test_notify_react_validation_and_delivery(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Validation errors: emoji required, telegram only, request_context, thread identity;
        successful delivery: empty/omitted message, emoji+intent forwarded."""
        booted = await _fresh_butler(booted_daemon, tmp_path)
        notify_fn = _notify(booted)

        # Validation: intent accepted but emoji required
        r_no_emoji = await notify_fn(
            channel="telegram",
            message="",
            intent="react",
            request_context=_CTX,
        )
        assert r_no_emoji["status"] == "error"
        assert "emoji" in r_no_emoji["error"].lower()

        # Validation: non-telegram rejected
        r_email = await notify_fn(
            channel="email",
            message="",
            intent="react",
            emoji="👍",
            request_context=_CTX,
        )
        assert r_email["status"] == "error"
        assert "telegram" in r_email["error"].lower()

        # Validation: missing request_context
        r_no_ctx = await notify_fn(channel="telegram", message="", intent="react", emoji="👍")
        assert r_no_ctx["status"] == "error"
        assert "request_context" in r_no_ctx["error"].lower()

        # Validation: missing source_thread_identity
        r_no_thread = await notify_fn(
            channel="telegram",
            message="",
            intent="react",
            emoji="👍",
            request_context=_CTX_WITHOUT_THREAD,
        )
        assert r_no_thread["status"] == "error"
        assert "source_thread_identity" in r_no_thread["error"].lower()

        # Successful delivery: empty message allowed
        await _booted_with_switchboard(booted, monkeypatch)
        r1 = await notify_fn(
            channel="telegram",
            message="",
            intent="react",
            emoji="👍",
            request_context=_CTX,
        )
        assert r1["status"] == "ok"

        # Successful delivery: omitted message normalized to empty string
        client = await _booted_with_switchboard(booted, monkeypatch)
        r2 = await notify_fn(
            channel="telegram",
            intent="react",
            emoji="✅",
            request_context=_CTX,
        )
        assert r2["status"] == "ok"
        assert client.call_tool.call_args[0][1]["notify_request"]["delivery"]["message"] == ""

        # Successful delivery: emoji and intent forwarded
        client = await _booted_with_switchboard(booted, monkeypatch)
        r3 = await notify_fn(
            channel="telegram",
            message="",
            intent="react",
            emoji="🔥",
            request_context=_CTX,
        )
        assert r3["status"] == "ok"
        nr = client.call_tool.call_args[0][1]["notify_request"]
        assert nr["delivery"]["emoji"] == "🔥"
        assert nr["delivery"]["intent"] == "react"

    @_booted
    async def test_notify_blocked_when_notify_permission_revoked(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Revoked notify permission blocks notify() before reaching the switchboard.

        Mirrors the spawn gate: a granted=false cell denies the notification
        outright (observable error), and the switchboard client is never called.
        Pre-fix this fails: the matrix was ignored, so delivery proceeded.

        [bu-tzlq6]
        """
        from butlers.core.permissions import NOTIFY_PERMISSION
        from butlers.testing.db import open_pool

        booted = await _fresh_butler(booted_daemon, tmp_path)
        client = await _booted_with_switchboard(booted, monkeypatch)
        admin = await open_pool(booted.admin_url)
        try:
            await admin.execute(
                "INSERT INTO public.permissions (butler, permission, granted, reason)"
                " VALUES ($1, $2, false, 'revoked by owner')",
                "general",
                NOTIFY_PERMISSION,
            )
        finally:
            await admin.close()

        result = await _notify(booted)(channel="telegram", message="hello", intent="send")

        assert result["status"] == "error"
        assert "permission denied" in result["error"].lower()
        client.call_tool.assert_not_called()

    @_booted
    async def test_notify_allowed_when_notify_permission_granted(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Granted/default notify permission lets the notification proceed."""
        booted = await _fresh_butler(booted_daemon, tmp_path)
        client = await _booted_with_switchboard(booted, monkeypatch)
        # No permissions row: the default cell is granted.

        result = await _notify(booted)(
            channel="telegram",
            message="",
            intent="react",
            emoji="👍",
            request_context=_CTX,
        )

        assert result["status"] == "ok"
        client.call_tool.assert_called_once()

    @_booted
    async def test_notify_coerces_stringified_request_context(
        self, booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A JSON-string request_context is parsed to a dict and forwarded.

        Non-Claude runtimes sometimes pass request_context as a JSON string.
        Pre-fix this was rejected at the schema boundary (the model could not
        recover and the reply was silently dropped). It must now be coerced and
        delivered.
        """
        import json as _json

        booted = await _fresh_butler(booted_daemon, tmp_path)
        client = await _booted_with_switchboard(booted, monkeypatch)

        ctx = {
            "request_id": "019ef580-de73-7ae4-8c14-c3ccc8a9bf21",
            "source_channel": "telegram_bot",
            "source_endpoint_identity": "switchboard",
            "source_sender_identity": "206570151",
            "source_thread_identity": "206570151:1311",
        }
        result = await _notify(booted)(
            channel="telegram",
            message="The Dr Ng followup was on June 4, not today.",
            intent="reply",
            request_context=_json.dumps(ctx),
        )

        assert result["status"] == "ok"
        forwarded = client.call_tool.call_args[0][1]["notify_request"]["request_context"]
        assert forwarded == ctx

    @_booted
    async def test_notify_rejects_unparseable_request_context_string(
        self, shared_booted_daemon, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-JSON request_context string returns an actionable error, not a drop."""
        booted = await shared_booted_daemon()
        client = await _booted_with_switchboard(booted, monkeypatch)

        result = await _notify(booted)(
            channel="telegram",
            message="hi",
            intent="reply",
            request_context="not-json {",
        )

        assert result["status"] == "error"
        assert "object/dict" in result["error"]
        client.call_tool.assert_not_called()

    @_booted
    async def test_notify_rejects_json_array_request_context(
        self, shared_booted_daemon, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A request_context string that parses to a non-dict (JSON array) errors.

        Guards the `isinstance(parsed, dict)` branch: json.loads succeeds here,
        so without the dict check a list would be forwarded and break downstream
        `.get()` access.
        """
        booted = await shared_booted_daemon()
        client = await _booted_with_switchboard(booted, monkeypatch)

        result = await _notify(booted)(
            channel="telegram",
            message="hi",
            intent="reply",
            request_context="[1, 2]",
        )

        assert result["status"] == "error"
        assert "object/dict" in result["error"]
        client.call_tool.assert_not_called()

    @_booted
    async def test_remind_coerces_stringified_request_context(
        self, booted_daemon, tmp_path: Path
    ) -> None:
        """remind() coerces a JSON-string request_context before embedding it.

        The reminder schedules a prompt that calls notify() with notify_args
        serialized via json.dumps. If the string were not coerced first, the
        embedded request_context would be a double-encoded JSON blob.
        """
        import json as _json

        booted = await _fresh_butler(booted_daemon, tmp_path)

        ctx = {
            "request_id": "019ef580-de73-7ae4-8c14-c3ccc8a9bf21",
            "source_channel": "telegram_bot",
            "source_endpoint_identity": "switchboard",
            "source_sender_identity": "206570151",
        }
        result = await booted.call_tool(
            "remind",
            {
                "message": "Take your meds",
                "channel": "telegram",
                "delay_minutes": 30,
                "request_context": _json.dumps(ctx),
            },
        )

        assert result.get("status") != "error"
        prompt = await booted.daemon.db.pool.fetchval(
            "SELECT prompt FROM scheduled_tasks WHERE id = $1::uuid", result["id"]
        )
        embedded = _json.loads(prompt.split("arguments: ", 1)[1])
        assert embedded["request_context"] == ctx


class TestNotifyReactContract:
    """Test suite for notify.v1 contract validation of react intent."""

    _BASE_CTX = {
        "request_id": "01916b9d-1234-7000-abcd-123456789abc",
        "source_channel": "telegram_bot",
        "source_endpoint_identity": "test",
        "source_sender_identity": "user123",
        "source_thread_identity": "123:456",
    }

    def test_react_contract_validation_and_valid_payload(self) -> None:
        """emoji required; request_context required; source_thread_identity required; valid payload accepted."""
        # Missing emoji
        with pytest.raises(ValidationError) as exc_info:
            parse_notify_request(
                {
                    "schema_version": "notify.v1",
                    "origin_butler": "health",
                    "delivery": {"intent": "react", "channel": "telegram", "message": ""},
                    "request_context": self._BASE_CTX,
                }
            )
        assert "emoji" in str(exc_info.value).lower()

        # Missing request_context
        with pytest.raises(ValidationError) as exc_info:
            parse_notify_request(
                {
                    "schema_version": "notify.v1",
                    "origin_butler": "health",
                    "delivery": {
                        "intent": "react",
                        "channel": "telegram",
                        "message": "",
                        "emoji": "👍",
                    },
                }
            )
        assert "context" in str(exc_info.value).lower()

        # Missing source_thread_identity
        ctx_no_thread = {k: v for k, v in self._BASE_CTX.items() if k != "source_thread_identity"}
        with pytest.raises(ValidationError) as exc_info:
            parse_notify_request(
                {
                    "schema_version": "notify.v1",
                    "origin_butler": "health",
                    "delivery": {
                        "intent": "react",
                        "channel": "telegram",
                        "message": "",
                        "emoji": "👍",
                    },
                    "request_context": ctx_no_thread,
                }
            )
        assert "thread" in str(exc_info.value).lower()

        # Valid payload accepted
        result = parse_notify_request(
            {
                "schema_version": "notify.v1",
                "origin_butler": "health",
                "delivery": {
                    "intent": "react",
                    "channel": "telegram",
                    "message": "",
                    "emoji": "🎉",
                },
                "request_context": self._BASE_CTX,
            }
        )
        assert result.delivery.intent == "react"
        assert result.delivery.emoji == "🎉"
        assert result.request_context.source_thread_identity == "123:456"
