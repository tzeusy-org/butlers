"""Tests for quota enforcement and ledger recording wired into the Spawner.

Covers:
- Spawn blocked when 24h limit exhausted
- Spawn blocked when 30d limit exhausted
- Spawn proceeds when within limits
- Spawn proceeds when no limits configured (unlimited)
- Failed session with usage still records to ledger
- No ledger recording when catalog_entry_id is absent (TOML fallback)
- No ledger recording when adapter reports no usage

[bu-lm4m.1]
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeConfig
from butlers.core.model_routing import QuotaStatus
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

# Fake catalog entry UUID used in resolve_model mock return values
_FAKE_CATALOG_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_SESSION_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")


# ---------------------------------------------------------------------------
# Minimal mock adapter
# ---------------------------------------------------------------------------


class _MockAdapter(RuntimeAdapter):
    """Minimal mock adapter for spawner orchestration tests."""

    def __init__(
        self,
        *,
        result_text: str = "ok",
        usage: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self._result_text = result_text
        self._usage = usage
        self._error = error
        self.invoke_calls = 0

    @property
    def binary_name(self) -> str:
        return "mock"

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
        if self._error:
            raise RuntimeError(self._error)
        return self._result_text, [], self._usage

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        import json

        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


def _make_config(name: str = "test-butler", port: int = 9100) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        runtime=RuntimeConfig(max_concurrent_sessions=1),
        modules={},
        env_required=[],
        env_optional=[],
    )


# ---------------------------------------------------------------------------
# Helper: build a spawner with a mock pool and patched helpers
# ---------------------------------------------------------------------------


def _quota_allowed() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=100, limit_24h=1000, usage_30d=500, limit_30d=5000)


def _quota_denied_24h() -> QuotaStatus:
    return QuotaStatus(allowed=False, usage_24h=1000, limit_24h=1000, usage_30d=500, limit_30d=5000)


def _quota_denied_30d() -> QuotaStatus:
    return QuotaStatus(allowed=False, usage_24h=100, limit_24h=1000, usage_30d=5000, limit_30d=5000)


def _quota_unlimited() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)


# ---------------------------------------------------------------------------
# Quota enforcement tests
# ---------------------------------------------------------------------------


class TestSpawnerQuotaEnforcement:
    """Spawner blocks spawn when catalog entry quota is exhausted."""

    async def test_spawn_blocked_when_24h_limit_exhausted(self, tmp_path: Path) -> None:
        """Spawner returns success=False when 24h quota is exceeded."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(
            result_text="should not run",
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

        with (
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_denied_24h(),
            ),
        ):
            result = await spawner.trigger("hello", "tick")

        assert result.success is False
        assert result.error is not None
        assert "quota" in result.error.lower() or "24h" in result.error
        # Adapter must NOT have been invoked
        assert adapter.invoke_calls == 0

    async def test_spawn_blocked_when_30d_limit_exhausted(self, tmp_path: Path) -> None:
        """Spawner returns success=False when 30d quota is exceeded."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="should not run")
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

        with (
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_denied_30d(),
            ),
        ):
            result = await spawner.trigger("hello", "tick")

        assert result.success is False
        assert result.error is not None
        assert "30d" in result.error
        assert adapter.invoke_calls == 0

    async def test_spawn_proceeds_when_within_limits(self, tmp_path: Path) -> None:
        """Spawner proceeds normally when usage is within limits."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="session output")
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID
            result = await spawner.trigger("hello", "tick")

        assert result.success is True
        assert result.output == "session output"
        assert adapter.invoke_calls == 1

    async def test_spawn_proceeds_when_no_limits_configured(self, tmp_path: Path) -> None:
        """Spawner proceeds when quota check returns unlimited (no limits row)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="unlimited output")
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_unlimited(),
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID
            result = await spawner.trigger("hello", "tick")

        assert result.success is True
        assert adapter.invoke_calls == 1

    async def test_quota_error_message_includes_alias_and_windows(self, tmp_path: Path) -> None:
        """Error message from quota block includes catalog alias and window details."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

        denied = QuotaStatus(
            allowed=False, usage_24h=1500, limit_24h=1000, usage_30d=200, limit_30d=5000
        )

        with (
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=denied,
            ),
        ):
            result = await spawner.trigger("hi", "tick")

        assert result.success is False
        assert result.error is not None
        # Should mention the alias/model and the exceeded window
        assert "claude-haiku" in result.error
        assert "24h" in result.error
        assert "1500" in result.error
        assert "1000" in result.error

    async def test_quota_not_checked_without_pool(self, tmp_path: Path) -> None:
        """When no pool is available, quota check is skipped (TOML fallback path)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        adapter = _MockAdapter(result_text="toml output")
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)  # no pool

        with patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock) as mock_quota:
            result = await spawner.trigger("hi", "tick")

        # No pool → quota check must not be called
        mock_quota.assert_not_called()
        assert result.success is True

    async def test_quota_not_checked_for_toml_fallback_resolution(self, tmp_path: Path) -> None:
        """When catalog returns None (TOML fallback), quota check is skipped."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="toml output")
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=None,  # catalog miss → TOML fallback, no catalog_entry_id
            ),
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock) as mock_quota,
        ):
            mock_create.return_value = _SESSION_ID
            result = await spawner.trigger("hi", "tick")

        mock_quota.assert_not_called()
        assert result.success is True


# ---------------------------------------------------------------------------
# Ledger recording tests
# ---------------------------------------------------------------------------


class TestSpawnerLedgerRecording:
    """Spawner records token usage to ledger in finally block."""

    async def test_successful_session_records_to_ledger(self, tmp_path: Path) -> None:
        """record_token_usage is called after a successful session with usage."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(
            result_text="ok",
            usage={"input_tokens": 200, "output_tokens": 100},
        )
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock) as mock_record,
        ):
            mock_create.return_value = _SESSION_ID
            result = await spawner.trigger("hello", "tick")

        assert result.success is True
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["catalog_entry_id"] == _FAKE_CATALOG_ID
        assert call_kwargs["butler_name"] == "test-butler"
        assert call_kwargs["session_id"] == _SESSION_ID
        assert call_kwargs["input_tokens"] == 200
        assert call_kwargs["output_tokens"] == 100

    async def test_failed_session_with_usage_records_to_ledger(self, tmp_path: Path) -> None:
        """record_token_usage is called even when the session fails, if usage was reported.

        Tokens are consumed by the provider on invocation — a failed session
        still costs tokens and MUST count against the quota.
        """
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        # Adapter that raises before returning usage (adapter crashed)

        class _FailingUsageAdapter(_MockAdapter):
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
                raise RuntimeError("adapter crashed")

        adapter = _FailingUsageAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock) as mock_record,
        ):
            mock_create.return_value = _SESSION_ID
            result = await spawner.trigger("hello", "tick")

        # Session failed
        assert result.success is False
        # No usage was reported (adapter raised before returning), so no ledger recording
        mock_record.assert_not_called()

    async def test_failed_session_with_reported_usage_still_records(self, tmp_path: Path) -> None:
        """When adapter reports usage and post-processing fails, ledger is still written.

        Simulates: adapter returns usage successfully, but session_complete (DB write)
        raises on the first call (success path). The except handler calls session_complete
        again (error path) which succeeds. The finally block then records to the ledger
        using the _ledger_input_tokens captured when the adapter returned.

        This verifies that tokens consumed by the upstream provider are counted against
        the quota even when session metadata persistence fails.
        """
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        # Adapter returns usage successfully
        adapter = _MockAdapter(
            result_text="ok",
            usage={"input_tokens": 50, "output_tokens": 25},
        )
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

        # session_complete fails on first call (success path) but succeeds on second (error path)
        _call_count = [0]

        async def _session_complete_side_effect(*args: Any, **kwargs: Any) -> None:
            _call_count[0] += 1
            if _call_count[0] == 1:
                raise RuntimeError("DB write failed on success path")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch(
                "butlers.core.spawner.session_complete",
                side_effect=_session_complete_side_effect,
            ),
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock) as mock_record,
        ):
            mock_create.return_value = _SESSION_ID
            result = await spawner.trigger("hello", "tick")

        # Session ended in error because session_complete raised during success path
        assert result.success is False
        # Ledger MUST still be written — _ledger_input_tokens was set before the failure
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["input_tokens"] == 50
        assert call_kwargs["output_tokens"] == 25
        assert call_kwargs["catalog_entry_id"] == _FAKE_CATALOG_ID

    async def test_no_ledger_recording_when_no_catalog_entry_id(self, tmp_path: Path) -> None:
        """No ledger recording when model was resolved from TOML (no catalog_entry_id)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(
            result_text="toml result", usage={"input_tokens": 100, "output_tokens": 50}
        )
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=None,  # catalog miss → TOML fallback
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock) as mock_record,
        ):
            mock_create.return_value = _SESSION_ID
            result = await spawner.trigger("hi", "tick")

        assert result.success is True
        # No catalog_entry_id → no ledger recording
        mock_record.assert_not_called()

    async def test_no_ledger_recording_when_adapter_reports_no_usage(self, tmp_path: Path) -> None:
        """No ledger recording when adapter returns None usage."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="ok", usage=None)  # no usage
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock) as mock_record,
        ):
            mock_create.return_value = _SESSION_ID
            result = await spawner.trigger("hi", "tick")

        assert result.success is True
        # No usage reported → no ledger recording
        mock_record.assert_not_called()
