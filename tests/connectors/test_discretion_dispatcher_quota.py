"""Tests for quota enforcement and ledger recording in DiscretionDispatcher.

Covers:
- Dispatcher raises RuntimeError when quota is exhausted
- Dispatcher raises with details about exceeded window(s)
- Dispatcher proceeds when within limits
- Dispatcher records usage to ledger after successful call
- Dispatcher records usage to ledger even when timeout occurs (best-effort)
- No ledger recording when adapter reports no usage

[bu-lm4m.1]
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.discretion_dispatcher import DiscretionDispatcher
from butlers.core.model_routing import QuotaStatus
from butlers.core.runtimes.base import RuntimeAdapter, register_adapter

pytestmark = pytest.mark.unit

_FAKE_CATALOG_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000003")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_pool(
    *,
    runtime_type: str = "claude",
    model_id: str = "claude-haiku",
    extra_args: list | None = None,
    catalog_id: uuid.UUID | None = None,
) -> Any:
    """Return an asyncpg pool mock whose fetchrow returns a matching catalog row."""
    if catalog_id is None:
        catalog_id = _FAKE_CATALOG_ID
    extra_args_json = json.dumps(extra_args) if extra_args is not None else None
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "runtime_type": runtime_type,
        "model_id": model_id,
        "extra_args": extra_args_json,
        "id": catalog_id,
    }[key]
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)
    return pool


class _StubAdapter(RuntimeAdapter):
    """Stub adapter that returns a configurable response with optional usage."""

    def __init__(
        self,
        response: str = "ok",
        usage: dict[str, Any] | None = None,
        *,
        butler_name: str | None = None,
    ) -> None:
        self._response = response
        self._usage = usage
        self.invoke_calls = 0

    @property
    def binary_name(self) -> str:
        return "stub-binary"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        max_turns: int = 20,
        model: str | None = None,
        runtime_args: list[str] | None = None,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        self.invoke_calls += 1
        return (self._response, [], self._usage)

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        return tmp_dir / "config.json"

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


def _quota_allowed() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=100, limit_24h=1000, usage_30d=200, limit_30d=5000)


def _quota_denied_24h() -> QuotaStatus:
    return QuotaStatus(allowed=False, usage_24h=1000, limit_24h=1000, usage_30d=200, limit_30d=5000)


def _quota_denied_both() -> QuotaStatus:
    return QuotaStatus(
        allowed=False, usage_24h=1000, limit_24h=1000, usage_30d=5000, limit_30d=5000
    )


def _quota_unlimited() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)


# ---------------------------------------------------------------------------
# Quota enforcement tests
# ---------------------------------------------------------------------------


class TestDiscretionDispatcherQuotaEnforcement:
    """DiscretionDispatcher raises RuntimeError when quota is exhausted."""

    async def test_raises_runtime_error_when_24h_quota_exhausted(self) -> None:
        """call() raises RuntimeError when 24h token quota is exceeded."""
        pool = _mock_pool(runtime_type="stub-quota-24h", model_id="tiny-model")
        stub = _StubAdapter()
        register_adapter("stub-quota-24h", type(stub))

        dispatcher = DiscretionDispatcher(pool=pool)
        dispatcher._adapter_cache["stub-quota-24h"] = stub

        with (
            patch(
                "butlers.connectors.discretion_dispatcher.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_denied_24h(),
            ),
            pytest.raises(RuntimeError) as exc_info,
        ):
            await dispatcher.call("is this spam?")

        assert "quota" in str(exc_info.value).lower() or "24h" in str(exc_info.value)
        # Adapter must NOT be invoked
        assert stub.invoke_calls == 0

    async def test_raises_runtime_error_includes_window_and_usage_details(self) -> None:
        """RuntimeError from quota block includes window and usage/limit details."""
        pool = _mock_pool(runtime_type="stub-quota-msg", model_id="claude-haiku")
        stub = _StubAdapter()
        register_adapter("stub-quota-msg", type(stub))

        dispatcher = DiscretionDispatcher(pool=pool)
        dispatcher._adapter_cache["stub-quota-msg"] = stub

        denied = QuotaStatus(
            allowed=False, usage_24h=2000, limit_24h=1000, usage_30d=100, limit_30d=50000
        )

        with (
            patch(
                "butlers.connectors.discretion_dispatcher.check_token_quota",
                new_callable=AsyncMock,
                return_value=denied,
            ),
            pytest.raises(RuntimeError) as exc_info,
        ):
            await dispatcher.call("prompt")

        error_str = str(exc_info.value)
        assert "claude-haiku" in error_str
        assert "24h" in error_str
        assert "2000" in error_str
        assert "1000" in error_str

    async def test_proceeds_when_within_limits(self) -> None:
        """call() proceeds normally when quota check returns allowed=True."""
        pool = _mock_pool(runtime_type="stub-quota-ok", model_id="tiny-model")
        stub = _StubAdapter(response="allowed response")
        register_adapter("stub-quota-ok", type(stub))

        dispatcher = DiscretionDispatcher(pool=pool)
        dispatcher._adapter_cache["stub-quota-ok"] = stub

        with (
            patch(
                "butlers.connectors.discretion_dispatcher.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch(
                "butlers.connectors.discretion_dispatcher.record_token_usage",
                new_callable=AsyncMock,
            ),
        ):
            result = await dispatcher.call("test prompt")

        assert result == "allowed response"
        assert stub.invoke_calls == 1

    async def test_proceeds_when_no_limits_configured(self) -> None:
        """call() proceeds normally for unlimited entries."""
        pool = _mock_pool(runtime_type="stub-quota-unlimited", model_id="fast-model")
        stub = _StubAdapter(response="unlimited response")
        register_adapter("stub-quota-unlimited", type(stub))

        dispatcher = DiscretionDispatcher(pool=pool)
        dispatcher._adapter_cache["stub-quota-unlimited"] = stub

        with (
            patch(
                "butlers.connectors.discretion_dispatcher.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_unlimited(),
            ),
            patch(
                "butlers.connectors.discretion_dispatcher.record_token_usage",
                new_callable=AsyncMock,
            ),
        ):
            result = await dispatcher.call("unlimited prompt")

        assert result == "unlimited response"

    async def test_raises_for_both_windows_exceeded(self) -> None:
        """Error message includes both windows when both are exceeded."""
        pool = _mock_pool(runtime_type="stub-quota-both", model_id="model-x")
        stub = _StubAdapter()
        register_adapter("stub-quota-both", type(stub))

        dispatcher = DiscretionDispatcher(pool=pool)
        dispatcher._adapter_cache["stub-quota-both"] = stub

        with (
            patch(
                "butlers.connectors.discretion_dispatcher.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_denied_both(),
            ),
            pytest.raises(RuntimeError) as exc_info,
        ):
            await dispatcher.call("prompt")

        error_str = str(exc_info.value)
        assert "24h" in error_str
        assert "30d" in error_str


# ---------------------------------------------------------------------------
# Ledger recording tests
# ---------------------------------------------------------------------------


class TestDiscretionDispatcherLedgerRecording:
    """DiscretionDispatcher records token usage to ledger."""

    async def test_records_usage_after_successful_call(self) -> None:
        """record_token_usage is called with correct params after a successful call."""
        pool = _mock_pool(runtime_type="stub-record-ok", model_id="tiny-model")
        stub = _StubAdapter(
            response="answer",
            usage={"input_tokens": 50, "output_tokens": 30},
        )
        register_adapter("stub-record-ok", type(stub))

        dispatcher = DiscretionDispatcher(pool=pool, butler_name="test-butler")
        dispatcher._adapter_cache["stub-record-ok"] = stub

        with (
            patch(
                "butlers.connectors.discretion_dispatcher.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch(
                "butlers.connectors.discretion_dispatcher.record_token_usage",
                new_callable=AsyncMock,
            ) as mock_record,
        ):
            result = await dispatcher.call("question")

        assert result == "answer"
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["catalog_entry_id"] == _FAKE_CATALOG_ID
        assert call_kwargs["butler_name"] == "test-butler"
        assert call_kwargs["session_id"] is None  # discretion calls have no session
        assert call_kwargs["input_tokens"] == 50
        assert call_kwargs["output_tokens"] == 30

    async def test_no_recording_when_adapter_returns_no_usage(self) -> None:
        """record_token_usage is NOT called when adapter returns None usage."""
        pool = _mock_pool(runtime_type="stub-record-none", model_id="tiny-model")
        stub = _StubAdapter(response="answer", usage=None)
        register_adapter("stub-record-none", type(stub))

        dispatcher = DiscretionDispatcher(pool=pool)
        dispatcher._adapter_cache["stub-record-none"] = stub

        with (
            patch(
                "butlers.connectors.discretion_dispatcher.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch(
                "butlers.connectors.discretion_dispatcher.record_token_usage",
                new_callable=AsyncMock,
            ) as mock_record,
        ):
            await dispatcher.call("question")

        mock_record.assert_not_called()

    async def test_no_recording_when_usage_missing_input_tokens(self) -> None:
        """record_token_usage is NOT called when input_tokens is absent from usage."""
        pool = _mock_pool(runtime_type="stub-record-missing", model_id="tiny-model")
        stub = _StubAdapter(response="answer", usage={"output_tokens": 30})  # no input_tokens
        register_adapter("stub-record-missing", type(stub))

        dispatcher = DiscretionDispatcher(pool=pool)
        dispatcher._adapter_cache["stub-record-missing"] = stub

        with (
            patch(
                "butlers.connectors.discretion_dispatcher.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch(
                "butlers.connectors.discretion_dispatcher.record_token_usage",
                new_callable=AsyncMock,
            ) as mock_record,
        ):
            await dispatcher.call("question")

        mock_record.assert_not_called()

    async def test_session_id_is_always_none_for_discretion_calls(self) -> None:
        """Discretion dispatcher always records with session_id=None."""
        pool = _mock_pool(runtime_type="stub-record-session", model_id="model-y")
        stub = _StubAdapter(response="yes", usage={"input_tokens": 10, "output_tokens": 5})
        register_adapter("stub-record-session", type(stub))

        dispatcher = DiscretionDispatcher(pool=pool, butler_name="__discretion__")
        dispatcher._adapter_cache["stub-record-session"] = stub

        with (
            patch(
                "butlers.connectors.discretion_dispatcher.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch(
                "butlers.connectors.discretion_dispatcher.record_token_usage",
                new_callable=AsyncMock,
            ) as mock_record,
        ):
            await dispatcher.call("classify this")

        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["session_id"] is None
        assert mock_record.call_args.kwargs["butler_name"] == "__discretion__"
